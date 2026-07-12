from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from app.sources.types import AdapterResult, ObservationWindow
from app.worker.contracts import IngestionCheckpoint, PersistOutcome
from app.worker.scheduler import (
    IngestionWorker,
    PollSchedule,
    RunMode,
    RunStatus,
    WindowPlanner,
)


NOW = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)


class FakeAdapter:
    source_id = "test.source"
    dataset = "TEST"
    endpoint = "test"

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.windows: list[ObservationWindow] = []

    async def fetch(self, window: ObservationWindow) -> AdapterResult[Any]:
        self.windows.append(window)
        if self.error:
            raise self.error
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=NOW,
            request_url="https://example.test/data",
            records=(),
            raw_payload={"data": []},
            raw_body=b'{"data":[]}',
            checksum_sha256="0" * 64,
        )


class FakeRepository:
    def __init__(self) -> None:
        self.checkpoints: dict[str, IngestionCheckpoint] = {}
        self.successes: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []

    async def get_checkpoint(self, job_id: str) -> IngestionCheckpoint | None:
        return self.checkpoints.get(job_id)

    async def persist_success(self, **values: Any) -> PersistOutcome:
        self.successes.append(values)
        result = values["result"]
        self.checkpoints[values["job_id"]] = IngestionCheckpoint(
            job_id=values["job_id"],
            last_attempted_at=values["attempted_at"],
            last_succeeded_at=values["completed_at"],
            window_end=result.window.end,
        )
        return PersistOutcome(unchanged=len(result.records))

    async def record_failure(self, **values: Any) -> None:
        self.failures.append(values)
        self.checkpoints[values["job_id"]] = IngestionCheckpoint(
            job_id=values["job_id"],
            last_attempted_at=values["attempted_at"],
            last_succeeded_at=None,
            window_end=None,
        )


class FakeLocks:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.names: list[str] = []

    @asynccontextmanager
    async def acquire(self, lock_name: str):
        self.names.append(lock_name)
        yield self.acquired


def schedule(adapter: FakeAdapter, *, reconcile: bool = False) -> PollSchedule:
    return PollSchedule(
        job_id="test.job",
        adapter=adapter,
        cadence=timedelta(minutes=2),
        overlap=timedelta(minutes=10),
        initial_lookback=timedelta(minutes=30),
        reconcile_every=timedelta(hours=1) if reconcile else None,
    )


def test_incremental_window_overlaps_checkpoint_and_caps_catchup() -> None:
    adapter = FakeAdapter()
    policy = schedule(adapter)
    checkpoint = IngestionCheckpoint(
        job_id="test.job",
        last_attempted_at=NOW - timedelta(minutes=2),
        last_succeeded_at=NOW - timedelta(minutes=2),
        window_end=NOW - timedelta(minutes=2),
    )
    window = WindowPlanner.incremental(policy, checkpoint, now=NOW)
    assert window.start == NOW - timedelta(minutes=12)
    assert window.end == NOW

    stale = IngestionCheckpoint(
        job_id="test.job",
        last_attempted_at=NOW - timedelta(days=4),
        last_succeeded_at=NOW - timedelta(days=4),
        window_end=NOW - timedelta(days=4),
    )
    capped = WindowPlanner.incremental(policy, stale, now=NOW)
    assert capped.start == NOW - timedelta(hours=48)


def test_worker_fetches_and_persists_due_incremental_job() -> None:
    adapter = FakeAdapter()
    repository = FakeRepository()
    locks = FakeLocks()
    worker = IngestionWorker(
        schedules=[schedule(adapter)],
        repository=repository,
        locks=locks,
        clock=lambda: NOW,
    )

    outcomes = asyncio.run(worker.run_once(now=NOW))

    assert len(outcomes) == 1
    assert outcomes[0].status is RunStatus.SUCCEEDED
    assert outcomes[0].mode is RunMode.INCREMENTAL
    assert adapter.windows[0].start == NOW - timedelta(minutes=30)
    assert len(repository.successes) == 1
    assert locks.names == ["50hz:ingest:test.source"]


