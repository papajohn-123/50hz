from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

import app.runtime as runtime_module
from app.config import Settings
from app.db.models import (
    B1610SettledEnergyRevision,
    CarbonObservation,
    DemandObservation,
    ForecastObservation,
    FrequencyObservation,
    GenerationObservation,
    InterconnectorObservation,
    PhysicalNotificationSegmentCurrent,
    ReportedNotice,
)
from app.persistence.retention import RawPayloadRetentionRepository
from app.worker.retention import RawPayloadRetentionWorker


NOW = datetime(2026, 7, 11, 12, tzinfo=UTC)


class ScalarResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def scalars(self) -> ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self.values


class FakeSession:
    def __init__(self, responses: list[ScalarResult | Exception]) -> None:
        self.responses = list(responses)
        self.statements: list[Any] = []
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement: Any) -> ScalarResult:
        self.statements.append(statement)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def test_retention_config_defaults_exceed_reconciliation_window() -> None:
    settings = Settings(_env_file=None)
    assert settings.raw_payload_retention_hours == 72
    assert settings.raw_payload_cleanup_interval_seconds == 3_600

    with pytest.raises(ValidationError):
        Settings(_env_file=None, raw_payload_retention_hours=48)


def test_repository_deletes_only_a_locked_expired_raw_payload_batch() -> None:
    identifiers = [uuid.uuid4(), uuid.uuid4()]
    session = FakeSession(
        [ScalarResult(identifiers), ScalarResult(identifiers)]
    )
    repository = RawPayloadRetentionRepository(lambda: session)
    cutoff = NOW - timedelta(hours=72)

    deleted = asyncio.run(repository.delete_batch(before=cutoff, limit=2))

    assert deleted == 2
    assert session.committed is True
    assert session.rolled_back is False
    selection, deletion = session.statements
    assert selection._limit_clause.value == 2
    assert selection._for_update_arg.skip_locked is True
    assert cutoff in selection.compile().params.values()
    assert deletion.table.name == "raw_payloads"


def test_repository_rolls_back_a_failed_cleanup_batch() -> None:
    session = FakeSession(
        [ScalarResult([uuid.uuid4()]), RuntimeError("database write failed")]
    )
    repository = RawPayloadRetentionRepository(lambda: session)

    with pytest.raises(RuntimeError, match="database write failed"):
        asyncio.run(
            repository.delete_batch(
                before=NOW - timedelta(hours=72),
                limit=1,
            )
        )
    assert session.rolled_back is True
    assert session.committed is False


def test_normalized_evidence_foreign_keys_are_set_null_on_raw_deletion() -> None:
    models = (
        GenerationObservation,
        DemandObservation,
        FrequencyObservation,
        InterconnectorObservation,
        CarbonObservation,
        ForecastObservation,
        ReportedNotice,
        PhysicalNotificationSegmentCurrent,
        B1610SettledEnergyRevision,
    )
    for model in models:
        foreign_key = next(iter(model.__table__.c.raw_payload_id.foreign_keys))
        assert foreign_key.ondelete == "SET NULL"


