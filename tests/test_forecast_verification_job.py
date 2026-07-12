from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.forecast_verification.core import (
    TARGET_BY_METRIC,
    ForecastEvidence,
    OutturnEvidence,
    VerificationMetric,
    verify_forecasts,
)
from app.forecast_verification.job import (
    ForecastVerificationRunner,
    PAIR_INSERT_BATCH_SIZE,
    PersistenceOutcome,
    RunStatus,
    VerificationDateRange,
    VerificationRequest,
    _append_pairs,
)


TARGET = TARGET_BY_METRIC[VerificationMetric.NATIONAL_DEMAND]
NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


class FakeLoader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    async def load(self, target, *, window_start, window_end):
        self.calls.append((target, window_start, window_end))
        if self.fail:
            raise RuntimeError("raw upstream payload must never be reported")
        valid = window_start
        return (
            (
                ForecastEvidence(
                    observation_id=UUID("00000000-0000-0000-0000-000000000001"),
                    valid_from=valid,
                    issued_at=valid - timedelta(hours=1),
                    captured_at=valid + timedelta(days=20),
                    value=101,
                    revision=0,
                ),
            ),
            (
                OutturnEvidence(
                    observation_id=UUID("00000000-0000-0000-0000-000000000002"),
                    observed_at=valid,
                    retrieved_at=valid + timedelta(minutes=20),
                    value=100,
                    revision=0,
                ),
            ),
        )


class FakeStore:
    def __init__(self, *, succeeded: bool = False) -> None:
        self.was_succeeded = succeeded
        self.started = []
        self.persisted = []
        self.failures = []

    async def succeeded(self, job_key):
        return self.was_succeeded

    async def record_started(self, **kwargs):
        self.started.append(kwargs)

    async def persist_success(self, **kwargs):
        self.persisted.append(kwargs)
        return PersistenceOutcome(1, 0, 4, 0)

    async def record_failure(self, **kwargs):
        self.failures.append(kwargs)


class FakeLocks:
    def __init__(self, *, denied: set[str] | None = None) -> None:
        self.denied = denied or set()
        self.requested = []

    @asynccontextmanager
    async def acquire(self, lock_name):
        self.requested.append(lock_name)
        yield lock_name not in self.denied


def request(*, dry_run: bool = False, force: bool = False) -> VerificationRequest:
    return VerificationRequest(
        date_range=VerificationDateRange(date(2026, 7, 11), date(2026, 7, 12)),
        metrics=(VerificationMetric.NATIONAL_DEMAND,),
        dry_run=dry_run,
        force=force,
    )


@pytest.mark.asyncio
async def test_dry_run_is_planning_only_and_completed_checkpoint_resumes() -> None:
    loader = FakeLoader()
    store = FakeStore(succeeded=True)
    locks = FakeLocks()
    runner = ForecastVerificationRunner(
        loader=loader,
        store=store,
        locks=locks,
        clock=lambda: NOW,
    )

    planned = await runner.run(request(dry_run=True))
    resumed = await runner.run(request())

    assert planned.outcomes[0].status is RunStatus.PLANNED
    assert resumed.outcomes[0].status is RunStatus.SKIPPED_COMPLETED
    assert loader.calls == []
    assert store.started == []
    assert locks.requested == [
        "50hz:forecast:verify:2026-07-12.forecast-verification.1"
    ]


@pytest.mark.asyncio
async def test_force_refresh_uses_global_and_source_locks_then_persists() -> None:
    loader = FakeLoader()
    store = FakeStore(succeeded=True)
    locks = FakeLocks()
    runner = ForecastVerificationRunner(
        loader=loader,
        store=store,
        locks=locks,
        clock=lambda: NOW,
    )

    report = await runner.run(request(force=True))

    assert report.outcomes[0].status is RunStatus.SUCCEEDED
    assert report.outcomes[0].persistence == PersistenceOutcome(1, 0, 4, 0)
    assert len(loader.calls) == 1
    assert len(store.started) == 1
    assert len(store.persisted) == 1
    assert set(locks.requested[1:]) == {
        "50hz:ingest:elexon.ndf",
        "50hz:ingest:elexon.indo",
    }


@pytest.mark.asyncio
async def test_failure_report_keeps_only_bounded_exception_type() -> None:
    loader = FakeLoader(fail=True)
    store = FakeStore()
    runner = ForecastVerificationRunner(
        loader=loader,
        store=store,
        locks=FakeLocks(),
        clock=lambda: NOW,
    )

    report = await runner.run(request())

    assert report.exit_code == 1
    assert report.outcomes[0].status is RunStatus.FAILED
    assert report.outcomes[0].error_type == "RuntimeError"
    assert store.failures[0]["error_type"] == "RuntimeError"
    assert "payload" not in str(report)


class _Scalars:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class _DBResult:
    def __init__(self, values) -> None:
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class _DBSession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _DBResult(self.responses.pop(0))


@pytest.mark.asyncio
async def test_large_pair_writes_are_split_below_postgres_bind_limit() -> None:
    count = PAIR_INSERT_BATCH_SIZE + 1
    valid_times = tuple(
        datetime(2026, 7, 1, tzinfo=UTC) + timedelta(minutes=30 * index)
        for index in range(count)
    )
    forecasts = tuple(
        ForecastEvidence(
            observation_id=UUID(int=index + 1),
            valid_from=valid,
            issued_at=valid - timedelta(hours=1),
            captured_at=valid - timedelta(minutes=50),
            value=101,
            revision=0,
        )
        for index, valid in enumerate(valid_times)
    )
    outturns = tuple(
        OutturnEvidence(
            observation_id=UUID(int=index + count + 1),
            observed_at=valid,
            retrieved_at=valid + timedelta(minutes=20),
            value=100,
            revision=0,
        )
        for index, valid in enumerate(valid_times)
    )
    bundle = verify_forecasts(
        TARGET,
        forecasts=forecasts,
        outturns=outturns,
        window_start=valid_times[0],
        window_end=valid_times[-1] + timedelta(minutes=30),
    )
    session = _DBSession(
        [
            [],
            [UUID(int=index + 10_000) for index in range(PAIR_INSERT_BATCH_SIZE)],
            [UUID(int=20_000)],
        ]
    )

    inserted, unchanged = await _append_pairs(session, bundle)

    assert (inserted, unchanged) == (count, 0)
    assert len(session.statements) == 3
    bind_counts = [
        len(statement.compile(dialect=postgresql.dialect()).params)
        for statement in session.statements[1:]
    ]
    assert max(bind_counts) < 32_767