def test_worker_skips_when_advisory_lock_is_held() -> None:
    adapter = FakeAdapter()
    repository = FakeRepository()
    worker = IngestionWorker(
        schedules=[schedule(adapter)],
        repository=repository,
        locks=FakeLocks(acquired=False),
        clock=lambda: NOW,
    )

    outcome = asyncio.run(worker.run_once(now=NOW))[0]
    assert outcome.status is RunStatus.SKIPPED_LOCKED
    assert adapter.windows == []
    assert repository.successes == []


def test_worker_records_source_failure_without_stopping_other_ticks() -> None:
    adapter = FakeAdapter(error=RuntimeError("upstream unavailable"))
    repository = FakeRepository()
    worker = IngestionWorker(
        schedules=[schedule(adapter)],
        repository=repository,
        locks=FakeLocks(),
        clock=lambda: NOW,
    )

    outcome = asyncio.run(worker.run_once(now=NOW))[0]
    assert outcome.status is RunStatus.FAILED
    assert outcome.error_type == "RuntimeError"
    assert repository.failures[0]["error_message"] == "upstream unavailable"


def test_post_success_action_failure_does_not_change_committed_source_outcome(
    caplog,
) -> None:
    class BrokenAction:
        async def after_success(self, context) -> None:
            assert context.job_id == "test.job"
            raise RuntimeError("derived maintenance unavailable")

    adapter = FakeAdapter()
    repository = FakeRepository()
    worker = IngestionWorker(
        schedules=[schedule(adapter)],
        repository=repository,
        locks=FakeLocks(),
        post_success_actions=(BrokenAction(),),
        clock=lambda: NOW,
    )

    with caplog.at_level("ERROR"):
        outcome = asyncio.run(worker.run_once(now=NOW))[0]

    assert outcome.status is RunStatus.SUCCEEDED
    assert len(repository.successes) == 1
    assert repository.failures == []
    assert "source commit remains successful" in caplog.text


def test_forever_loop_recovers_when_a_tick_crashes(caplog) -> None:
    stop_event = asyncio.Event()

    class FlakyRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.checkpoint_reads = 0

        async def get_checkpoint(self, job_id: str) -> IngestionCheckpoint | None:
            self.checkpoint_reads += 1
            if self.checkpoint_reads == 1:
                raise RuntimeError("database connection reset")
            return await super().get_checkpoint(job_id)

    class StoppingAdapter(FakeAdapter):
        async def fetch(self, window: ObservationWindow) -> AdapterResult[Any]:
            result = await super().fetch(window)
            stop_event.set()
            return result

    repository = FlakyRepository()
    worker = IngestionWorker(
        schedules=[schedule(StoppingAdapter())],
        repository=repository,
        locks=FakeLocks(),
        clock=lambda: NOW,
    )

    asyncio.run(
        worker.run_forever(stop_event, tick_interval=timedelta(milliseconds=1))
    )

    assert repository.checkpoint_reads == 2
    assert len(repository.successes) == 1
    assert "Ingestion tick crashed" in caplog.text


def test_reconciliation_revisits_preceding_48_hours_hourly() -> None:
    adapter = FakeAdapter()
    repository = FakeRepository()
    worker = IngestionWorker(
        schedules=[schedule(adapter, reconcile=True)],
        repository=repository,
        locks=FakeLocks(),
        clock=lambda: NOW,
    )

    outcomes = asyncio.run(worker.run_once(now=NOW))
    assert [outcome.mode for outcome in outcomes] == [
        RunMode.INCREMENTAL,
        RunMode.RECONCILIATION,
    ]
    assert adapter.windows[1] == ObservationWindow(
        start=NOW - timedelta(hours=48),
        end=NOW,
    )

    later = NOW + timedelta(minutes=2)
    outcomes = asyncio.run(worker.run_once(now=later))
    assert outcomes[0].status is RunStatus.SUCCEEDED
    assert outcomes[1].status is RunStatus.SKIPPED_NOT_DUE
