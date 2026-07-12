from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.history.calendar import expected_settlement_intervals
from app.history.materialization_job import (
    DEFAULT_MATERIALIZATION_DAYS,
    MATERIALIZATION_GENERATION_SELECTORS,
    MATERIALIZATION_INTERCONNECTOR_SELECTORS,
    MATERIALIZATION_REGISTRY_VERSION,
    MATERIALIZATION_TARGETS,
    MaterializationChunk,
    MaterializationDateRange,
    MaterializationRequest,
    MaterializationStatus,
    HistoryMaterializationRunner,
    PersistMaterializationOutcome,
    _next_revision,
    build_parser,
    build_materialized_chunk,
    materialization_job_key,
    plan_materialization_chunks,
    request_from_args,
    resolve_materialization_date_range,
)
from app.history.materialize import RawMetricObservation, RawMetricSeries
from app.history.models import MetricSeriesIdentity, ResultStatus
from app.history.repository import HistoryMetric


DEMAND_TARGET = next(
    target
    for target in MATERIALIZATION_TARGETS
    if target.metric is HistoryMetric.NATIONAL_DEMAND
)
LONDON = ZoneInfo("Europe/London")


def demand_raw(
    chunk: MaterializationChunk,
    *,
    omit: set[datetime] | None = None,
    correction: tuple[datetime, float] | None = None,
) -> RawMetricSeries:
    omitted = {value.astimezone(UTC) for value in (omit or set())}
    observations: list[RawMetricObservation] = []
    day = chunk.read_start
    index = 0
    while day < chunk.output_end:
        for interval in expected_settlement_intervals(day):
            timestamp = interval.start.astimezone(UTC)
            if timestamp in omitted:
                continue
            value = float(20_000 + index)
            observations.append(
                RawMetricObservation(
                    timestamp=timestamp,
                    value=value,
                    revision=0,
                    source_record_id=f"indo:{timestamp.isoformat()}:r0",
                    retrieved_at=timestamp + timedelta(hours=1),
                )
            )
            if correction is not None and timestamp == correction[0].astimezone(UTC):
                observations.append(
                    RawMetricObservation(
                        timestamp=timestamp,
                        value=correction[1],
                        revision=1,
                        source_record_id=f"indo:{timestamp.isoformat()}:r1",
                        retrieved_at=timestamp + timedelta(days=2),
                    )
                )
            index += 1
        day += timedelta(days=1)
    return RawMetricSeries(
        identity=MetricSeriesIdentity(
            metric_id=HistoryMetric.NATIONAL_DEMAND.value,
            geography="GB",
            unit="MW",
            fact_class="observed",
            source_id=DEMAND_TARGET.source_id,
            methodology_version="indo-national-demand-v1",
        ),
        source_cadence_minutes=30,
        observations=tuple(observations),
    )


@pytest.mark.parametrize(
    ("day", "expected_intervals"),
    ((date(2026, 3, 29), 46), (date(2026, 10, 25), 50)),
)
def test_materialized_chunk_is_london_dst_safe_and_complete(
    day: date,
    expected_intervals: int,
) -> None:
    chunk = MaterializationChunk(day, day + timedelta(days=1))

    result = build_materialized_chunk(
        target=DEMAND_TARGET,
        chunk=chunk,
        raw=demand_raw(chunk),
    )

    assert len(result.coverage) == 1
    assert result.coverage[0].expected_interval_count == expected_intervals
    assert result.coverage[0].observed_interval_count == expected_intervals
    assert result.coverage[0].coverage_fraction == 1
    assert result.coverage[0].is_sufficient
    assert result.aggregates[0].status == ResultStatus.AVAILABLE.value
    assert result.aggregates[0].value is not None
    assert len(result.baselines) == expected_intervals
    assert all(
        baseline.status == ResultStatus.AVAILABLE.value
        and baseline.sample_count == 28
        and baseline.coverage_fraction == 1
        for baseline in result.baselines
    )
    assert result.source_watermark_at is not None
    assert len(result.result_checksum) == 64