class CountRepository:
    def __init__(
        self,
        counts: list[int | Exception],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self.counts = list(counts)
        self.calls: list[tuple[datetime, int]] = []
        self.stop_event = stop_event

    async def delete_batch(self, *, before: datetime, limit: int) -> int:
        self.calls.append((before, limit))
        result = self.counts.pop(0)
        if isinstance(result, Exception):
            raise result
        if self.stop_event is not None:
            self.stop_event.set()
        return result


def test_retention_run_is_time_and_batch_bounded() -> None:
    repository = CountRepository([2, 2, 1])
    worker = RawPayloadRetentionWorker(
        repository,
        retention=timedelta(hours=72),
        interval=timedelta(hours=1),
        batch_size=2,
        max_batches=5,
        clock=lambda: NOW,
    )

    outcome = asyncio.run(worker.run_once())

    assert outcome.cutoff == NOW - timedelta(hours=72)
    assert outcome.deleted_rows == 5
    assert outcome.batch_attempts == 3
    assert outcome.hit_batch_limit is False
    assert repository.calls == [(outcome.cutoff, 2)] * 3


def test_retention_stops_at_its_per_run_batch_limit() -> None:
    repository = CountRepository([2, 2, 2])
    worker = RawPayloadRetentionWorker(
        repository,
        retention=timedelta(hours=72),
        interval=timedelta(hours=1),
        batch_size=2,
        max_batches=2,
        clock=lambda: NOW,
    )

    outcome = asyncio.run(worker.run_once())

    assert outcome.deleted_rows == 4
    assert outcome.hit_batch_limit is True
    assert len(repository.calls) == 2


def test_cleanup_failure_is_logged_and_retried_without_crashing(caplog) -> None:
    stop_event = asyncio.Event()
    repository = CountRepository(
        [RuntimeError("temporary database failure"), 0],
        stop_event=stop_event,
    )
    worker = RawPayloadRetentionWorker(
        repository,
        retention=timedelta(hours=72),
        interval=timedelta(milliseconds=1),
        batch_size=1,
        max_batches=1,
        clock=lambda: NOW,
    )

    with caplog.at_level(logging.ERROR):
        asyncio.run(worker.run_forever(stop_event))

    assert len(repository.calls) == 2
    assert "cleanup failed; retrying" in caplog.text


@pytest.mark.asyncio
async def test_runtime_starts_retention_as_a_separate_worker_only_task(
    monkeypatch,
) -> None:
    ingestion_started = asyncio.Event()
    retention_started = asyncio.Event()
    clients: list[Any] = []
    captured: dict[str, Any] = {}
    ingestion_kwargs: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **_: Any) -> None:
            self.closed = False
            clients.append(self)

        async def aclose(self) -> None:
            self.closed = True

    class FakeIngestionWorker:
        def __init__(self, **values: Any) -> None:
            ingestion_kwargs.update(values)

        async def run_forever(self, stop_event, *, tick_interval) -> None:
            ingestion_started.set()
            await stop_event.wait()

    class FakeRetentionWorker:
        def __init__(self, repository, *, retention, interval) -> None:
            captured.update(
                repository=repository,
                retention=retention,
                interval=interval,
            )

        async def run_forever(self, stop_event) -> None:
            retention_started.set()
            await stop_event.wait()

    session_factory = object()
    monkeypatch.setattr(
        runtime_module,
        "get_settings",
        lambda: SimpleNamespace(
            service_role="worker",
            database_url="postgresql://configured",
            elexon_base_url="https://elexon.test",
            carbon_intensity_base_url="https://carbon.test",
            ukpn_base_url="https://ukpn.test",
            ukpn_api_key=None,
            worker_poll_seconds=60,
            raw_payload_retention_hours=72,
            raw_payload_cleanup_interval_seconds=3_600,
        ),
    )
    monkeypatch.setattr(runtime_module, "AsyncJSONClient", FakeClient)
    monkeypatch.setattr(runtime_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(runtime_module, "IngestionWorker", FakeIngestionWorker)
    monkeypatch.setattr(runtime_module, "RawPayloadRetentionWorker", FakeRetentionWorker)
    monkeypatch.setattr(runtime_module, "PostgresIngestionRepository", lambda value: value)
    monkeypatch.setattr(runtime_module, "PostgresAdvisoryLockProvider", lambda value: value)
    monkeypatch.setattr(runtime_module, "RawPayloadRetentionRepository", lambda value: value)
    monkeypatch.setattr(runtime_module, "build_production_schedules", lambda **_: ())
    monkeypatch.setattr(runtime_module, "dispose_engine", AsyncMock())

    app = FastAPI()
    async with runtime_module.lifespan(app):
        await asyncio.wait_for(ingestion_started.wait(), timeout=1)
        await asyncio.wait_for(retention_started.wait(), timeout=1)
        assert app.state.worker_runtime.task.done() is False
        assert app.state.worker_runtime.retention_task.done() is False
        assert captured == {
            "repository": session_factory,
            "retention": timedelta(hours=72),
            "interval": timedelta(hours=1),
        }
        assert len(ingestion_kwargs["post_success_actions"]) == 1
        assert (
            type(ingestion_kwargs["post_success_actions"][0]).__name__
            == "ObservedEventMaintenanceAction"
        )

    assert all(client.closed for client in clients)
    runtime_module.dispose_engine.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_runtime_does_not_start_retention(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_module,
        "get_settings",
        lambda: SimpleNamespace(service_role="api", database_url=None),
    )
    app = FastAPI()
    async with runtime_module.lifespan(app):
        assert not hasattr(app.state, "worker_runtime")
