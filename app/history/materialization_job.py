"""Bounded materialization of normalized history into auditable derived tables.

This is an operator command, not an API or polling-worker dependency::

    python -m app.history.materialization_job --days 90 --dry-run
    python -m app.history.materialization_job --days 90

Output dates are Europe/London settlement dates and ``--end`` is exclusive.
Successful chunks are checkpoints. Re-running skips them; ``--force`` re-evaluates
their immutable source revisions and appends a derived revision only when the
audited content changed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ComparisonBaseline,
    HistoryMaterializationRun,
    MetricAggregate,
    MetricDefinition as StoredMetricDefinition,
    ObservationCoverageDaily,
)
from app.db.session import (
    DatabaseNotConfiguredError,
    configured_database_url,
    dispose_engine,
    get_session_factory,
)
from app.history.calendar import expected_settlement_intervals
from app.history.materialize import RawMetricSeries, materialize_half_hours
from app.history.models import (
    DAILY_AGGREGATE_VERSION,
    HISTORY_METHODOLOGY_VERSION,
    IDENTITY_VERSION,
    MINIMUM_COVERAGE_FRACTION,
    ROLLING_DAY_COUNT,
    MetricSeries,
    ResultStatus,
)
from app.history.repository import (
    FUELINST_SOURCE_ID,
    INDO_SOURCE_ID,
    NATIONAL_CARBON_SOURCE_ID,
    HistoryMetric,
    HistorySeriesRequest,
    NormalizedHistoryRepository,
)
from app.history.service import (
    aggregate_daily_mean,
    assess_daily_coverage,
    compare_history_many,
)
from app.metrics.registry import METRIC_DEFINITIONS, METRIC_REGISTRY_VERSION
from app.persistence.locks import PostgresAdvisoryLockProvider
from app.sources.elexon import FUEL_TYPES, INTERCONNECTOR_NAMES
from app.worker.contracts import AdvisoryLockProvider


LONDON = ZoneInfo("Europe/London")
MAX_MATERIALIZATION_DAYS = 95
DEFAULT_MATERIALIZATION_DAYS = 90
OUTPUT_CHUNK_DAYS = 30
MATERIALIZATION_LOOKBACK_DAYS = ROLLING_DAY_COUNT
MATERIALIZATION_JOB_VERSION = "50hz.history.materialization.v1"
MATERIALIZATION_COVERAGE_VERSION = "50hz.history.coverage.v1"
MATERIALIZATION_REGISTRY_VERSION = "2026-07-12.1.history.1"
BASELINE_KIND = "rolling_28_same_local_half_hour"
AGGREGATE_KIND = "daily_mean"
_ROW_NAMESPACE = uuid.UUID("d21b8131-d273-535a-bad2-71b55b99fbea")
_RUN_NAMESPACE = uuid.UUID("91a1af14-9940-50fc-bcb6-df6a58cadf89")
MATERIALIZATION_GENERATION_SELECTORS = (
    "BIOMASS",
    "CCGT",
    "COAL",
    "NPSHYD",
    "NUCLEAR",
    "OCGT",
    "OIL",
    "OTHER",
    "PS",
    "SOLAR",
    "WIND",
)
MATERIALIZATION_INTERCONNECTOR_SELECTORS = (
    "INTELEC",
    "INTEW",
    "INTFR",
    "INTGRNL",
    "INTIFA2",
    "INTIRL",
    "INTNED",
    "INTNEM",
    "INTNSL",
    "INTVKL",
)
if not MATERIALIZATION_REGISTRY_VERSION.startswith(f"{METRIC_REGISTRY_VERSION}."):
    raise RuntimeError(
        "history materialization registry must identify its metric-registry base"
    )


class MaterializationStatus(StrEnum):
    PLANNED = "planned"
    SUCCEEDED = "succeeded"
    SKIPPED_COMPLETED = "skipped_completed"
    SKIPPED_LOCKED = "skipped_locked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MaterializationDateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if isinstance(self.start, datetime) or not isinstance(self.start, date):
            raise TypeError("start must be a date")
        if isinstance(self.end, datetime) or not isinstance(self.end, date):
            raise TypeError("end must be a date")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.day_count > MAX_MATERIALIZATION_DAYS:
            raise ValueError(
                f"materialization ranges cannot exceed {MAX_MATERIALIZATION_DAYS} days"
            )

    @property
    def day_count(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True, slots=True)
class MaterializationChunk:
    output_start: date
    output_end: date

    def __post_init__(self) -> None:
        if self.output_end <= self.output_start:
            raise ValueError("chunk end must follow its start")
        if self.output_day_count > OUTPUT_CHUNK_DAYS:
            raise ValueError(f"chunks cannot exceed {OUTPUT_CHUNK_DAYS} output days")

    @property
    def output_day_count(self) -> int:
        return (self.output_end - self.output_start).days

    @property
    def read_start(self) -> date:
        return self.output_start - timedelta(days=MATERIALIZATION_LOOKBACK_DAYS)

    @property
    def read_start_utc(self) -> datetime:
        return _local_midnight_utc(self.read_start)

    @property
    def read_end_utc(self) -> datetime:
        return _local_midnight_utc(self.output_end)


@dataclass(frozen=True, slots=True)
class MaterializationTarget:
    metric: HistoryMetric
    selector: str | None
    source_id: str
    ingestion_lock_source_id: str
    series_key: str

    @property
    def stable_metric_id(self) -> str:
        return self.metric.value

    @property
    def key(self) -> str:
        suffix = self.selector or self.series_key
        return f"{self.metric.value}:{suffix}"

    @property
    def definition(self):
        matches = tuple(
            definition
            for definition in METRIC_DEFINITIONS
            if definition.metric_id == self.stable_metric_id
        )
        if len(matches) != 1:
            raise RuntimeError(
                f"current metric registry must define {self.stable_metric_id} exactly once"
            )
        return matches[0]

    @property
    def source_lock_name(self) -> str:
        return f"50hz:ingest:{self.ingestion_lock_source_id}"


def _build_targets() -> tuple[MaterializationTarget, ...]:
    targets = [
        MaterializationTarget(
            metric=HistoryMetric.NATIONAL_CARBON,
            selector=None,
            source_id=NATIONAL_CARBON_SOURCE_ID,
            ingestion_lock_source_id="neso.carbon.national.current",
            series_key="gb",
        ),
        MaterializationTarget(
            metric=HistoryMetric.NATIONAL_DEMAND,
            selector=None,
            source_id=INDO_SOURCE_ID,
            ingestion_lock_source_id="elexon.indo",
            series_key="gb",
        ),
    ]
    targets.extend(
        MaterializationTarget(
            metric=HistoryMetric.GENERATION_FUEL,
            selector=selector,
            source_id=FUELINST_SOURCE_ID,
            ingestion_lock_source_id="elexon.fuelinst",
            series_key=selector,
        )
        for selector in MATERIALIZATION_GENERATION_SELECTORS
    )
    targets.extend(
        MaterializationTarget(
            metric=HistoryMetric.INTERCONNECTOR_FLOW,
            selector=selector,
            source_id=FUELINST_SOURCE_ID,
            ingestion_lock_source_id="elexon.interconnectors",
            series_key=selector,
        )
        for selector in MATERIALIZATION_INTERCONNECTOR_SELECTORS
    )
    result = tuple(targets)
    keys = tuple(target.key for target in result)
    if len(keys) != len(set(keys)):
        raise RuntimeError("history materialization target keys must be unique")
    if any(selector not in FUEL_TYPES for selector in MATERIALIZATION_GENERATION_SELECTORS):
        raise RuntimeError("history generation registry contains an unsupported selector")
    if any(
        selector not in INTERCONNECTOR_NAMES
        for selector in MATERIALIZATION_INTERCONNECTOR_SELECTORS
    ):
        raise RuntimeError("history interconnector registry contains an unsupported selector")
    for target in result:
        definition = target.definition
        if definition.methodology_version not in {
            "neso-national-carbon-v1",
            "indo-national-demand-v1",
            "fuelinst-generation-v1",
            "fuelinst-interconnector-flow-v1",
        }:
            raise RuntimeError("history target uses an unreviewed methodology version")
    return result


MATERIALIZATION_TARGETS = _build_targets()
MATERIALIZABLE_METRICS = (
    HistoryMetric.NATIONAL_CARBON,
    HistoryMetric.NATIONAL_DEMAND,
    HistoryMetric.GENERATION_FUEL,
    HistoryMetric.INTERCONNECTOR_FLOW,
)


@dataclass(frozen=True, slots=True)
class MaterializationRequest:
    date_range: MaterializationDateRange
    metrics: tuple[HistoryMetric, ...] = MATERIALIZABLE_METRICS
    dry_run: bool = False
    force: bool = False
    refresh_latest: bool = False

    def __post_init__(self) -> None:
        if not self.metrics:
            raise ValueError("at least one materializable metric is required")
        if len(self.metrics) != len(set(self.metrics)):
            raise ValueError("materialization metrics must be unique")
        if self.refresh_latest:
            if self.date_range.day_count != 1:
                raise ValueError("latest refresh must contain exactly one completed day")
            if not self.force:
                raise ValueError("latest refresh must recheck its existing checkpoint")

    @property
    def targets(self) -> tuple[MaterializationTarget, ...]:
        selected = set(self.metrics)
        return tuple(
            target for target in MATERIALIZATION_TARGETS if target.metric in selected
        )


@dataclass(frozen=True, slots=True)
class CoverageWrite:
    settlement_date: date
    expected_interval_count: int
    observed_interval_count: int
    duplicate_interval_count: int
    source_record_count: int
    coverage_fraction: float
    is_sufficient: bool
    missing_starts: tuple[str, ...]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class AggregateWrite:
    settlement_date: date
    period_start: datetime
    period_end: datetime
    value: float | None
    unit: str
    sample_count: int
    expected_sample_count: int
    coverage_fraction: float
    status: str
    attributes: dict[str, Any]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class BaselineWrite:
    reference_start: datetime
    window_start: datetime
    window_end: datetime
    median: float | None
    first_quartile: float | None
    third_quartile: float | None
    sample_count: int
    expected_sample_count: int
    coverage_fraction: float
    status: str
    attributes: dict[str, Any]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class MaterializedChunk:
    target: MaterializationTarget
    chunk: MaterializationChunk
    coverage: tuple[CoverageWrite, ...]
    aggregates: tuple[AggregateWrite, ...]
    baselines: tuple[BaselineWrite, ...]
    source_watermark_at: datetime | None
    result_checksum: str


@dataclass(frozen=True, slots=True)
class PersistMaterializationOutcome:
    inserted: int = 0
    unchanged: int = 0


@dataclass(frozen=True, slots=True)
class MaterializationOutcome:
    target_key: str
    job_key: str
    chunk: MaterializationChunk
    status: MaterializationStatus
    persistence: PersistMaterializationOutcome | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class MaterializationReport:
    request: MaterializationRequest
    outcomes: tuple[MaterializationOutcome, ...]

    @property
    def exit_code(self) -> int:
        incomplete = {MaterializationStatus.FAILED, MaterializationStatus.SKIPPED_LOCKED}
        return 1 if any(outcome.status in incomplete for outcome in self.outcomes) else 0

    def counts(self) -> Counter[MaterializationStatus]:
        return Counter(outcome.status for outcome in self.outcomes)


class HistorySeriesLoader(Protocol):
    async def load(self, request: HistorySeriesRequest) -> RawMetricSeries: ...


class HistoryMaterializationStore(Protocol):
    async def succeeded(self, job_key: str) -> bool: ...

    async def record_started(
        self,
        *,
        job_key: str,
        target: MaterializationTarget,
        chunk: MaterializationChunk,
        started_at: datetime,
    ) -> None: ...

    async def persist_success(
        self,
        *,
        job_key: str,
        materialized: MaterializedChunk,
        completed_at: datetime,
    ) -> PersistMaterializationOutcome: ...

    async def record_failure(
        self,
        *,
        job_key: str,
        failed_at: datetime,
        error_type: str,
    ) -> None: ...


def resolve_materialization_date_range(
    *,
    days: int | None,
    start: date | None,
    end: date | None,
    today: date,
) -> MaterializationDateRange:
    if isinstance(today, datetime) or not isinstance(today, date):
        raise TypeError("today must be a date")
    if days is not None and start is not None:
        raise ValueError("use either --days or --start, not both")
    if days is None and start is None:
        days = DEFAULT_MATERIALIZATION_DAYS
    resolved_end = end or today
    if resolved_end > today:
        raise ValueError("end cannot be later than today's London settlement date")
    if days is not None:
        if isinstance(days, bool) or not isinstance(days, int):
            raise TypeError("days must be an integer")
        if not 1 <= days <= MAX_MATERIALIZATION_DAYS:
            raise ValueError(f"days must be between 1 and {MAX_MATERIALIZATION_DAYS}")
        resolved_start = resolved_end - timedelta(days=days)
    else:
        if end is None:
            raise ValueError("--start requires an exclusive --end")
        assert start is not None
        resolved_start = start
    return MaterializationDateRange(resolved_start, resolved_end)


def plan_materialization_chunks(
    date_range: MaterializationDateRange,
) -> tuple[MaterializationChunk, ...]:
    chunks: list[MaterializationChunk] = []
    cursor = date_range.start
    while cursor < date_range.end:
        chunk_end = min(cursor + timedelta(days=OUTPUT_CHUNK_DAYS), date_range.end)
        chunks.append(MaterializationChunk(cursor, chunk_end))
        cursor = chunk_end
    return tuple(chunks)


def materialization_job_key(
    target: MaterializationTarget,
    chunk: MaterializationChunk,
) -> str:
    identity = "\x1f".join(
        (
            MATERIALIZATION_JOB_VERSION,
            MATERIALIZATION_REGISTRY_VERSION,
            target.key,
            chunk.output_start.isoformat(),
            chunk.output_end.isoformat(),
        )
    )
    return f"history-materialize:{hashlib.sha256(identity.encode()).hexdigest()}"


def build_materialized_chunk(
    *,
    target: MaterializationTarget,
    chunk: MaterializationChunk,
    raw: RawMetricSeries,
) -> MaterializedChunk:
    result = materialize_half_hours(
        raw,
        start=chunk.read_start_utc,
        end=chunk.read_end_utc,
        minimum_coverage_fraction=MINIMUM_COVERAGE_FRACTION,
    )
    _validate_materialized_identity(target, result.series)
    interval_by_start = {
        interval.start.astimezone(UTC): interval for interval in result.intervals
    }
    source_watermark = _source_watermark(raw)
    coverage_writes: list[CoverageWrite] = []
    aggregate_writes: list[AggregateWrite] = []
    baseline_writes: list[BaselineWrite] = []
    output_intervals = tuple(
        interval
        for output_day in _days(chunk.output_start, chunk.output_end)
        for interval in expected_settlement_intervals(output_day)
    )
    comparison_by_start = {
        interval.start.astimezone(UTC): comparison
        for interval, comparison in zip(
            output_intervals,
            compare_history_many(
                result.series,
                reference_starts=(interval.start for interval in output_intervals),
                history_series=result.series,
            ),
            strict=True,
        )
    }

    day = chunk.output_start
    while day < chunk.output_end:
        expected = expected_settlement_intervals(day)
        expected_starts = {interval.start.astimezone(UTC) for interval in expected}
        day_series = MetricSeries(
            identity=result.series.identity,
            observations=[
                observation
                for observation in result.series.observations
                if observation.start.astimezone(UTC) in expected_starts
            ],
        )
        coverage = assess_daily_coverage(day_series, day)
        aggregate = aggregate_daily_mean(day_series, day)
        interval_evidence = [
            interval_by_start[start].model_dump(mode="json")
            for start in sorted(expected_starts)
        ]
        coverage_payload = {
            "version": MATERIALIZATION_COVERAGE_VERSION,
            "identity": result.series.identity.model_dump(mode="json"),
            "coverage": coverage.model_dump(mode="json"),
            "intervalEvidence": interval_evidence,
        }
        coverage_sha = _checksum(coverage_payload)
        coverage_writes.append(
            CoverageWrite(
                settlement_date=day,
                expected_interval_count=coverage.expected_interval_count,
                observed_interval_count=coverage.unique_interval_count,
                duplicate_interval_count=len(coverage.duplicate_starts),
                source_record_count=sum(
                    interval_by_start[start].raw_sample_count
                    for start in expected_starts
                ),
                coverage_fraction=coverage.coverage_fraction,
                is_sufficient=coverage.is_sufficient,
                missing_starts=tuple(
                    value.astimezone(UTC).isoformat() for value in coverage.missing_starts
                ),
                content_sha256=coverage_sha,
            )
        )
        day_start = expected[0].start.astimezone(UTC)
        day_end = expected[-1].end.astimezone(UTC)
        aggregate_payload = {
            "identity": result.series.identity.model_dump(mode="json"),
            "aggregate": aggregate.model_dump(mode="json"),
            "coverageContentSha256": coverage_sha,
        }
        aggregate_sha = _checksum(aggregate_payload)
        aggregate_writes.append(
            AggregateWrite(
                settlement_date=day,
                period_start=day_start,
                period_end=day_end,
                value=aggregate.value,
                unit=result.series.identity.unit,
                sample_count=coverage.unique_interval_count,
                expected_sample_count=coverage.expected_interval_count,
                coverage_fraction=coverage.coverage_fraction,
                status=aggregate.status.value,
                attributes={
                    "reason": aggregate.reason.value,
                    "coverageContentSha256": coverage_sha,
                    "sourceEvidenceSha256": _checksum(interval_evidence),
                },
                content_sha256=aggregate_sha,
            )
        )

        baseline_window_start = _local_midnight_utc(
            day - timedelta(days=MATERIALIZATION_LOOKBACK_DAYS)
        )
        for interval in expected:
            comparison = comparison_by_start[interval.start.astimezone(UTC)]
            rolling = comparison.rolling_28_days
            baseline_payload = {
                "identity": result.series.identity.model_dump(mode="json"),
                "rolling": rolling.model_dump(mode="json"),
            }
            baseline_writes.append(
                BaselineWrite(
                    reference_start=interval.start.astimezone(UTC),
                    window_start=baseline_window_start,
                    window_end=interval.start.astimezone(UTC),
                    median=rolling.median,
                    first_quartile=rolling.first_quartile,
                    third_quartile=rolling.third_quartile,
                    sample_count=rolling.coverage.valid_sample_count,
                    expected_sample_count=ROLLING_DAY_COUNT,
                    coverage_fraction=rolling.coverage.coverage_fraction,
                    status=rolling.status.value,
                    attributes={
                        "reason": rolling.reason.value,
                        "sourceEvidenceSha256": _checksum(rolling.source_record_ids),
                        "missingDateCount": len(rolling.coverage.missing_dates),
                        "duplicateStartCount": len(
                            rolling.coverage.duplicate_starts
                        ),
                        "ambiguousDateCount": len(rolling.coverage.ambiguous_dates),
                    },
                    content_sha256=_checksum(baseline_payload),
                )
            )
        day += timedelta(days=1)

    combined = [
        *(write.content_sha256 for write in coverage_writes),
        *(write.content_sha256 for write in aggregate_writes),
        *(write.content_sha256 for write in baseline_writes),
    ]
    result_checksum = _checksum(
        {
            "version": MATERIALIZATION_JOB_VERSION,
            "registryVersion": MATERIALIZATION_REGISTRY_VERSION,
            "target": target.key,
            "outputStart": chunk.output_start.isoformat(),
            "outputEnd": chunk.output_end.isoformat(),
            "records": combined,
        }
    )
    return MaterializedChunk(
        target=target,
        chunk=chunk,
        coverage=tuple(coverage_writes),
        aggregates=tuple(aggregate_writes),
        baselines=tuple(baseline_writes),
        source_watermark_at=source_watermark,
        result_checksum=result_checksum,
    )


class HistoryMaterializationRunner:
    def __init__(
        self,
        *,
        loader: HistorySeriesLoader,
        store: HistoryMaterializationStore,
        locks: AdvisoryLockProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.loader = loader
        self.store = store
        self.locks = locks
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run(self, request: MaterializationRequest) -> MaterializationReport:
        chunks = plan_materialization_chunks(request.date_range)
        if request.dry_run:
            return MaterializationReport(
                request=request,
                outcomes=tuple(
                    MaterializationOutcome(
                        target_key=target.key,
                        job_key=materialization_job_key(target, chunk),
                        chunk=chunk,
                        status=MaterializationStatus.PLANNED,
                    )
                    for target in request.targets
                    for chunk in chunks
                ),
            )

        outcomes: list[MaterializationOutcome] = []
        global_lock = f"50hz:history:materialize:{MATERIALIZATION_REGISTRY_VERSION}"
        async with self.locks.acquire(global_lock) as globally_acquired:
            if not globally_acquired:
                return MaterializationReport(
                    request=request,
                    outcomes=tuple(
                        MaterializationOutcome(
                            target_key=target.key,
                            job_key=materialization_job_key(target, chunk),
                            chunk=chunk,
                            status=MaterializationStatus.SKIPPED_LOCKED,
                        )
                        for target in request.targets
                        for chunk in chunks
                    ),
                )
            for target in request.targets:
                for chunk in chunks:
                    outcomes.append(await self._run_chunk(request, target, chunk))
        return MaterializationReport(request=request, outcomes=tuple(outcomes))

    async def _run_chunk(
        self,
        request: MaterializationRequest,
        target: MaterializationTarget,
        chunk: MaterializationChunk,
    ) -> MaterializationOutcome:
        job_key = materialization_job_key(target, chunk)
        try:
            if not request.force and await self.store.succeeded(job_key):
                return MaterializationOutcome(
                    target_key=target.key,
                    job_key=job_key,
                    chunk=chunk,
                    status=MaterializationStatus.SKIPPED_COMPLETED,
                )
            async with self.locks.acquire(target.source_lock_name) as acquired:
                if not acquired:
                    return MaterializationOutcome(
                        target_key=target.key,
                        job_key=job_key,
                        chunk=chunk,
                        status=MaterializationStatus.SKIPPED_LOCKED,
                    )
                started_at = _aware_utc(self.clock(), "clock")
                await self.store.record_started(
                    job_key=job_key,
                    target=target,
                    chunk=chunk,
                    started_at=started_at,
                )
                raw = await self.loader.load(
                    HistorySeriesRequest(
                        metric_id=target.metric,
                        source_id=target.source_id,
                        selector=target.selector,
                        start=chunk.read_start_utc,
                        end=chunk.read_end_utc,
                    )
                )
            # The SELECT above is one MVCC snapshot and normalized revisions are
            # immutable. Release the live-ingestion lock before CPU work and the
            # derived transaction so a 90-day operator run cannot starve polling.
            materialized = build_materialized_chunk(
                target=target,
                chunk=chunk,
                raw=raw,
            )
            persisted = await self.store.persist_success(
                job_key=job_key,
                materialized=materialized,
                completed_at=_aware_utc(self.clock(), "clock"),
            )
            return MaterializationOutcome(
                target_key=target.key,
                job_key=job_key,
                chunk=chunk,
                status=MaterializationStatus.SUCCEEDED,
                persistence=persisted,
            )
        except Exception as exc:
            try:
                await self.store.record_failure(
                    job_key=job_key,
                    failed_at=_aware_utc(self.clock(), "clock"),
                    error_type=type(exc).__name__,
                )
            except Exception:
                pass
            return MaterializationOutcome(
                target_key=target.key,
                job_key=job_key,
                chunk=chunk,
                status=MaterializationStatus.FAILED,
                error_type=type(exc).__name__,
            )


SessionFactory = Callable[[], AsyncSession]


class PostgresHistoryMaterializationStore:
    """Append-only derived storage plus mutable, payload-free checkpoints."""

    def __init__(self, session_factory: SessionFactory) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self.session_factory = session_factory

    async def succeeded(self, job_key: str) -> bool:
        async with self.session_factory() as session:
            status = await session.scalar(
                select(HistoryMaterializationRun.status).where(
                    HistoryMaterializationRun.job_key == job_key
                )
            )
        return status == MaterializationStatus.SUCCEEDED.value

    async def record_started(
        self,
        *,
        job_key: str,
        target: MaterializationTarget,
        chunk: MaterializationChunk,
        started_at: datetime,
    ) -> None:
        values = {
            "id": _run_id(job_key),
            "job_key": job_key,
            "registry_version": MATERIALIZATION_REGISTRY_VERSION,
            "stable_metric_id": target.stable_metric_id,
            "series_key": target.series_key,
            "geography": "GB",
            "source_id": target.source_id,
            "output_start_date": chunk.output_start,
            "output_end_date": chunk.output_end,
            "status": "running",
            "attempt_count": 1,
            "records_written": 0,
            "started_at": started_at,
            "completed_at": None,
            "error_type": None,
            "result_checksum": None,
            "source_watermark_at": None,
        }
        statement = pg_insert(HistoryMaterializationRun).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[HistoryMaterializationRun.job_key],
            set_={
                "registry_version": statement.excluded.registry_version,
                "status": "running",
                "attempt_count": HistoryMaterializationRun.attempt_count + 1,
                "records_written": 0,
                "started_at": statement.excluded.started_at,
                "completed_at": None,
                "error_type": None,
                "result_checksum": None,
                "source_watermark_at": None,
            },
        )
        async with self.session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def persist_success(
        self,
        *,
        job_key: str,
        materialized: MaterializedChunk,
        completed_at: datetime,
    ) -> PersistMaterializationOutcome:
        async with self.session_factory() as session:
            definition_id = await _ensure_definition(session, materialized.target)
            inserted, unchanged = await _append_materialized_rows(
                session,
                definition_id=definition_id,
                materialized=materialized,
            )
            await session.execute(
                update(HistoryMaterializationRun)
                .where(HistoryMaterializationRun.job_key == job_key)
                .values(
                    metric_definition_id=definition_id,
                    status="succeeded",
                    records_written=inserted,
                    result_checksum=materialized.result_checksum,
                    source_watermark_at=materialized.source_watermark_at,
                    completed_at=completed_at,
                    error_type=None,
                )
            )
            await session.commit()
        return PersistMaterializationOutcome(inserted=inserted, unchanged=unchanged)

    async def record_failure(
        self,
        *,
        job_key: str,
        failed_at: datetime,
        error_type: str,
    ) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(HistoryMaterializationRun)
                .where(HistoryMaterializationRun.job_key == job_key)
                .values(
                    status="failed",
                    completed_at=failed_at,
                    error_type=(error_type or "OperationFailed")[:120],
                    records_written=0,
                    result_checksum=None,
                    source_watermark_at=None,
                )
            )
            await session.commit()


async def _ensure_definition(
    session: AsyncSession,
    target: MaterializationTarget,
) -> str:
    registered = target.definition
    expected = {
        "id": _definition_row_id(target),
        "stable_metric_id": registered.metric_id,
        "identity_version": IDENTITY_VERSION,
        "display_name": registered.display_name,
        "unit": registered.unit,
        "geography_scope": "GB",
        "fact_class": registered.classification.value,
        "methodology_version": registered.methodology_version,
        "source_id": target.source_id,
        "definition": registered.description,
        "inclusions": [registered.boundary, registered.methodology],
        "exclusions": list(registered.exclusions),
        "expected_interval_minutes": 30,
        "active": True,
        "attributes": {
            "firstRegisteredIn": METRIC_REGISTRY_VERSION,
            "sourceDatasets": list(registered.source_datasets),
            "sourceResolutionSeconds": registered.resolution_seconds,
            "signConvention": registered.sign_convention,
        },
    }
    statement = pg_insert(StoredMetricDefinition).values(**expected)
    statement = statement.on_conflict_do_nothing(
        constraint="uq_metric_definition_identity"
    )
    await session.execute(statement)
    row = await session.scalar(
        select(StoredMetricDefinition).where(
            StoredMetricDefinition.stable_metric_id == registered.metric_id,
            StoredMetricDefinition.identity_version == IDENTITY_VERSION,
            StoredMetricDefinition.methodology_version
            == registered.methodology_version,
        )
    )
    if row is None:
        raise RuntimeError("metric definition could not be resolved")
    comparable = (
        "stable_metric_id",
        "identity_version",
        "display_name",
        "unit",
        "geography_scope",
        "fact_class",
        "methodology_version",
        "source_id",
        "definition",
        "inclusions",
        "exclusions",
        "expected_interval_minutes",
        "active",
    )
    mismatches = tuple(name for name in comparable if getattr(row, name) != expected[name])
    if mismatches:
        raise RuntimeError(
            "stored metric definition conflicts with the current immutable contract: "
            + ", ".join(mismatches)
        )
    return row.id


async def _append_materialized_rows(
    session: AsyncSession,
    *,
    definition_id: str,
    materialized: MaterializedChunk,
) -> tuple[int, int]:
    target = materialized.target
    chunk = materialized.chunk
    coverage_rows = (
        await session.execute(
            select(ObservationCoverageDaily).where(
                ObservationCoverageDaily.metric_id == definition_id,
                ObservationCoverageDaily.series_key == target.series_key,
                ObservationCoverageDaily.geography == "GB",
                ObservationCoverageDaily.settlement_date >= chunk.output_start,
                ObservationCoverageDaily.settlement_date < chunk.output_end,
                ObservationCoverageDaily.methodology_version
                == MATERIALIZATION_COVERAGE_VERSION,
            )
        )
    ).scalars().all()
    aggregate_rows = (
        await session.execute(
            select(MetricAggregate).where(
                MetricAggregate.metric_id == definition_id,
                MetricAggregate.series_key == target.series_key,
                MetricAggregate.geography == "GB",
                MetricAggregate.aggregate_kind == AGGREGATE_KIND,
                MetricAggregate.period_start >= _local_midnight_utc(chunk.output_start),
                MetricAggregate.period_start < _local_midnight_utc(chunk.output_end),
                MetricAggregate.methodology_version == DAILY_AGGREGATE_VERSION,
            )
        )
    ).scalars().all()
    baseline_rows = (
        await session.execute(
            select(ComparisonBaseline).where(
                ComparisonBaseline.metric_id == definition_id,
                ComparisonBaseline.series_key == target.series_key,
                ComparisonBaseline.geography == "GB",
                ComparisonBaseline.baseline_kind == BASELINE_KIND,
                ComparisonBaseline.reference_start
                >= _local_midnight_utc(chunk.output_start),
                ComparisonBaseline.reference_start < _local_midnight_utc(chunk.output_end),
                ComparisonBaseline.methodology_version == HISTORY_METHODOLOGY_VERSION,
            )
        )
    ).scalars().all()
    coverage_latest = _latest_by(coverage_rows, lambda row: row.settlement_date)
    aggregate_latest = _latest_by(
        aggregate_rows, lambda row: _aware_utc(row.period_start, "period_start")
    )
    baseline_latest = _latest_by(
        baseline_rows, lambda row: _aware_utc(row.reference_start, "reference_start")
    )
    inserted = 0
    unchanged = 0
    watermark = materialized.source_watermark_at

    for write in materialized.coverage:
        revision = _next_revision(coverage_latest.get(write.settlement_date), write.content_sha256)
        if revision is None:
            unchanged += 1
            continue
        session.add(
            ObservationCoverageDaily(
                id=_row_id("coverage", target.key, write.settlement_date.isoformat(), revision),
                metric_id=definition_id,
                series_key=target.series_key,
                geography="GB",
                settlement_date=write.settlement_date,
                expected_interval_count=write.expected_interval_count,
                observed_interval_count=write.observed_interval_count,
                duplicate_interval_count=write.duplicate_interval_count,
                source_record_count=write.source_record_count,
                coverage_fraction=write.coverage_fraction,
                is_sufficient=write.is_sufficient,
                missing_starts=list(write.missing_starts),
                methodology_version=MATERIALIZATION_COVERAGE_VERSION,
                revision=revision,
                content_sha256=write.content_sha256,
                source_watermark_at=watermark,
                attributes=write.attributes,
            )
        )
        inserted += 1

    for write in materialized.aggregates:
        key = write.period_start.astimezone(UTC)
        revision = _next_revision(aggregate_latest.get(key), write.content_sha256)
        if revision is None:
            unchanged += 1
            continue
        session.add(
            MetricAggregate(
                id=_row_id("aggregate", target.key, key.isoformat(), revision),
                metric_id=definition_id,
                series_key=target.series_key,
                geography="GB",
                aggregate_kind=AGGREGATE_KIND,
                period_start=write.period_start,
                period_end=write.period_end,
                value=write.value,
                unit=write.unit,
                sample_count=write.sample_count,
                expected_sample_count=write.expected_sample_count,
                coverage_fraction=write.coverage_fraction,
                status=write.status,
                methodology_version=DAILY_AGGREGATE_VERSION,
                revision=revision,
                content_sha256=write.content_sha256,
                source_watermark_at=watermark,
                attributes=write.attributes,
            )
        )
        inserted += 1

    for write in materialized.baselines:
        key = write.reference_start.astimezone(UTC)
        revision = _next_revision(baseline_latest.get(key), write.content_sha256)
        if revision is None:
            unchanged += 1
            continue
        session.add(
            ComparisonBaseline(
                id=_row_id("baseline", target.key, key.isoformat(), revision),
                metric_id=definition_id,
                series_key=target.series_key,
                geography="GB",
                baseline_kind=BASELINE_KIND,
                reference_start=write.reference_start,
                window_start=write.window_start,
                window_end=write.window_end,
                median=write.median,
                first_quartile=write.first_quartile,
                third_quartile=write.third_quartile,
                sample_count=write.sample_count,
                expected_sample_count=write.expected_sample_count,
                coverage_fraction=write.coverage_fraction,
                status=write.status,
                methodology_version=HISTORY_METHODOLOGY_VERSION,
                revision=revision,
                content_sha256=write.content_sha256,
                source_watermark_at=watermark,
            )
        )
        inserted += 1
    return inserted, unchanged


def _latest_by(rows: Sequence[Any], key: Callable[[Any], Any]) -> dict[Any, Any]:
    latest: dict[Any, Any] = {}
    for row in rows:
        identity = key(row)
        current = latest.get(identity)
        if current is None or row.revision > current.revision:
            latest[identity] = row
    return latest


def _next_revision(latest: Any | None, content_sha256: str) -> int | None:
    if latest is None:
        return 0
    if latest.content_sha256 == content_sha256:
        return None
    return int(latest.revision) + 1


def _validate_materialized_identity(
    target: MaterializationTarget,
    series: MetricSeries,
) -> None:
    identity = series.identity
    expected_metric_id = target.metric.value
    if target.selector is not None:
        expected_metric_id += f".{target.selector.casefold()}"
    expected = {
        "metric_id": expected_metric_id,
        "geography": "GB",
        "unit": target.definition.unit,
        "fact_class": target.definition.classification.value,
        "source_id": target.source_id,
        "methodology_version": target.definition.methodology_version,
    }
    mismatches = tuple(
        name for name, value in expected.items() if getattr(identity, name) != value
    )
    if mismatches:
        raise ValueError("normalized series identity mismatch: " + ", ".join(mismatches))


def _source_watermark(raw: RawMetricSeries) -> datetime | None:
    values = tuple(
        observation.retrieved_at.astimezone(UTC)
        for observation in raw.observations
        if observation.retrieved_at is not None
    )
    return max(values) if values else None


def _definition_row_id(target: MaterializationTarget) -> str:
    definition = target.definition
    identity = "\x1f".join(
        (definition.metric_id, IDENTITY_VERSION, definition.methodology_version)
    )
    return f"metric-definition:{hashlib.sha256(identity.encode()).hexdigest()}"


def _row_id(kind: str, target_key: str, identity: str, revision: int) -> uuid.UUID:
    return uuid.uuid5(_ROW_NAMESPACE, f"{kind}\x1f{target_key}\x1f{identity}\x1f{revision}")


def _run_id(job_key: str) -> uuid.UUID:
    return uuid.uuid5(_RUN_NAMESPACE, job_key)


def _checksum(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _local_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=LONDON).astimezone(UTC)


def _days(start: date, end: date) -> Iterator[date]:
    day = start
    while day < end:
        yield day
        day += timedelta(days=1)


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="50hz-history-materialize",
        description=(
            "Materialize bounded, completed Europe/London settlement days. "
            "--end is exclusive."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        help=(
            f"number of completed settlement days (1-{MAX_MATERIALIZATION_DAYS}); "
            f"default {DEFAULT_MATERIALIZATION_DAYS}"
        ),
    )
    parser.add_argument("--start", type=_date_argument, help="first date (YYYY-MM-DD)")
    parser.add_argument(
        "--end", type=_date_argument, help="exclusive date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--metric",
        action="append",
        choices=[metric.value for metric in MATERIALIZABLE_METRICS],
        help="materializable metric family; repeat to select more (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print chunk checkpoints without database reads or writes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-evaluate completed chunks and append only changed derived revisions",
    )
    parser.add_argument(
        "--refresh-latest",
        action="store_true",
        help=(
            "bounded production refresh of only the last completed London day; "
            "implies --force and a 28-day read-only baseline lookback"
        ),
    )
    return parser


def request_from_args(args: argparse.Namespace, *, today: date) -> MaterializationRequest:
    if args.refresh_latest:
        if args.days is not None or args.start is not None or args.end is not None:
            raise ValueError(
                "--refresh-latest cannot be combined with --days, --start, or --end"
            )
        date_range = MaterializationDateRange(
            start=today - timedelta(days=1),
            end=today,
        )
    else:
        date_range = resolve_materialization_date_range(
            days=args.days,
            start=args.start,
            end=args.end,
            today=today,
        )
    requested = set(args.metric or (metric.value for metric in MATERIALIZABLE_METRICS))
    metrics = tuple(metric for metric in MATERIALIZABLE_METRICS if metric.value in requested)
    return MaterializationRequest(
        date_range=date_range,
        metrics=metrics,
        dry_run=bool(args.dry_run),
        force=bool(args.force or args.refresh_latest),
        refresh_latest=bool(args.refresh_latest),
    )


def render_report(report: MaterializationReport) -> str:
    mode = (
        "DRY RUN"
        if report.request.dry_run
        else "REFRESH LATEST"
        if report.request.refresh_latest
        else "MATERIALIZE"
    )
    counts = report.counts()
    lines = [
        (
            f"{mode}: {report.request.date_range.start.isoformat()} to "
            f"{report.request.date_range.end.isoformat()} (exclusive), "
            f"{report.request.date_range.day_count} settlement days, "
            f"{len(report.request.targets)} explicit series"
        ),
        "chunks: "
        + ", ".join(
            f"{status.value}={counts[status]}"
            for status in MaterializationStatus
            if counts[status]
        ),
        (
            "rule: daily aggregates and rolling baselines are available only at "
            "95% or greater compatible coverage"
        ),
        (
            "corrections: successful checkpoints skip by default; --force compares "
            "source-revision evidence and appends changed derived revisions"
        ),
        (
            "deferred: frequency history and historical forecast vintages are not "
            "materialized because the selected sources do not provide safe equivalents"
        ),
        "status: complete" if report.exit_code == 0 else "status: incomplete",
    ]
    return "\n".join(lines)


async def run_configured_materialization(
    request: MaterializationRequest,
) -> MaterializationReport:
    session_factory = get_session_factory()
    try:
        runner = HistoryMaterializationRunner(
            loader=NormalizedHistoryRepository(session_factory),
            store=PostgresHistoryMaterializationStore(session_factory),
            locks=PostgresAdvisoryLockProvider(session_factory),
        )
        return await runner.run(request)
    finally:
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        request = request_from_args(args, today=datetime.now(LONDON).date())
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    try:
        configured_database_url()
    except DatabaseNotConfiguredError:
        print("DATABASE_URL is required for history materialization", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(run_configured_materialization(request))
    except Exception as exc:
        print(
            f"history materialization could not start ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    print(render_report(report))
    return report.exit_code


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from exc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
