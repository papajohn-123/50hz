"""Explicit, bounded historical ingestion for the 50Hz history foundation.

This module is deliberately not imported by the API/worker runtime.  Run it as
an operator command only, for example::

    python -m app.history.backfill --days 90 --dry-run
    python -m app.history.backfill --days 90

Date arguments are Europe/London settlement dates and ``--end`` is exclusive.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db.session import (
    DatabaseNotConfiguredError,
    configured_database_url,
    dispose_engine,
    get_session_factory,
)
from app.domain.settlement import settlement_day_bounds_utc
from app.persistence.ingestion import PostgresIngestionRepository
from app.persistence.locks import PostgresAdvisoryLockProvider
from app.sources.client import AsyncJSONClient
from app.sources.elexon import (
    FuelInstGenerationAdapter,
    InitialDemandAdapter,
    InterconnectorFlowAdapter,
    NationalDemandForecastAdapter,
    RemitUnavailabilityAdapter,
    SystemWarningsAdapter,
    WindGenerationForecastAdapter,
)
from app.sources.neso_carbon import NationalCarbonHistoryAdapter
from app.sources.types import (
    AdapterResult,
    CarbonIntensityRecord,
    DemandForecastRecord,
    DemandRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
    ObservationWindow,
    RemitUnavailabilityRecord,
    SourceAdapter,
    SystemWarningRecord,
    WindForecastRecord,
    as_utc,
)
from app.worker.contracts import (
    AdvisoryLockProvider,
    IngestionRepository,
    PersistOutcome,
)


LONDON = ZoneInfo("Europe/London")
MAX_BACKFILL_DAYS = 95
DEFAULT_BACKFILL_DAYS = 90
BACKFILL_JOB_VERSION = "history-backfill-v1"
NATIONAL_CARBON_EARLIEST_DATE = date(2017, 9, 26)


class BackfillSource(StrEnum):
    GENERATION = "elexon.fuelinst"
    DEMAND = "elexon.indo"
    INTERCONNECTORS = "elexon.interconnectors"
    DEMAND_FORECASTS = "elexon.ndf"
    WIND_FORECASTS = "elexon.windfor"
    REMIT_REVISIONS = "elexon.remit.unavailability"
    SYSTEM_WARNINGS = "elexon.syswarn"
    NATIONAL_CARBON = "neso.carbon.national.history"


SOURCE_ORDER = tuple(BackfillSource)


@dataclass(frozen=True, slots=True)
class BackfillDateRange:
    """A bounded half-open range of GB settlement dates."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if isinstance(self.start, datetime) or not isinstance(self.start, date):
            raise TypeError("start must be a date")
        if isinstance(self.end, datetime) or not isinstance(self.end, date):
            raise TypeError("end must be a date")
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.day_count > MAX_BACKFILL_DAYS:
            raise ValueError(f"backfill ranges cannot exceed {MAX_BACKFILL_DAYS} days")

    @property
    def day_count(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    date_range: BackfillDateRange
    sources: tuple[BackfillSource, ...] = SOURCE_ORDER
    dry_run: bool = False
    force: bool = False

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("at least one backfill source is required")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("backfill sources must be unique")
        if any(not isinstance(source, BackfillSource) for source in self.sources):
            raise TypeError("sources must use the BackfillSource allow-list")
        if (
            BackfillSource.NATIONAL_CARBON in self.sources
            and self.date_range.start < NATIONAL_CARBON_EARLIEST_DATE
        ):
            raise ValueError(
                "national carbon history is unavailable before "
                f"{NATIONAL_CARBON_EARLIEST_DATE.isoformat()}"
            )


@dataclass(frozen=True, slots=True)
class BackfillSourceSpec:
    source: BackfillSource
    adapter: SourceAdapter[Any]
    chunk_days: int
    lock_source_id: str
    split_at_year_boundary: bool = False
    query_end_overlap: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if self.chunk_days < 1 or self.chunk_days > 30:
            raise ValueError("source chunk days must be between 1 and 30")
        if not self.lock_source_id:
            raise ValueError("lock source ID cannot be empty")
        if not timedelta(0) <= self.query_end_overlap <= timedelta(hours=2):
            raise ValueError("source query end overlap must be between zero and two hours")

    @property
    def lock_name(self) -> str:
        # This is the same namespace used by PollSchedule.lock_name.  Carbon
        # history intentionally locks against the live national carbon source.
        return f"50hz:ingest:{self.lock_source_id}"


class BackfillStatus(StrEnum):
    PLANNED = "planned"
    SUCCEEDED = "succeeded"
    SKIPPED_COMPLETED = "skipped_completed"
    SKIPPED_LOCKED = "skipped_locked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BackfillOutcome:
    source: BackfillSource
    job_id: str
    window: ObservationWindow
    status: BackfillStatus
    record_count: int = 0
    persistence: PersistOutcome | None = None
    error_type: str | None = None
    failure_checkpoint_recorded: bool | None = None


@dataclass(frozen=True, slots=True)
class BackfillReport:
    request: BackfillRequest
    outcomes: tuple[BackfillOutcome, ...]

    @property
    def exit_code(self) -> int:
        incomplete = {BackfillStatus.FAILED, BackfillStatus.SKIPPED_LOCKED}
        return 1 if any(outcome.status in incomplete for outcome in self.outcomes) else 0

    def counts(self, source: BackfillSource | None = None) -> Counter[BackfillStatus]:
        return Counter(
            outcome.status
            for outcome in self.outcomes
            if source is None or outcome.source is source
        )


def resolve_backfill_date_range(
    *,
    days: int | None,
    start: date | None,
    end: date | None,
    today: date,
) -> BackfillDateRange:
    """Resolve CLI range forms without ever including an incomplete local day."""

    if isinstance(today, datetime) or not isinstance(today, date):
        raise TypeError("today must be a date")
    if days is not None and start is not None:
        raise ValueError("use either --days or --start, not both")
    if days is None and start is None:
        raise ValueError("provide --days or both --start and --end")

    resolved_end = end or today
    if resolved_end > today:
        raise ValueError("end cannot be later than today's London settlement date")

    if days is not None:
        if isinstance(days, bool) or not isinstance(days, int):
            raise TypeError("days must be an integer")
        if not 1 <= days <= MAX_BACKFILL_DAYS:
            raise ValueError(f"days must be between 1 and {MAX_BACKFILL_DAYS}")
        resolved_start = resolved_end - timedelta(days=days)
    else:
        if end is None:
            raise ValueError("--start requires an exclusive --end")
        assert start is not None
        resolved_start = start

    return BackfillDateRange(start=resolved_start, end=resolved_end)


def plan_settlement_chunks(
    date_range: BackfillDateRange,
    *,
    chunk_days: int,
    split_at_year_boundary: bool = False,
) -> tuple[ObservationWindow, ...]:
    """Create deterministic chunks on London settlement-day boundaries."""

    if isinstance(chunk_days, bool) or not isinstance(chunk_days, int):
        raise TypeError("chunk_days must be an integer")
    if chunk_days < 1 or chunk_days > 30:
        raise ValueError("chunk_days must be between 1 and 30")

    chunks: list[ObservationWindow] = []
    cursor = date_range.start
    while cursor < date_range.end:
        chunk_end = min(cursor + timedelta(days=chunk_days), date_range.end)
        if split_at_year_boundary:
            next_year = date(cursor.year + 1, 1, 1)
            chunk_end = min(chunk_end, next_year)
        start_utc, _ = settlement_day_bounds_utc(cursor)
        end_utc, _ = settlement_day_bounds_utc(chunk_end)
        chunks.append(ObservationWindow(start=start_utc, end=end_utc))
        cursor = chunk_end

    if any(
        current.end != following.start
        for current, following in zip(chunks, chunks[1:], strict=False)
    ):
        raise AssertionError("planned backfill chunks must be contiguous")
    return tuple(chunks)


def backfill_job_id(source: BackfillSource, window: ObservationWindow) -> str:
    """Stable per-source/per-chunk checkpoint identity, separate from live jobs."""

    start = window.start.strftime("%Y%m%dT%H%MZ")
    end = window.end.strftime("%Y%m%dT%H%MZ")
    return f"{BACKFILL_JOB_VERSION}:{source.value}:{start}:{end}"


class HistoryBackfillRunner:
    """Run independent source chunks while retaining failures in the summary."""

    def __init__(
        self,
        *,
        sources: Sequence[BackfillSourceSpec],
        repository: IngestionRepository,
        locks: AdvisoryLockProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sources = {spec.source: spec for spec in sources}
        if set(self._sources) != set(SOURCE_ORDER):
            raise ValueError("runner sources must exactly match the backfill allow-list")
        self._repository = repository
        self._locks = locks
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self, request: BackfillRequest) -> BackfillReport:
        outcomes: list[BackfillOutcome] = []
        for source in request.sources:
            spec = self._sources[source]
            chunks = plan_settlement_chunks(
                request.date_range,
                chunk_days=spec.chunk_days,
                split_at_year_boundary=spec.split_at_year_boundary,
            )
            for window in chunks:
                outcomes.append(await self._run_chunk(request, spec, window))
        return BackfillReport(request=request, outcomes=tuple(outcomes))

    async def _run_chunk(
        self,
        request: BackfillRequest,
        spec: BackfillSourceSpec,
        window: ObservationWindow,
    ) -> BackfillOutcome:
        job_id = backfill_job_id(spec.source, window)
        if request.dry_run:
            return BackfillOutcome(
                source=spec.source,
                job_id=job_id,
                window=window,
                status=BackfillStatus.PLANNED,
            )

        if not request.force:
            try:
                checkpoint = await self._repository.get_checkpoint(job_id)
            except Exception as exc:
                return BackfillOutcome(
                    source=spec.source,
                    job_id=job_id,
                    window=window,
                    status=BackfillStatus.FAILED,
                    error_type=type(exc).__name__,
                    failure_checkpoint_recorded=False,
                )
            if (
                checkpoint is not None
                and checkpoint.last_succeeded_at is not None
                and checkpoint.window_end == window.end
            ):
                return BackfillOutcome(
                    source=spec.source,
                    job_id=job_id,
                    window=window,
                    status=BackfillStatus.SKIPPED_COMPLETED,
                )

        attempted_at = as_utc(self._clock(), field_name="clock")
        try:
            async with self._locks.acquire(spec.lock_name) as acquired:
                if not acquired:
                    return BackfillOutcome(
                        source=spec.source,
                        job_id=job_id,
                        window=window,
                        status=BackfillStatus.SKIPPED_LOCKED,
                    )
                try:
                    query_window = ObservationWindow(
                        start=window.start,
                        end=window.end + spec.query_end_overlap,
                    )
                    result = await spec.adapter.fetch(query_window)
                    if query_window != window:
                        result = replace(
                            result,
                            window=window,
                            metadata={
                                **result.metadata,
                                "backfillQueryWindowStart": query_window.start.isoformat(),
                                "backfillQueryWindowEnd": query_window.end.isoformat(),
                            },
                            warnings=result.warnings
                            + (
                                "source query included a bounded publication-lag "
                                "overlap; normalized records remain target-window bounded",
                            ),
                        )
                    result = _retain_records_in_window(result)
                    persistence = await self._repository.persist_success(
                        job_id=job_id,
                        result=result,
                        attempted_at=attempted_at,
                        completed_at=as_utc(self._clock(), field_name="clock"),
                    )
                except Exception as exc:
                    recorded = await self._record_failure(
                        job_id=job_id,
                        window=window,
                        attempted_at=attempted_at,
                        exc=exc,
                    )
                    return BackfillOutcome(
                        source=spec.source,
                        job_id=job_id,
                        window=window,
                        status=BackfillStatus.FAILED,
                        error_type=type(exc).__name__,
                        failure_checkpoint_recorded=recorded,
                    )
        except Exception as exc:
            # Lock acquisition itself can fail when PostgreSQL is unavailable.
            # Preserve the failure in the summary and continue other chunks.
            return BackfillOutcome(
                source=spec.source,
                job_id=job_id,
                window=window,
                status=BackfillStatus.FAILED,
                error_type=type(exc).__name__,
                failure_checkpoint_recorded=False,
            )

        return BackfillOutcome(
            source=spec.source,
            job_id=job_id,
            window=window,
            status=BackfillStatus.SUCCEEDED,
            record_count=len(result.records),
            persistence=persistence,
        )

    async def _record_failure(
        self,
        *,
        job_id: str,
        window: ObservationWindow,
        attempted_at: datetime,
        exc: Exception,
    ) -> bool:
        try:
            await self._repository.record_failure(
                job_id=job_id,
                window=window,
                attempted_at=attempted_at,
                failed_at=as_utc(self._clock(), field_name="clock"),
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
            )
        except Exception:
            return False
        return True


def build_backfill_sources(
    *,
    elexon_client: AsyncJSONClient,
    carbon_client: AsyncJSONClient,
) -> tuple[BackfillSourceSpec, ...]:
    """Build the fixed public-source allow-list and upstream-safe chunk policy."""

    return (
        BackfillSourceSpec(
            source=BackfillSource.GENERATION,
            adapter=FuelInstGenerationAdapter(elexon_client),
            # FUELINST contains many fuels at five-minute cadence.
            chunk_days=1,
            lock_source_id="elexon.fuelinst",
            query_end_overlap=timedelta(minutes=15),
        ),
        BackfillSourceSpec(
            source=BackfillSource.DEMAND,
            adapter=InitialDemandAdapter(elexon_client),
            chunk_days=7,
            lock_source_id="elexon.indo",
            query_end_overlap=timedelta(hours=1),
        ),
        BackfillSourceSpec(
            source=BackfillSource.INTERCONNECTORS,
            adapter=InterconnectorFlowAdapter(elexon_client),
            # This shares FUELINST's volume even though it keeps only links.
            chunk_days=1,
            lock_source_id="elexon.interconnectors",
            query_end_overlap=timedelta(minutes=15),
        ),
        BackfillSourceSpec(
            source=BackfillSource.DEMAND_FORECASTS,
            adapter=NationalDemandForecastAdapter(elexon_client),
            # NDF can contain many revised valid periods per publication.
            chunk_days=1,
            lock_source_id="elexon.ndf",
        ),
        BackfillSourceSpec(
            source=BackfillSource.WIND_FORECASTS,
            adapter=WindGenerationForecastAdapter(elexon_client),
            chunk_days=7,
            lock_source_id="elexon.windfor",
        ),
        BackfillSourceSpec(
            source=BackfillSource.REMIT_REVISIONS,
            adapter=RemitUnavailabilityAdapter(elexon_client),
            # Detail fan-out is batch bounded, but one-day listing chunks keep
            # unusually busy event days isolated and safely resumable.
            chunk_days=1,
            lock_source_id="elexon.remit.unavailability",
        ),
        BackfillSourceSpec(
            source=BackfillSource.SYSTEM_WARNINGS,
            adapter=SystemWarningsAdapter(elexon_client),
            chunk_days=7,
            lock_source_id="elexon.syswarn",
        ),
        BackfillSourceSpec(
            source=BackfillSource.NATIONAL_CARBON,
            adapter=NationalCarbonHistoryAdapter(carbon_client),
            # The public download surface caps ranges at 30 days. Seven local
            # days is deliberately conservative and chunks never span a year.
            chunk_days=7,
            lock_source_id="neso.carbon.national.current",
            split_at_year_boundary=True,
        ),
    )


def _retain_records_in_window(result: AdapterResult[Any]) -> AdapterResult[Any]:
    records = tuple(
        record
        for record in result.records
        if result.window.start <= _record_timestamp(record) < result.window.end
    )
    removed = len(result.records) - len(records)
    if not removed:
        return result
    return replace(
        result,
        records=records,
        warnings=result.warnings
        + (f"ignored {removed} normalized record(s) outside the requested window",),
    )


def _record_timestamp(record: Any) -> datetime:
    if isinstance(record, (GenerationRecord, DemandRecord, InterconnectorFlowRecord)):
        return as_utc(record.observed_at, field_name="record timestamp")
    if isinstance(record, CarbonIntensityRecord):
        return as_utc(record.period_start, field_name="record timestamp")
    if isinstance(
        record,
        (
            DemandForecastRecord,
            WindForecastRecord,
            RemitUnavailabilityRecord,
            SystemWarningRecord,
        ),
    ):
        # These endpoints are queried by publication time. Forecast validity and
        # event activity may truthfully extend outside the requested date range;
        # the backfill range describes when the source published each vintage.
        return as_utc(record.published_at, field_name="record timestamp")
    raise TypeError(f"unsupported history backfill record: {type(record).__name__}")


def _safe_error_message(exc: Exception) -> str:
    """Bound diagnostics and remove URLs/credentials before database storage."""

    message = " ".join(str(exc).split())
    message = re.sub(
        r"(?:https?|postgres(?:ql)?(?:\+asyncpg)?|redis)://\S+",
        "[url]",
        message,
        flags=re.IGNORECASE,
    )
    message = re.sub(
        r"(?i)(password|token|secret|api[_-]?key)\s*[=:]\s*\S+",
        r"\1=[redacted]",
        message,
    )
    return (message or "operation failed")[:500]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="50hz-history-backfill",
        description=(
            "Backfill bounded, completed Europe/London settlement days. "
            "--end is exclusive."
        ),
        epilog=(
            f"Recommended initial validation: --days {DEFAULT_BACKFILL_DAYS} "
            "--dry-run"
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        help=f"number of completed settlement days (1-{MAX_BACKFILL_DAYS})",
    )
    parser.add_argument("--start", type=_date_argument, help="first date (YYYY-MM-DD)")
    parser.add_argument(
        "--end",
        type=_date_argument,
        help="exclusive date (YYYY-MM-DD); defaults to today with --days",
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=[source.value for source in SOURCE_ORDER],
        help="allowlisted source; repeat to select more (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the chunk summary without HTTP or database writes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-fetch chunks already marked successful (writes remain idempotent)",
    )
    return parser


def request_from_args(
    args: argparse.Namespace,
    *,
    today: date,
) -> BackfillRequest:
    date_range = resolve_backfill_date_range(
        days=args.days,
        start=args.start,
        end=args.end,
        today=today,
    )
    requested = args.source or [source.value for source in SOURCE_ORDER]
    # Deduplicate repeated flags while preserving the stable allow-list order.
    selected = tuple(
        source for source in SOURCE_ORDER if source.value in set(requested)
    )
    return BackfillRequest(
        date_range=date_range,
        sources=selected,
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )


def render_report(report: BackfillReport) -> str:
    """Return a deliberately payload- and secret-free operator summary."""

    mode = "DRY RUN" if report.request.dry_run else "BACKFILL"
    lines = [
        (
            f"{mode}: {report.request.date_range.start.isoformat()} to "
            f"{report.request.date_range.end.isoformat()} (exclusive), "
            f"{report.request.date_range.day_count} settlement days"
        )
    ]
    for source in report.request.sources:
        counts = report.counts(source)
        details = ", ".join(
            f"{status.value}={counts[status]}"
            for status in BackfillStatus
            if counts[status]
        )
        failure_types = Counter(
            outcome.error_type or "UnknownError"
            for outcome in report.outcomes
            if outcome.source is source and outcome.status is BackfillStatus.FAILED
        )
        if failure_types:
            safe_types = ",".join(
                f"{error_type[:80]}:{count}"
                for error_type, count in sorted(failure_types.items())
            )
            details = f"{details}; failure_types={safe_types}"
        lines.append(f"{source.value}: {details or 'no chunks'}")
    lines.extend(
        (
            "scope: NDF/WINDFOR and REMIT/SYSWARN ranges use publication time; "
            "forecast validity and event activity may extend outside the range",
            "deferred: elexon.freq high-frequency detail is intentionally not a "
            "90-day backfill",
            "limitation: the selected public carbon range endpoint has no "
            "historical forecast issue timestamps, so it backfills estimates only",
        )
    )
    lines.append("status: complete" if report.exit_code == 0 else "status: incomplete")
    return "\n".join(lines)


async def run_configured_backfill(request: BackfillRequest) -> BackfillReport:
    settings = get_settings()
    elexon_base_url = settings.elexon_base_url.rstrip("/") + "/"
    carbon_base_url = settings.carbon_intensity_base_url.rstrip("/") + "/"
    elexon_client = AsyncJSONClient(base_url=elexon_base_url)
    carbon_client = AsyncJSONClient(base_url=carbon_base_url)
    session_factory = get_session_factory()
    try:
        runner = HistoryBackfillRunner(
            sources=build_backfill_sources(
                elexon_client=elexon_client,
                carbon_client=carbon_client,
            ),
            repository=PostgresIngestionRepository(session_factory),
            locks=PostgresAdvisoryLockProvider(session_factory),
        )
        return await runner.run(request)
    finally:
        await elexon_client.aclose()
        await carbon_client.aclose()
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        request = request_from_args(args, today=datetime.now(LONDON).date())
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))

    # Even dry-runs require an explicitly configured target. This makes it much
    # harder to validate one environment and accidentally execute against
    # another later without noticing the missing configuration.
    try:
        configured_database_url()
    except DatabaseNotConfiguredError:
        print("DATABASE_URL is required for history backfill", file=sys.stderr)
        return 2

    try:
        report = asyncio.run(run_configured_backfill(request))
    except Exception as exc:
        # Never echo exception messages here: connection errors can contain
        # credentials and upstream errors can contain payload previews.
        print(f"history backfill could not start ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(render_report(report))
    return report.exit_code


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from exc


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
