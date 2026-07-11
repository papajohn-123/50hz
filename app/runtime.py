import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI

from app.config import get_settings
from app.db import dispose_engine, get_session_factory
from app.persistence import PostgresAdvisoryLockProvider, PostgresIngestionRepository
from app.sources import AsyncJSONClient
from app.worker.production import build_production_schedules
from app.worker.scheduler import IngestionWorker


@dataclass(slots=True)
class WorkerRuntime:
    stop_event: asyncio.Event
    task: asyncio.Task[None]
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
        session_factory = get_session_factory()
        worker = IngestionWorker(
            schedules=build_production_schedules(
                elexon_client=elexon_client,
                carbon_client=carbon_client,
            ),
            repository=PostgresIngestionRepository(session_factory),
            locks=PostgresAdvisoryLockProvider(session_factory),
        )
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            worker.run_forever(
                stop_event,
                tick_interval=timedelta(seconds=settings.worker_poll_seconds),
            ),
            name="50hz-ingestion-worker",
        )
        runtime = WorkerRuntime(
            stop_event=stop_event,
            task=task,
            clients=(elexon_client, carbon_client),
            started_at=datetime.now(UTC),
        )
        app.state.worker_runtime = runtime
    try:
        yield
    finally:
        if runtime:
            runtime.stop_event.set()
            await runtime.task
            for client in runtime.clients:
                await client.aclose()
        if settings.database_url:
            await dispose_engine()