def test_aggregate_and_baseline_withhold_values_below_95_percent() -> None:
    day = date(2026, 7, 12)
    chunk = MaterializationChunk(day, day + timedelta(days=1))
    output_intervals = expected_settlement_intervals(day)
    missing = {
        output_intervals[1].start,
        output_intervals[2].start,
        output_intervals[3].start,
    }
    reference = output_intervals[36].start
    reference_local = reference.astimezone(LONDON)
    for days_back in (1, 2):
        history_day = day - timedelta(days=days_back)
        match = next(
            interval.start
            for interval in expected_settlement_intervals(history_day)
            if (
                interval.start.astimezone(reference_local.tzinfo).hour,
                interval.start.astimezone(reference_local.tzinfo).minute,
            )
            == (reference_local.hour, reference_local.minute)
        )
        missing.add(match)

    result = build_materialized_chunk(
        target=DEMAND_TARGET,
        chunk=chunk,
        raw=demand_raw(chunk, omit=missing),
    )

    assert result.coverage[0].coverage_fraction == pytest.approx(45 / 48)
    assert not result.coverage[0].is_sufficient
    assert result.aggregates[0].status == ResultStatus.INSUFFICIENT_DATA.value
    assert result.aggregates[0].value is None
    baseline = next(
        item for item in result.baselines if item.reference_start == reference
    )
    assert baseline.sample_count == 26
    assert baseline.coverage_fraction == pytest.approx(26 / 28)
    assert baseline.status == ResultStatus.INSUFFICIENT_DATA.value
    assert baseline.median is None


def test_source_correction_changes_derived_evidence_without_mutating_old_revision() -> None:
    day = date(2026, 7, 12)
    chunk = MaterializationChunk(day, day + timedelta(days=1))
    corrected_at = expected_settlement_intervals(day)[10].start
    original = build_materialized_chunk(
        target=DEMAND_TARGET,
        chunk=chunk,
        raw=demand_raw(chunk),
    )
    corrected = build_materialized_chunk(
        target=DEMAND_TARGET,
        chunk=chunk,
        raw=demand_raw(chunk, correction=(corrected_at, 99_999)),
    )

    assert corrected.coverage[0].content_sha256 != original.coverage[0].content_sha256
    assert corrected.aggregates[0].content_sha256 != original.aggregates[0].content_sha256
    assert corrected.aggregates[0].value != original.aggregates[0].value
    assert corrected.result_checksum != original.result_checksum

    class Existing:
        revision = 0
        content_sha256 = original.coverage[0].content_sha256

    assert _next_revision(Existing(), original.coverage[0].content_sha256) is None
    assert _next_revision(Existing(), corrected.coverage[0].content_sha256) == 1


def test_registry_is_explicit_and_does_not_fabricate_frequency_or_forecasts() -> None:
    metrics = {target.metric for target in MATERIALIZATION_TARGETS}

    assert metrics == set(HistoryMetric)
    assert all("frequency" not in target.key for target in MATERIALIZATION_TARGETS)
    assert all("forecast" not in target.key for target in MATERIALIZATION_TARGETS)
    assert tuple(
        target.selector
        for target in MATERIALIZATION_TARGETS
        if target.metric is HistoryMetric.GENERATION_FUEL
    ) == MATERIALIZATION_GENERATION_SELECTORS
    assert tuple(
        target.selector
        for target in MATERIALIZATION_TARGETS
        if target.metric is HistoryMetric.INTERCONNECTOR_FLOW
    ) == MATERIALIZATION_INTERCONNECTOR_SELECTORS
    assert {
        target.selector
        for target in MATERIALIZATION_TARGETS
        if target.metric is HistoryMetric.GENERATION_FUEL
    }
    assert {
        target.selector
        for target in MATERIALIZATION_TARGETS
        if target.metric is HistoryMetric.INTERCONNECTOR_FLOW
    }
    assert MATERIALIZATION_REGISTRY_VERSION.endswith(".history.1")


def test_date_range_defaults_bounds_completed_days_and_chunks_with_lookback() -> None:
    today = date(2026, 7, 12)
    resolved = resolve_materialization_date_range(
        days=None,
        start=None,
        end=None,
        today=today,
    )
    chunks = plan_materialization_chunks(resolved)

    assert resolved.day_count == DEFAULT_MATERIALIZATION_DAYS == 90
    assert resolved.end == today
    assert [chunk.output_day_count for chunk in chunks] == [30, 30, 30]
    assert chunks[0].read_start == resolved.start - timedelta(days=28)
    assert chunks[-1].output_end == today

    with pytest.raises(ValueError, match="cannot exceed 95"):
        MaterializationDateRange(today - timedelta(days=96), today)
    with pytest.raises(ValueError, match="later than today's"):
        resolve_materialization_date_range(
            days=1,
            start=None,
            end=today + timedelta(days=1),
            today=today,
        )


