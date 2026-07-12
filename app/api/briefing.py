"""Production assembly of the deterministic daily grid briefing.

The assembler deliberately treats each source family as independently fallible.
A missing forecast, notice, or observation therefore removes only the affected
section and is made explicit in coverage instead of taking the whole briefing
offline.  All copy and selection are delegated to :mod:`app.briefing`; this
module only turns normalized repository reads into that source-neutral input.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

from app.api.forecast_vintages import (
    NationalForecastVintage,
    load_national_forecast_vintages,
)
from app.api.notices import reported_notice_to_grid_event
from app.briefing import (
    BestWindowInput,
    Briefing,
    BriefingCoverageInput,
    BriefingInput,
    BriefingSourceStatus,
    ComparisonPeriod,
    CurrentFactClass,
    CurrentPositionInput,
    CurrentValueInput,
    FutureFactClass,
    FutureMomentInput,
    ObservedChangeInput,
    ReportedEventInput,
    ReportedEventSeverity,
    RevisionWatermark,
    SourceState,
    build_briefing,
)
from app.charging import plan_flexible_use
from app.persistence import (
    CarbonRead,
    CurrentGridRead,
    DemandRead,
    ForecastRead,
    FrequencyRead,
    ReportedNoticeRead,
)


FUTURE_HORIZON = timedelta(hours=24)
FORECAST_CAPTURE_LOOKBACK = timedelta(hours=24)
MAX_FORECAST_CAPTURE_AGE = timedelta(minutes=90)
BEST_WINDOW_DURATION = timedelta(minutes=60)

_EXPECTED_CURRENT = {
    "demand": ("elexon.indo", "INDO"),
    "carbon": ("neso.carbon-intensity-national", "CARBON_INTENSITY_NATIONAL"),
    "frequency": ("elexon.freq", "FREQ"),
}
_EXPECTED_FORECAST = {
    "carbon": ("neso.carbon-intensity-national", "CARBON_INTENSITY_NATIONAL"),
    "demand": ("elexon.ndf", "NDF"),
    "wind": ("elexon.windfor", "WINDFOR"),
}
_SOURCE_CADENCES = {
    "CARBON_INTENSITY_NATIONAL": 1_800,
    "FREQ": 60,
    "INDO": 1_800,
    "NDF": 1_800,
    "REMIT": 300,
    "SYSWARN": 300,
    "WINDFOR": 1_800,
}
_STATE_RANK = {
    SourceState.LIVE: 0,
    SourceState.DELAYED: 1,
    SourceState.STALE: 2,
    SourceState.UNAVAILABLE: 3,
}
_NOTICE_SEVERITY = {
    "info": ReportedEventSeverity.INFO,
    "notable": ReportedEventSeverity.NOTABLE,
    "important": ReportedEventSeverity.MATERIAL,
    "critical": ReportedEventSeverity.CRITICAL,
}


class BriefingRepository(Protocol):
    async def get_current(
        self,
        *,
        as_of: datetime | None = None,
        carbon_region: str = "GB",
    ) -> CurrentGridRead: ...

    async def get_forecasts(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        metric_types: Iterable[str] | None = None,
        series_key: str | None = None,
        issued_before: datetime | None = None,
    ) -> tuple[ForecastRead, ...]: ...

    async def get_carbon_forecast_history(
        self,
        *,
        region_code: str,
        window_start: datetime,
        window_end: datetime,
        captured_after: datetime,
        captured_before: datetime,
        issued_before: datetime | None = None,
    ) -> tuple[ForecastRead, ...]: ...

    async def get_briefing_notices(
        self,
        *,
        as_of: datetime,
        upcoming_until: datetime,
        warning_fresh_for_seconds: int = 900,
    ) -> tuple[ReportedNoticeRead, ...]: ...


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _Attempt[T]:
    value: T | None = None
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class _SourceEvidence:
    source_id: str
    dataset: str
    observed_at: datetime
    retrieved_at: datetime
    detail: str


@dataclass(frozen=True, slots=True)
class _BestWindowResult:
    value: BestWindowInput | None
    evidence: tuple[_SourceEvidence, ...] = ()
    captured_times: tuple[datetime, ...] = ()
    revision_parts: tuple[str, ...] = ()


async def present_today_briefing(
    repository: BriefingRepository,
    *,
    as_of: datetime,
) -> Briefing:
    """Build one bounded briefing at an explicitly supplied evaluation instant."""

    instant = _aware_utc(as_of, "as_of")
    previous_cutoff = instant - timedelta(hours=1)
    future_end = instant + FUTURE_HORIZON
    query_start = _floor_half_hour(instant)

    current_attempt, previous_attempt, forecast_attempt, notice_attempt, vintage_attempt = (
        await asyncio.gather(
            _attempt(lambda: repository.get_current(as_of=instant)),
            _attempt(lambda: repository.get_current(as_of=previous_cutoff)),
            _attempt(
                lambda: repository.get_forecasts(
                    window_start=instant,
                    window_end=future_end,
                    metric_types=("carbon_intensity", "demand", "generation"),
                    issued_before=instant,
                )
            ),
            _attempt(
                lambda: repository.get_briefing_notices(
                    as_of=instant,
                    upcoming_until=future_end,
                )
            ),
            _attempt(
                lambda: load_national_forecast_vintages(
                    repository,
                    window_start=query_start,
                    window_end=_ceil_half_hour(instant) + FUTURE_HORIZON,
                    captured_before=instant,
                    capture_lookback=FORECAST_CAPTURE_LOOKBACK,
                )
            ),
        )
    )

    current_read = current_attempt.value
    current, current_evidence, current_metrics, observed_times, revision_parts = (
        _present_current_inputs(current_read, as_of=instant)
        if current_read is not None
        else (CurrentPositionInput(expected_metric_ids=list(_EXPECTED_CURRENT)), (), set(), (), ())
    )

    comparison_periods: list[ComparisonPeriod] = []
    changes: list[ObservedChangeInput] = []
    if current_read is not None and previous_attempt.value is not None:
        comparison_periods, changes = _present_one_hour_changes(
            current_read,
            previous_attempt.value,
            as_of=instant,
        )
        revision_parts = (
            *revision_parts,
            *(
                f"change:{item.stable_id}:{item.current_value}:{item.previous_value}"
                for item in changes
            ),
        )

    forecasts = forecast_attempt.value or ()
    next_moments, forecast_evidence, forecast_families, captured_times, forecast_parts = (
        _present_future_moments(forecasts, as_of=instant, horizon_end=future_end)
    )
    revision_parts = (*revision_parts, *forecast_parts)

    notices = notice_attempt.value or ()
    reported_events, notice_evidence, reported_times, notice_parts = (
        _present_reported_events(notices, as_of=instant, horizon_end=future_end)
    )
    revision_parts = (*revision_parts, *notice_parts)

    best = _present_best_window(vintage_attempt.value or (), as_of=instant)
    revision_parts = (*revision_parts, *best.revision_parts)

    evidence = (
        *current_evidence,
        *forecast_evidence,
        *notice_evidence,
        *best.evidence,
    )
    missing_families: list[str] = []
    notes: list[str] = []
    unavailable_sources: list[tuple[str, str, str]] = []

    for metric, (source_id, dataset) in _EXPECTED_CURRENT.items():
        if metric not in current_metrics:
            missing_families.append(f"now.{metric}")
            unavailable_sources.append(
                (source_id, dataset, f"No eligible current {metric} value")
            )
    if not current_attempt.succeeded:
        notes.append(
            "The current-observation read failed; independent sections were retained."
        )
    if not previous_attempt.succeeded:
        missing_families.append("changes.one_hour")
        notes.append("The compatible one-hour comparison read failed.")

    for family, (source_id, dataset) in _EXPECTED_FORECAST.items():
        if family not in forecast_families:
            missing_families.append(f"forecast.{family}")
            unavailable_sources.append(
                (
                    source_id,
                    dataset,
                    f"No eligible national {family} forecast",
                )
            )
    if not forecast_attempt.succeeded:
        notes.append(
            "The future-forecast read failed; observed and reported sections "
            "were retained."
        )
    if not notice_attempt.succeeded:
        missing_families.append("reported_events")
        notes.append("The reported-event read failed; other sections were retained.")
        unavailable_sources.extend(
            (
                ("elexon.remit", "REMIT", "Reported-event source read failed"),
                ("elexon.syswarn", "SYSWARN", "Reported-event source read failed"),
            )
        )
    if not vintage_attempt.succeeded:
        missing_families.append("best_window")
        notes.append("The compatible carbon-forecast history read failed.")
    elif best.value is None:
        notes.append(
            "No complete continuous 60-minute national carbon forecast window was available."
        )

    source_statuses = _present_source_statuses(
        evidence,
        unavailable_sources=unavailable_sources,
        as_of=instant,
    )
    all_forecast_times = (*captured_times, *best.captured_times)

    facts = BriefingInput(
        as_of=instant,
        now=current,
        changes=changes,
        next_moments=next_moments,
        reported_events=reported_events,
        best_window=best.value,
        comparison_periods=comparison_periods,
        source_statuses=source_statuses,
        coverage=BriefingCoverageInput(
            missing_families=missing_families,
            notes=notes,
        ),
        revision_watermark=RevisionWatermark(
            revision_token=_revision_token(instant, revision_parts),
            as_of=instant,
            observed_through=max(observed_times, default=None),
            forecast_captured_through=max(all_forecast_times, default=None),
            reported_through=max(reported_times, default=None),
        ),
    )
    return build_briefing(facts)


async def _attempt(factory: Callable[[], Awaitable[T]]) -> _Attempt[T]:
    try:
        return _Attempt(value=await factory())
    except Exception as error:  # Each source family is intentionally isolated.
        return _Attempt(error=error)


def _present_current_inputs(
    read: CurrentGridRead,
    *,
    as_of: datetime,
) -> tuple[
    CurrentPositionInput,
    tuple[_SourceEvidence, ...],
    set[str],
    tuple[datetime, ...],
    tuple[str, ...],
]:
    values: list[CurrentValueInput] = []
    evidence: list[_SourceEvidence] = []
    available: set[str] = set()
    times: list[datetime] = []
    revisions: list[str] = []
    metadata = {source.id: source for source in read.sources}

    candidates: tuple[
        tuple[
            str,
            str,
            float,
            str,
            CurrentFactClass,
            float,
            DemandRead | CarbonRead | FrequencyRead | None,
        ],
        ...,
    ] = (
        (
            "demand",
            "National demand",
            read.demand.megawatts if read.demand is not None else 0,
            "MW",
            CurrentFactClass.OBSERVED,
            1.0,
            read.demand,
        ),
        (
            "carbon",
            "National carbon intensity",
            read.carbon.intensity_gco2_kwh if read.carbon is not None else 0,
            "gCO2/kWh",
            CurrentFactClass.ESTIMATED,
            0.9,
            read.carbon,
        ),
        (
            "frequency",
            "System frequency",
            read.frequency.hertz if read.frequency is not None else 0,
            "Hz",
            CurrentFactClass.OBSERVED,
            0.8,
            read.frequency,
        ),
    )
    for metric, label, value, unit, fact_class, priority, reading in candidates:
        if reading is None or not math.isfinite(value):
            continue
        provenance = reading.provenance
        if provenance.observed_at > as_of or provenance.retrieved_at > as_of:
            continue
        stable_id = f"current:{metric}:{provenance.source_id}"
        values.append(
            CurrentValueInput(
                stable_id=stable_id,
                metric_id=metric,
                label=label,
                value=value,
                unit=unit,
                fact_class=fact_class,
                observed_at=provenance.observed_at,
                source_ids=[provenance.source_id],
                priority=priority,
            )
        )
        source = metadata.get(provenance.source_id)
        dataset = (
            source.dataset
            if source is not None
            else _dataset_from_source(provenance.source_id)
        )
        evidence.append(
            _SourceEvidence(
                source_id=provenance.source_id,
                dataset=dataset,
                observed_at=provenance.observed_at,
                retrieved_at=provenance.retrieved_at,
                detail=f"Current {metric} evidence",
            )
        )
        available.add(metric)
        times.append(provenance.observed_at)
        revisions.append(
            f"current:{metric}:{provenance.source_id}:{provenance.source_record_id or ''}:"
            f"{provenance.observed_at.isoformat()}:{value}"
        )
    return (
        CurrentPositionInput(
            values=values,
            expected_metric_ids=list(_EXPECTED_CURRENT),
        ),
        tuple(evidence),
        available,
        tuple(times),
        tuple(revisions),
    )


def _present_one_hour_changes(
    current: CurrentGridRead,
    previous: CurrentGridRead,
    *,
    as_of: datetime,
) -> tuple[list[ComparisonPeriod], list[ObservedChangeInput]]:
    specs = (
        (
            "demand",
            "National demand",
            current.demand,
            previous.demand,
            lambda value: value.megawatts,
            lambda value: (value.series_key, value.demand_type),
            "MW",
            250.0,
            1_000.0,
        ),
        (
            "carbon",
            "National carbon intensity",
            current.carbon,
            previous.carbon,
            lambda value: value.intensity_gco2_kwh,
            lambda value: (value.region_code.casefold(),),
            "gCO2/kWh",
            10.0,
            50.0,
        ),
        (
            "frequency",
            "System frequency",
            current.frequency,
            previous.frequency,
            lambda value: value.hertz,
            lambda value: (value.series_key,),
            "Hz",
            0.02,
            0.10,
        ),
    )
    periods: list[ComparisonPeriod] = []
    changes: list[ObservedChangeInput] = []
    for metric, label, latest, earlier, value_of, identity_of, unit, threshold, scale in specs:
        if latest is None or earlier is None:
            continue
        latest_provenance = latest.provenance
        earlier_provenance = earlier.provenance
        compatible = (
            latest_provenance.source_id == earlier_provenance.source_id
            and identity_of(latest) == identity_of(earlier)
            and latest_provenance.observed_at - earlier_provenance.observed_at
            == timedelta(hours=1)
            and latest_provenance.observed_at <= as_of
            and latest_provenance.retrieved_at <= as_of
            and earlier_provenance.retrieved_at <= as_of
        )
        latest_value = float(value_of(latest))
        earlier_value = float(value_of(earlier))
        if not compatible or not all(map(math.isfinite, (latest_value, earlier_value))):
            continue
        period_id = f"previous-compatible-hour:{metric}:{latest_provenance.observed_at.isoformat()}"
        periods.append(
            ComparisonPeriod(
                id=period_id,
                label="the previous compatible hour",
                start=earlier_provenance.observed_at,
                end=latest_provenance.observed_at,
            )
        )
        delta = latest_value - earlier_value
        changes.append(
            ObservedChangeInput(
                stable_id=f"change:{metric}:{latest_provenance.source_id}",
                metric_id=metric,
                label=label,
                current_value=latest_value,
                previous_value=earlier_value,
                delta=delta,
                unit=unit,
                observed_at=latest_provenance.observed_at,
                comparison_period_id=period_id,
                meaningful_threshold=threshold,
                significance=min(1.0, abs(delta) / scale),
                source_ids=[latest_provenance.source_id],
            )
        )
    return periods, changes


def _present_future_moments(
    rows: tuple[ForecastRead, ...],
    *,
    as_of: datetime,
    horizon_end: datetime,
) -> tuple[
    list[FutureMomentInput],
    tuple[_SourceEvidence, ...],
    set[str],
    tuple[datetime, ...],
    tuple[str, ...],
]:
    definitions = (
        (
            "carbon",
            "carbon_intensity",
            {"gb", "national"},
            "gco2/kwh",
            False,
            "Lowest national carbon forecast",
            1.0,
        ),
        (
            "demand",
            "demand",
            {"gb", "n", "national"},
            "mw",
            True,
            "National demand forecast peak",
            0.9,
        ),
        (
            "wind",
            "generation",
            {"wind"},
            "mw",
            True,
            "National wind forecast peak",
            0.8,
        ),
    )
    moments: list[FutureMomentInput] = []
    evidence: list[_SourceEvidence] = []
    families: set[str] = set()
    captures: list[datetime] = []
    revisions: list[str] = []
    for family, metric_type, series_keys, unit, choose_max, label, importance in definitions:
        eligible = [
            row
            for row in rows
            if row.metric_type == metric_type
            and row.series_key.casefold() in series_keys
            and _unit_key(row.unit) == unit
            and str(row.attributes.get("classification") or "").casefold() == "forecast"
            and as_of < row.valid_from < horizon_end
            and (row.valid_to is None or row.valid_to <= horizon_end)
            and row.issued_at <= as_of
            and row.retrieved_at <= as_of
            and math.isfinite(row.value)
            and row.value >= 0
        ]
        if not eligible:
            continue
        # Keep a single source/model/issue/capture identity before looking for
        # an extremum. This prevents a displayed moment from silently mixing
        # revisions from different forecast runs.
        latest_identity = max(
            (
                row.issued_at,
                row.retrieved_at,
                row.source_id,
                row.model_name or "",
            )
            for row in eligible
        )
        compatible = [
            row
            for row in eligible
            if (
                row.issued_at,
                row.retrieved_at,
                row.source_id,
                row.model_name or "",
            )
            == latest_identity
        ]
        selected = min(
            compatible,
            key=(
                (lambda row: (-row.value, row.valid_from, row.source_record_id or ""))
                if choose_max
                else (lambda row: (row.value, row.valid_from, row.source_record_id or ""))
            ),
        )
        end = (
            selected.valid_to
            if selected.valid_to and selected.valid_to > selected.valid_from
            else None
        )
        moments.append(
            FutureMomentInput(
                stable_id=(
                    f"forecast-moment:{family}:{selected.source_id}:"
                    f"{selected.valid_from.isoformat()}"
                ),
                label=label,
                starts_at=selected.valid_from,
                ends_at=end,
                fact_class=FutureFactClass.FORECAST,
                importance=importance,
                source_ids=[selected.source_id],
                value=selected.value,
                unit=selected.unit,
                updated_at=max(selected.issued_at, selected.retrieved_at),
            )
        )
        dataset = str(
            selected.attributes.get("dataset")
            or selected.model_name
            or _dataset_from_source(selected.source_id)
        )
        evidence.append(
            _SourceEvidence(
                source_id=selected.source_id,
                dataset=dataset,
                observed_at=selected.issued_at,
                retrieved_at=selected.retrieved_at,
                detail=f"National {family} forecast",
            )
        )
        families.add(family)
        captures.append(selected.retrieved_at)
        revisions.append(
            f"forecast:{family}:{selected.source_id}:{selected.source_record_id or ''}:"
            f"{selected.issued_at.isoformat()}:{selected.retrieved_at.isoformat()}:"
            f"{selected.valid_from.isoformat()}:{selected.value}"
        )
    return moments, tuple(evidence), families, tuple(captures), tuple(revisions)


def _present_reported_events(
    notices: tuple[ReportedNoticeRead, ...],
    *,
    as_of: datetime,
    horizon_end: datetime,
) -> tuple[
    list[ReportedEventInput],
    tuple[_SourceEvidence, ...],
    tuple[datetime, ...],
    tuple[str, ...],
]:
    events: list[ReportedEventInput] = []
    evidence: list[_SourceEvidence] = []
    times: list[datetime] = []
    revisions: list[str] = []
    for notice in notices:
        if (
            notice.published_at > as_of
            or notice.retrieved_at > as_of
            or not _notice_status_is_eligible(notice.event_status)
        ):
            continue
        is_active = (
            (notice.event_start is None or notice.event_start <= as_of)
            and (notice.event_end is None or notice.event_end > as_of)
        )
        is_upcoming = (
            notice.event_start is not None
            and as_of < notice.event_start <= horizon_end
        )
        if not (is_active or is_upcoming):
            continue
        public = reported_notice_to_grid_event(notice)
        events.append(
            ReportedEventInput(
                stable_id=public.id,
                revision_id=notice.revision_key,
                revision_number=notice.revision_number or 0,
                title=public.title,
                summary=public.summary,
                severity=_NOTICE_SEVERITY.get(public.severity, ReportedEventSeverity.INFO),
                published_at=notice.published_at,
                starts_at=notice.event_start,
                ends_at=notice.event_end,
                source_ids=[notice.source_id],
            )
        )
        dataset = "SYSWARN" if notice.notice_kind == "system_warning" else "REMIT"
        evidence.append(
            _SourceEvidence(
                source_id=notice.source_id,
                dataset=dataset,
                observed_at=notice.published_at,
                retrieved_at=notice.retrieved_at,
                detail="Authoritatively reported event",
            )
        )
        times.append(notice.published_at)
        revisions.append(
            f"notice:{notice.source_id}:{notice.external_id}:{notice.revision_key}:"
            f"{notice.published_at.isoformat()}"
        )
    return events, tuple(evidence), tuple(times), tuple(revisions)


def _present_best_window(
    vintages: tuple[NationalForecastVintage, ...],
    *,
    as_of: datetime,
) -> _BestWindowResult:
    earliest = _ceil_half_hour(as_of)
    requested_latest = earliest + FUTURE_HORIZON
    for vintage in vintages:
        if vintage.captured_at > as_of:
            continue
        if as_of - vintage.captured_at > MAX_FORECAST_CAPTURE_AGE:
            continue
        latest = min(requested_latest, vintage.horizon_end)
        if latest - earliest < BEST_WINDOW_DURATION or not _is_half_hour(latest):
            continue
        try:
            plan = plan_flexible_use(
                vintage.as_series(),
                duration=BEST_WINDOW_DURATION,
                earliest_start=earliest,
                latest_finish=latest,
                start_now=as_of,
                continuous=True,
            )
        except (TypeError, ValueError):
            continue
        window = plan.recommended_window
        if window is None or window.coverage_fraction != 1.0:
            continue
        value = BestWindowInput(
            stable_id=(
                f"best-window:national-carbon:{vintage.source_id}:"
                f"{window.start.isoformat()}"
            ),
            label="Lowest national carbon forecast window",
            start=window.start,
            end=window.end,
            average_value=window.average_intensity_gco2_kwh,
            unit="gCO2/kWh",
            source_ids=[vintage.source_id],
            methodology_version=plan.methodology.version,
            captured_at=vintage.captured_at,
        )
        dataset = _dataset_from_source(vintage.source_id)
        evidence = _SourceEvidence(
            source_id=vintage.source_id,
            dataset=dataset,
            observed_at=vintage.source_issued_at or vintage.captured_at,
            retrieved_at=vintage.captured_at,
            detail="Compatible national carbon forecast vintage for the 60-minute window",
        )
        revision = (
            f"best:{vintage.series_id}:{vintage.vintage_at.isoformat()}:"
            f"{vintage.captured_at.isoformat()}:{window.start.isoformat()}:"
            f"{window.average_intensity_gco2_kwh}:"
            f"{','.join(window.source_record_ids)}"
        )
        return _BestWindowResult(
            value=value,
            evidence=(evidence,),
            captured_times=(vintage.captured_at,),
            revision_parts=(revision,),
        )
    return _BestWindowResult(value=None)


def _present_source_statuses(
    evidence: tuple[_SourceEvidence, ...],
    *,
    unavailable_sources: list[tuple[str, str, str]],
    as_of: datetime,
) -> list[BriefingSourceStatus]:
    grouped: dict[str, list[_SourceEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.source_id, []).append(item)
    statuses: list[BriefingSourceStatus] = []
    for source_id, values in grouped.items():
        observed_at = max(value.observed_at for value in values)
        retrieved_at = max(value.retrieved_at for value in values)
        states = [
            _source_state(
                dataset=value.dataset,
                observed_at=value.observed_at,
                retrieved_at=value.retrieved_at,
                as_of=as_of,
            )
            for value in values
        ]
        statuses.append(
            BriefingSourceStatus(
                source_id=source_id,
                dataset=sorted({value.dataset for value in values})[0],
                state=max(states, key=_STATE_RANK.__getitem__),
                observed_at=observed_at,
                retrieved_at=retrieved_at,
                detail="; ".join(dict.fromkeys(value.detail for value in values)),
            )
        )
    known = set(grouped)
    for source_id, dataset, detail in unavailable_sources:
        if source_id in known:
            continue
        statuses.append(
            BriefingSourceStatus(
                source_id=source_id,
                dataset=dataset,
                state=SourceState.UNAVAILABLE,
                detail=detail,
            )
        )
        known.add(source_id)
    return sorted(statuses, key=lambda item: (item.source_id, item.dataset))


def _source_state(
    *,
    dataset: str,
    observed_at: datetime,
    retrieved_at: datetime,
    as_of: datetime,
) -> SourceState:
    normalized_dataset = dataset.strip().upper()
    cadence = _SOURCE_CADENCES.get(normalized_dataset, 300)
    # A REMIT publication can remain valid for a long event window. Its age is
    # not source staleness; delivery currency comes from our retrieval time.
    # SYSWARN, by contrast, is a short-lived publication and keeps both checks.
    observed_age = (
        0.0
        if normalized_dataset == "REMIT"
        else max(0.0, (as_of - observed_at).total_seconds())
    )
    retrieved_age = max(0.0, (as_of - retrieved_at).total_seconds())
    live_observation = max(600, cadence * 2 + 300)
    live_retrieval = max(300, cadence * 2)
    stale_observation = max(live_observation + 1, cadence * 4 + 300)
    stale_retrieval = max(live_retrieval + 1, cadence * 4)
    if observed_age <= live_observation and retrieved_age <= live_retrieval:
        return SourceState.LIVE
    if observed_age < stale_observation and retrieved_age < stale_retrieval:
        return SourceState.DELAYED
    return SourceState.STALE


def _revision_token(as_of: datetime, parts: tuple[str, ...]) -> str:
    payload = "\n".join((as_of.isoformat(), *sorted(set(parts))))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"briefing:{as_of.date().isoformat()}:{digest}"


def _notice_status_is_eligible(value: str | None) -> bool:
    normalized = (value or "").strip().casefold().replace(" ", "_")
    return normalized not in {
        "cancelled",
        "canceled",
        "dismissed",
        "withdrawn",
        "inactive",
    }


def _dataset_from_source(source_id: str) -> str:
    return source_id.rsplit(".", maxsplit=1)[-1].replace("-", "_").upper()


def _unit_key(value: str) -> str:
    return value.strip().casefold().replace("₂", "2").replace(" ", "")


def _floor_half_hour(value: datetime) -> datetime:
    return value.replace(
        minute=30 if value.minute >= 30 else 0,
        second=0,
        microsecond=0,
    )


def _ceil_half_hour(value: datetime) -> datetime:
    floor = _floor_half_hour(value)
    return floor if floor == value else floor + timedelta(minutes=30)


def _is_half_hour(value: datetime) -> bool:
    return value.minute in (0, 30) and value.second == 0 and value.microsecond == 0


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(UTC)
