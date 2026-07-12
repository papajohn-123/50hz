"""Bounded, resumable operator job for immutable forecast verification."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import uuid
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import (
    DatabaseNotConfiguredError,
    ForecastVerificationPair,
    ForecastVerificationResult as StoredVerificationResult,
    ForecastVerificationRun,
    dispose_engine,
    get_session_factory,
)
from app.db.session import configured_database_url
from app.forecast_verification.core import (
    VERIFICATION_METHODOLOGY_VERSION,
    VERIFICATION_REGISTRY_VERSION,
    VERIFICATION_TARGETS,
    VerificationBundle,
    VerificationMetric,
    VerificationPair,
    VerificationResult,
    VerificationTarget,
    verify_forecasts,
)
from app.forecast_verification.repository import (
    MAX_FORECAST_INPUT_ROWS,
    MAX_OUTTURN_INPUT_ROWS,
    ForecastVerificationInputRepository,
)
from app.persistence.locks import PostgresAdvisoryLockProvider


LONDON = ZoneInfo("Europe/London")
DEFAULT_VERIFICATION_DAYS = 28
MAX_VERIFICATION_DAYS = 31
REFRESH_LATEST_DAYS = 7
MAX_LONDON_HALF_HOURS_PER_DAY = 50
MAX_CAPTURE_VINTAGES_PER_VALID_TIME = 96
PAIR_INSERT_BATCH_SIZE = 400


@dataclass(frozen=True, slots=True)
class VerificationDateRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if isinstance(self.start, datetime) or isinstance(self.end, datetime):
            raise TypeError("verification bounds must be dates")
        if self.end <= self.start:
            raise ValueError("verification end must be after start")
        if self.day_count > MAX_VERIFICATION_DAYS:
            raise ValueError("verification windows cannot exceed 31 completed days")

    @property
    def day_count(self) -> int:
        return (self.end - self.start).days

    @property
    def start_utc(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=LONDON).astimezone(UTC)

    @property
    def end_utc(self) -> datetime:
        return datetime.combine(self.end, time.min, tzinfo=LONDON).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    date_range: VerificationDateRange
    metrics: tuple[VerificationMetric, ...] = tuple(VerificationMetric)
    dry_run: bool = False
    force: bool = False
    refresh_latest: bool = False

    def __post_init__(self) -> None:
        if not self.metrics or len(self.metrics) != len(set(self.metrics)):
            raise ValueError("verification metrics must be non-empty and unique")
        if self.refresh_latest and not self.force:
            raise ValueError("latest refresh must force checkpoint re-evaluation")

    @property
    def targets(self) -> tuple[VerificationTarget, ...]:
        selected = set(self.metrics)
        return tuple(target for target in VERIFICATION_TARGETS if target.metric in selected)


class RunStatus(StrEnum):
    PLANNED = "planned"
    SUCCEEDED = "succeeded"
    SKIPPED_COMPLETED = "skipped_completed"
    SKIPPED_LOCKED = "skipped_locked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PersistenceOutcome:
    pairs_inserted: int
    pairs_unchanged: int
    results_inserted: int
    results_unchanged: int


@dataclass(frozen=True, slots=True)
class RunOutcome:
    metric: VerificationMetric
    job_key: str
    status: RunStatus
    persistence: PersistenceOutcome | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationReport:
    request: VerificationRequest
    outcomes: tuple[RunOutcome, ...]

    @property
    def exit_code(self) -> int:
        return 1 if any(item.status is RunStatus.FAILED for item in self.outcomes) else 0


class InputRepository(Protocol):
    async def load(
        self,
        target: VerificationTarget,
        *,
        window_start: datetime,
        window_end: datetime,
    ): ...


class Store(Protocol):
    async def succeeded(self, job_key: str) -> bool: ...

    async def record_started(
        self,
        *,
        job_key: str,
        target: VerificationTarget,
        date_range: VerificationDateRange,
        started_at: datetime,
    ) -> None: ...

    async def persist_success(
        self,
        *,
        job_key: str,
        bundle: VerificationBundle,
        completed_at: datetime,
    ) -> PersistenceOutcome: ...

    async def record_failure(
        self,
        *,
        job_key: str,
        failed_at: datetime,
        error_type: str,
    ) -> None: ...


class LockProvider(Protocol):
    def acquire(self, lock_name: str): ...


class ForecastVerificationRunner:
    def __init__(
        self,
        *,
        loader: InputRepository,
        store: Store,
        locks: LockProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.loader = loader
        self.store = store
        self.locks = locks
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run(self, request: VerificationRequest) -> VerificationReport:
        if request.dry_run:
            return VerificationReport(
                request=request,
                outcomes=tuple(
                    RunOutcome(
                        metric=target.metric,
                        job_key=verification_job_key(target, request.date_range),
                        status=RunStatus.PLANNED,
                    )
                    for target in request.targets
                ),
            )
        global_lock = f"50hz:forecast:verify:{VERIFICATION_REGISTRY_VERSION}"
        async with self.locks.acquire(global_lock) as acquired:
            if not acquired:
                return VerificationReport(
                    request=request,
                    outcomes=tuple(
                        RunOutcome(
                            metric=target.metric,
                            job_key=verification_job_key(target, request.date_range),
                            status=RunStatus.SKIPPED_LOCKED,
                        )
                        for target in request.targets
                    ),
                )
            outcomes = []
            for target in request.targets:
                outcomes.append(await self._run_target(request, target))
        return VerificationReport(request=request, outcomes=tuple(outcomes))

    async def _run_target(
        self,
        request: VerificationRequest,
        target: VerificationTarget,
    ) -> RunOutcome:
        job_key = verification_job_key(target, request.date_range)
        started = False
        try:
            if not request.force and await self.store.succeeded(job_key):
                return RunOutcome(target.metric, job_key, RunStatus.SKIPPED_COMPLETED)
            lock_names = tuple(
                sorted(
                    {
                        f"50hz:ingest:{target.forecast_ingestion_lock}",
                        f"50hz:ingest:{target.outturn_ingestion_lock}",
                    }
                )
            )
            async with AsyncExitStack() as stack:
                for lock_name in lock_names:
                    acquired = await stack.enter_async_context(self.locks.acquire(lock_name))
                    if not acquired:
                        return RunOutcome(target.metric, job_key, RunStatus.SKIPPED_LOCKED)
                await self.store.record_started(
                    job_key=job_key,
                    target=target,
                    date_range=request.date_range,
                    started_at=_aware(self.clock()),
                )
                started = True
                forecasts, outturns = await self.loader.load(
                    target,
                    window_start=request.date_range.start_utc,
                    window_end=request.date_range.end_utc,
                )
            bundle = verify_forecasts(
                target,
                forecasts=forecasts,
                outturns=outturns,
                window_start=request.date_range.start_utc,
                window_end=request.date_range.end_utc,
            )
            persistence = await self.store.persist_success(
                job_key=job_key,
                bundle=bundle,
                completed_at=_aware(self.clock()),
            )
            return RunOutcome(
                target.metric,
                job_key,
                RunStatus.SUCCEEDED,
                persistence=persistence,
            )
        except Exception as exc:
            if started:
                try:
                    await self.store.record_failure(
                        job_key=job_key,
                        failed_at=_aware(self.clock()),
                        error_type=type(exc).__name__,
                    )
                except Exception:
                    pass
            return RunOutcome(
                target.metric,
                job_key,
                RunStatus.FAILED,
                error_type=type(exc).__name__,
            )


class PostgresForecastVerificationStore:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self.session_factory = session_factory

    async def succeeded(self, job_key: str) -> bool:
        async with self.session_factory() as session:
            status = await session.scalar(
                select(ForecastVerificationRun.status).where(
                    ForecastVerificationRun.job_key == job_key
                )
            )
        return status == RunStatus.SUCCEEDED.value

    async def record_started(
        self,
        *,
        job_key: str,
        target: VerificationTarget,
        date_range: VerificationDateRange,
        started_at: datetime,
    ) -> None:
        values = {
            "id": uuid.uuid5(uuid.NAMESPACE_URL, f"50hz:{job_key}"),
            "job_key": job_key,
            "metric_id": target.metric.value,
            "registry_version": VERIFICATION_REGISTRY_VERSION,
            "window_start_date": date_range.start,
            "window_end_date": date_range.end,
            "status": "running",
            "attempt_count": 1,
            "pairs_written": 0,
            "results_written": 0,
            "result_checksum": None,
            "started_at": started_at,
            "completed_at": None,
            "error_type": None,
        }
        statement = pg_insert(ForecastVerificationRun).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[ForecastVerificationRun.job_key],
            set_={
                "registry_version": statement.excluded.registry_version,
                "status": "running",
                "attempt_count": ForecastVerificationRun.attempt_count + 1,
                "pairs_written": 0,
                "results_written": 0,
                "result_checksum": None,
                "started_at": statement.excluded.started_at,
                "completed_at": None,
                "error_type": None,
            },
        )
        async with self.session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def persist_success(
        self,
        *,
        job_key: str,
        bundle: VerificationBundle,
        completed_at: datetime,
    ) -> PersistenceOutcome:
        async with self.session_factory() as session:
            pair_inserted, pair_unchanged = await _append_pairs(session, bundle)
            result_inserted, result_unchanged = await _append_results(
                session, bundle, computed_at=completed_at
            )
            await session.execute(
                update(ForecastVerificationRun)
                .where(ForecastVerificationRun.job_key == job_key)
                .values(
                    status="succeeded",
                    pairs_written=pair_inserted,
                    results_written=result_inserted,
                    result_checksum=bundle.result_checksum,
                    completed_at=completed_at,
                    error_type=None,
                )
            )
            await session.commit()
        return PersistenceOutcome(
            pairs_inserted=pair_inserted,
            pairs_unchanged=pair_unchanged,
            results_inserted=result_inserted,
            results_unchanged=result_unchanged,
        )

    async def record_failure(
        self,
        *,
        job_key: str,
        failed_at: datetime,
        error_type: str,
    ) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(ForecastVerificationRun)
                .where(ForecastVerificationRun.job_key == job_key)
                .values(
                    status="failed",
                    pairs_written=0,
                    results_written=0,
                    result_checksum=None,
                    completed_at=failed_at,
                    error_type=(error_type or "OperationFailed")[:120],
                )
            )
            await session.commit()


async def _append_pairs(
    session: AsyncSession,
    bundle: VerificationBundle,
) -> tuple[int, int]:
    model = ForecastVerificationPair
    window_start = min((pair.forecast.valid_from for pair in bundle.pairs), default=None)
    window_end = max((pair.forecast.valid_from for pair in bundle.pairs), default=None)
    existing_rows = []
    if window_start is not None and window_end is not None:
        existing_rows = list(
            (
                await session.execute(
                    select(model)
                    .where(
                        model.metric_id == bundle.target.metric.value,
                        model.verification_methodology_version
                        == VERIFICATION_METHODOLOGY_VERSION,
                        model.valid_from >= window_start,
                        model.valid_from <= window_end,
                    )
                    .order_by(model.valid_from, model.horizon_bucket, model.revision)
                )
            )
            .scalars()
            .all()
        )
    latest = {}
    for row in existing_rows:
        key = (
            row.horizon_bucket,
            row.valid_from.astimezone(UTC),
            row.forecast_vintage_at.astimezone(UTC),
        )
        if key not in latest or row.revision > latest[key].revision:
            latest[key] = row
    values = []
    inserted = 0
    unchanged = 0
    for pair in bundle.pairs:
        key = (
            pair.horizon.value,
            pair.forecast.valid_from.astimezone(UTC),
            pair.forecast_vintage_at,
        )
        existing = latest.get(key)
        if existing is not None and existing.content_sha256 == pair.content_sha256:
            unchanged += 1
            continue
        revision = 0 if existing is None else existing.revision + 1
        values.append(_pair_values(pair, revision=revision))
        if len(values) == PAIR_INSERT_BATCH_SIZE:
            inserted += await _insert_pair_batch(session, values)
            values = []
    if values:
        inserted += await _insert_pair_batch(session, values)
    return inserted, unchanged


def _pair_values(
    pair: VerificationPair,
    *,
    revision: int,
) -> dict[str, object]:
    if revision < 0:
        raise ValueError("forecast pair revision must be non-negative")
    return {
        "id": uuid.uuid4(),
        "metric_id": pair.target.metric.value,
        "horizon_bucket": pair.horizon.value,
        "valid_from": pair.forecast.valid_from,
        "forecast_source_id": pair.target.forecast_source_id,
        "outturn_source_id": pair.target.outturn_source_id,
        "forecast_observation_id": pair.forecast.observation_id,
        "outturn_observation_id": pair.outturn.observation_id,
        "forecast_vintage_at": pair.forecast_vintage_at,
        "forecast_source_issued_at": pair.forecast_source_issued_at,
        "forecast_captured_at": pair.forecast.captured_at,
        "issue_time_basis": pair.target.issue_time_basis,
        "effective_vintage_time_basis": pair.target.effective_vintage_time_basis,
        "forecast_value": pair.forecast.value,
        "outturn_value": pair.outturn.value,
        "signed_error": pair.signed_error,
        "absolute_error": pair.absolute_error,
        "unit": pair.target.unit,
        "forecast_revision": pair.forecast.revision,
        "outturn_revision": pair.outturn.revision,
        "forecast_methodology_version": pair.target.forecast_methodology_version,
        "outturn_methodology_version": pair.target.outturn_methodology_version,
        "verification_methodology_version": VERIFICATION_METHODOLOGY_VERSION,
        "registry_version": VERIFICATION_REGISTRY_VERSION,
        "revision": revision,
        "content_sha256": pair.content_sha256,
    }


async def _insert_pair_batch(
    session: AsyncSession,
    values: list[dict[str, object]],
) -> int:
    if not 1 <= len(values) <= PAIR_INSERT_BATCH_SIZE:
        raise ValueError("forecast pair insert batch is outside its reviewed bound")
    model = ForecastVerificationPair
    statement = (
        pg_insert(model)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_forecast_pair_identity_revision")
        .returning(model.id)
    )
    inserted = len((await session.execute(statement)).scalars().all())
    if inserted != len(values):
        raise RuntimeError("forecast pair revision conflict; retry under the source lock")
    return inserted


async def _append_results(
    session: AsyncSession,
    bundle: VerificationBundle,
    *,
    computed_at: datetime,
) -> tuple[int, int]:
    model = StoredVerificationResult
    first = bundle.results[0]
    existing_rows = list(
        (
            await session.execute(
                select(model)
                .where(
                    model.metric_id == bundle.target.metric.value,
                    model.window_start == first.window_start,
                    model.window_end == first.window_end,
                    model.verification_methodology_version
                    == VERIFICATION_METHODOLOGY_VERSION,
                )
                .order_by(model.horizon_bucket, model.revision)
            )
        )
        .scalars()
        .all()
    )
    latest = {}
    for row in existing_rows:
        if row.horizon_bucket not in latest or row.revision > latest[row.horizon_bucket].revision:
            latest[row.horizon_bucket] = row
    values = []
    unchanged = 0
    for result in bundle.results:
        existing = latest.get(result.horizon.value)
        if existing is not None and existing.evidence_checksum == result.evidence_checksum:
            unchanged += 1
            continue
        revision = 0 if existing is None else existing.revision + 1
        values.append(
            _result_values(
                result,
                revision=revision,
                computed_at=computed_at,
            )
        )
    inserted = 0
    if values:
        statement = (
            pg_insert(model)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_forecast_result_identity_revision")
            .returning(model.id)
        )
        inserted = len((await session.execute(statement)).scalars().all())
        if inserted != len(values):
            raise RuntimeError("forecast result revision conflict; retry under the global lock")
    return inserted, unchanged


def _result_values(
    result: VerificationResult,
    *,
    revision: int,
    computed_at: datetime,
) -> dict[str, object]:
    if revision < 0:
        raise ValueError("forecast result revision must be non-negative")
    return {
        "id": uuid.uuid4(),
        "metric_id": result.target.metric.value,
        "horizon_bucket": result.horizon.value,
        "window_start": result.window_start,
        "window_end": result.window_end,
        "status": result.status,
        "reason": result.reason,
        "mae": result.mae,
        "bias": result.bias,
        "wape_percent": result.wape_percent,
        "verified_sample_count": result.verified_sample_count,
        "expected_sample_count": result.expected_sample_count,
        "coverage_fraction": result.coverage_fraction,
        "unit": result.target.unit,
        "forecast_source_id": result.target.forecast_source_id,
        "outturn_source_id": result.target.outturn_source_id,
        "issue_time_basis": result.target.issue_time_basis,
        "effective_vintage_time_basis": result.target.effective_vintage_time_basis,
        "forecast_methodology_version": result.target.forecast_methodology_version,
        "outturn_methodology_version": result.target.outturn_methodology_version,
        "verification_methodology_version": VERIFICATION_METHODOLOGY_VERSION,
        "registry_version": VERIFICATION_REGISTRY_VERSION,
        "evidence_checksum": result.evidence_checksum,
        "source_watermark_at": result.source_watermark_at,
        "revision": revision,
        "computed_at": computed_at,
    }


def verification_job_key(
    target: VerificationTarget,
    date_range: VerificationDateRange,
) -> str:
    identity = (
        f"{VERIFICATION_REGISTRY_VERSION}:{VERIFICATION_METHODOLOGY_VERSION}:"
        f"{target.metric.value}:{date_range.start.isoformat()}:{date_range.end.isoformat()}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"forecast-verify:{target.metric.value}:{digest}"


def resolve_date_range(
    *,
    days: int | None,
    start: date | None,
    end: date | None,
    today: date,
) -> VerificationDateRange:
    if start is not None or end is not None:
        if days is not None:
            raise ValueError("--days cannot be combined with --start or --end")
        if start is None or end is None:
            raise ValueError("--start and --end must be supplied together")
        resolved = VerificationDateRange(start, end)
    else:
        count = days if days is not None else DEFAULT_VERIFICATION_DAYS
        if not 1 <= count <= MAX_VERIFICATION_DAYS:
            raise ValueError("--days must be between 1 and 31")
        resolved = VerificationDateRange(today - timedelta(days=count), today)
    if resolved.end > today:
        raise ValueError("verification end cannot be later than today's London date")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="50hz-forecast-verify",
        description="Verify stored forecast vintages against compatible normalized outturns.",
    )
    parser.add_argument("--days", type=int)
    parser.add_argument("--start", type=_date_argument)
    parser.add_argument("--end", type=_date_argument)
    parser.add_argument(
        "--metric",
        action="append",
        choices=[metric.value for metric in VerificationMetric],
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--refresh-latest",
        action="store_true",
        help="Force the latest seven completed London days; suitable for a daily cron.",
    )
    return parser


def conservative_carbon_forecast_row_bound(days: int) -> int:
    """Bound 30-minute captures with a 48-hour (96-point) forecast horizon."""

    if not 1 <= days <= 366:
        raise ValueError("capacity estimate days must be between 1 and 366")
    return (
        days
        * MAX_LONDON_HALF_HOURS_PER_DAY
        * MAX_CAPTURE_VINTAGES_PER_VALID_TIME
    )


def request_from_args(args: argparse.Namespace, *, today: date) -> VerificationRequest:
    if args.refresh_latest:
        if args.days is not None or args.start is not None or args.end is not None:
            raise ValueError(
                "--refresh-latest cannot be combined with --days, --start, or --end"
            )
        date_range = VerificationDateRange(
            today - timedelta(days=REFRESH_LATEST_DAYS), today
        )
    else:
        date_range = resolve_date_range(
            days=args.days,
            start=args.start,
            end=args.end,
            today=today,
        )
    requested = set(args.metric or [metric.value for metric in VerificationMetric])
    metrics = tuple(metric for metric in VerificationMetric if metric.value in requested)
    return VerificationRequest(
        date_range=date_range,
        metrics=metrics,
        dry_run=bool(args.dry_run),
        force=bool(args.force or args.refresh_latest),
        refresh_latest=bool(args.refresh_latest),
    )


def render_report(report: VerificationReport) -> str:
    mode = (
        "DRY RUN"
        if report.request.dry_run
        else "REFRESH"
        if report.request.refresh_latest
        else "VERIFY"
    )
    counts = {status: 0 for status in RunStatus}
    for outcome in report.outcomes:
        counts[outcome.status] += 1
    return "\n".join(
        (
            f"{mode}: {report.request.date_range.start.isoformat()} to "
            f"{report.request.date_range.end.isoformat()} (exclusive), "
            f"{report.request.date_range.day_count} completed London days",
            "targets: " + ", ".join(metric.value for metric in report.request.metrics),
            "runs: " + ", ".join(
                f"{status.value}={count}" for status, count in counts.items() if count
            ),
            "pairing: exact stored vintages and exact reviewed outturn timestamps only",
            (
                f"bounds: at most {MAX_VERIFICATION_DAYS} days, "
                f"{MAX_FORECAST_INPUT_ROWS} forecast rows and "
                f"{MAX_OUTTURN_INPUT_ROWS} outturn rows per target"
            ),
            (
                "corrections: changed evidence appends pair and aggregate revisions; "
                "prior evidence is retained"
            ),
            "display: at least 100 verified samples and 90% compatible coverage",
            "status: complete" if report.exit_code == 0 else "status: incomplete",
        )
    )


async def run_configured(request: VerificationRequest) -> VerificationReport:
    session_factory = get_session_factory()
    try:
        runner = ForecastVerificationRunner(
            loader=ForecastVerificationInputRepository(session_factory),
            store=PostgresForecastVerificationStore(session_factory),
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
        print("DATABASE_URL is required for forecast verification", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(run_configured(request))
    except Exception as exc:
        print(
            f"forecast verification could not start ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    print(render_report(report))
    return report.exit_code


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("verification clock must include a timezone")
    return value.astimezone(UTC)


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from exc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