def test_refresh_latest_is_a_forced_one_day_production_path() -> None:
    parser = build_parser()
    today = date(2026, 7, 12)

    request = request_from_args(
        parser.parse_args(["--refresh-latest"]),
        today=today,
    )

    assert request.refresh_latest
    assert request.force
    assert request.date_range == MaterializationDateRange(
        date(2026, 7, 11),
        date(2026, 7, 12),
    )
    chunks = plan_materialization_chunks(request.date_range)
    assert len(chunks) == 1
    assert chunks[0].read_start == date(2026, 6, 13)

    with pytest.raises(ValueError, match="cannot be combined"):
        request_from_args(
            parser.parse_args(["--refresh-latest", "--days", "2"]),
            today=today,
        )


class FakeLoader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests = []

    async def load(self, request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("payload must never appear in report")
        chunk = MaterializationChunk(
            request.start.astimezone(LONDON).date() + timedelta(days=28),
            request.end.astimezone(LONDON).date(),
        )
        return demand_raw(chunk)


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
        return PersistMaterializationOutcome(inserted=50, unchanged=0)

    async def record_failure(self, **kwargs):
        self.failures.append(kwargs)


class FakeLocks:
    def __init__(self, denied: set[str] | None = None) -> None:
        self.denied = denied or set()
        self.requested = []

    @asynccontextmanager
    async def acquire(self, lock_name):
        self.requested.append(lock_name)
        yield lock_name not in self.denied


def one_day_request(*, dry_run: bool = False, force: bool = False):
    return MaterializationRequest(
        date_range=MaterializationDateRange(date(2026, 7, 12), date(2026, 7, 13)),
        metrics=(HistoryMetric.NATIONAL_DEMAND,),
        dry_run=dry_run,
        force=force,
    )


@pytest.mark.asyncio
async def test_runner_dry_run_does_not_touch_database_loader_or_locks() -> None:
    loader = FakeLoader()
    store = FakeStore()
    locks = FakeLocks()
    runner = HistoryMaterializationRunner(loader=loader, store=store, locks=locks)

    report = await runner.run(one_day_request(dry_run=True))

    assert [outcome.status for outcome in report.outcomes] == [
        MaterializationStatus.PLANNED
    ]
    assert loader.requests == []
    assert store.started == []
    assert locks.requested == []


@pytest.mark.asyncio
async def test_runner_resumes_success_and_force_rechecks_under_both_locks() -> None:
    store = FakeStore(succeeded=True)
    loader = FakeLoader()
    locks = FakeLocks()
    runner = HistoryMaterializationRunner(loader=loader, store=store, locks=locks)

    skipped = await runner.run(one_day_request())
    assert skipped.outcomes[0].status is MaterializationStatus.SKIPPED_COMPLETED
    assert loader.requests == []

    forced = await runner.run(one_day_request(force=True))
    assert forced.outcomes[0].status is MaterializationStatus.SUCCEEDED
    assert len(loader.requests) == 1
    assert len(store.started) == 1
    assert len(store.persisted) == 1
    assert any(name.startswith("50hz:history:materialize:") for name in locks.requested)
    assert DEMAND_TARGET.source_lock_name in locks.requested
    assert forced.outcomes[0].job_key == materialization_job_key(
        DEMAND_TARGET,
        MaterializationChunk(date(2026, 7, 12), date(2026, 7, 13)),
    )


@pytest.mark.asyncio
async def test_runner_isolates_source_lock_and_failure_without_leaking_message() -> None:
    denied_locks = FakeLocks({DEMAND_TARGET.source_lock_name})
    denied = await HistoryMaterializationRunner(
        loader=FakeLoader(),
        store=FakeStore(),
        locks=denied_locks,
    ).run(one_day_request())
    assert denied.outcomes[0].status is MaterializationStatus.SKIPPED_LOCKED

    store = FakeStore()
    failed = await HistoryMaterializationRunner(
        loader=FakeLoader(fail=True),
        store=store,
        locks=FakeLocks(),
    ).run(one_day_request())
    outcome = failed.outcomes[0]
    assert outcome.status is MaterializationStatus.FAILED
    assert outcome.error_type == "RuntimeError"
    assert "payload" not in repr(outcome)
    assert store.failures[0]["error_type"] == "RuntimeError"
