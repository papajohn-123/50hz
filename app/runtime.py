import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from app.config import get_settings
from app.db import dispose_engine, get_session_factory
from app.persistence import PostgresAdvisoryLockProvider, PostgresIngestionRepository
from app.persistence.observed_events import (
    PostgresObservedEventRepository,
    PostgresObservedEvidenceLoader,
)
from app.persistence.retention import RawPayloadRetentionRepository
from app.sources import AsyncJSONClient
from app.sources.ukpn import ukpn_authorization_headers
from app.worker.production import build_production_schedules
from app.worker.observed_events import ObservedEventMaintenanceAction
from app.worker.retention import RawPayloadRetentionWorker
from app.worker.scheduler import IngestionWorker


@dataclass(slots=True)
class WorkerRuntime:
    stop_event: asyncio.Event
    task: asyncio.Task[None]
    retention_task: asyncio.Task[None]
    clients: tuple[AsyncJSONClient, ...]
    started_at: datetime


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    runtime: WorkerRuntime | None = None
    if settings.service_role == "worker":
        if not settings.database_url:
            raise RuntimeError("The worker requires DATABASE_URL")
        elexon_client = AsyncJSONClient(
            base_url=settings.elexon_base_url.rstrip("/") + "/"
        )
        carbon_client = AsyncJSONClient(
            base_url=settings.carbon_intensity_base_url.rstrip("/") + "/"
        )
        ukpn_client = AsyncJSONClient(
            base_url=settings.ukpn_base_url.rstrip("/") + "/",
            headers=ukpn_authorization_headers(settings.ukpn_api_key),
        )
        session_factory = get_session_factory()
        locks = PostgresAdvisoryLockProvider(session_factory)
        observed_event_action = ObservedEventMaintenanceAction(
            loader=PostgresObservedEvidenceLoader(session_factory),
            store=PostgresObservedEventRepository(session_factory),
            locks=locks,
        )
        worker = IngestionWorker(
            schedules=build_production_schedules(
                elexon_client=elexon_client,
                carbon_client=carbon_client,
                ukpn_client=ukpn_client,
            ),
            repository=PostgresIngestionRepository(session_factory),
            locks=locks,
            post_success_actions=(observed_event_action,),
        )
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            worker.run_forever(
                stop_event,
                tick_interval=timedelta(seconds=settings.worker_poll_seconds),
            ),
            name="50hz-ingestion-worker",
        )
        retention_worker = RawPayloadRetentionWorker(
            RawPayloadRetentionRepository(session_factory),
            retention=timedelta(hours=settings.raw_payload_retention_hours),
            interval=timedelta(
                seconds=settings.raw_payload_cleanup_interval_seconds
            ),
        )
        retention_task = asyncio.create_task(
            retention_worker.run_forever(stop_event),
            name="50hz-raw-payload-retention",
        )
        runtime = WorkerRuntime(
            stop_event=stop_event,
            task=task,
            retention_task=retention_task,
            clients=(elexon_client, carbon_client, ukpn_client),
            started_at=datetime.now(UTC),
        )
        app.state.worker_runtime = runtime
    try:
        yield
    finally:
        if runtime:
            runtime.stop_event.set()
            await asyncio.gather(runtime.task, runtime.retention_task)
            for client in runtime.clients:
                await client.aclose()
        if settings.database_url:
            await dispose_engine()
