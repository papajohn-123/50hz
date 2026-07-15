"""Mobile presentation of source-compatible recent-history comparisons."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import AwareDatetime, Field

from app.api.models import MobileModel
from app.history import (
    HistoryComparisonSet,
    PointComparisonResult,
    ResultStatus,
    Rolling28ComparisonResult,
    compare_history,
    materialize_half_hours,
)
from app.history.repository import (
    INDO_SOURCE_ID,
    NATIONAL_CARBON_SOURCE_ID,
    HistoryMetric,
    HistorySeriesRequest,
    NormalizedHistoryRepository,
)
from app.persistence.reads import CurrentGridRead, GridReadRepository


HISTORY_LOOKBACK = timedelta(days=29)
HISTORY_INTERVAL = timedelta(minutes=30)


class HistoryPointContext(MobileModel):
    status: str
    reason: str
    comparison_at: AwareDatetime | None = None
    comparison_value: float | None = None
    reference_minus_comparison: float | None = None
    percent_change_from_comparison: float | None = None
    comparison_sample_count: int = Field(ge=0)


class HistoryRollingContext(MobileModel):
    status: str
    reason: str
    expected_day_count: int = Field(ge=1)
    valid_day_count: int = Field(ge=0)
    coverage_fraction: float = Field(ge=0, le=1)
    median: float | None = None
    first_quartile: float | None = None
    third_quartile: float | None = None
    reference_minus_median: float | None = None
    reference_percentile: float | None = Field(default=None, ge=0, le=100)


class MetricHistoryContext(MobileModel):
    metric_id: str = Field(alias="metricID")
    display_name: str
    unit: str
    fact_class: str
    source_id: str = Field(alias="sourceID")
    available: bool
    reference_at: AwareDatetime | None = None
    reference_value: float | None = None
    summary: str
    materialization_coverage_fraction: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    previous_period: HistoryPointContext | None = None
    same_time_yesterday: HistoryPointContext | None = None
    same_time_seven_days_ago: HistoryPointContext | None = None
    rolling_28_days: HistoryRollingContext | None = None


class CurrentHistoryContextResponse(MobileModel):
    schema_version: str = "1.0"
    evaluated_at: AwareDatetime
    baseline_disclosure: str = (
        "Comparisons use the same compatible metric, geography, unit, fact class "
        "and methodology. The 28-day baseline matches Europe/London wall-clock "
        "half-hours and is withheld below 95% coverage."
    )
    metrics: list[MetricHistoryContext]


class _HistoryLoader(Protocol):
    async def load(self, request: HistorySeriesRequest): ...


async def present_current_history_context(
    grid_repository: GridReadRepository,
    history_repository: NormalizedHistoryRepository | _HistoryLoader,
    *,
    as_of: datetime,
) -> CurrentHistoryContextResponse:
    evaluated_at = _aware_utc(as_of, "as_of")
    read = await grid_repository.get_current(as_of=evaluated_at)
    metrics = [
        await _metric_context(
            history_repository,
            metric=HistoryMetric.NATIONAL_DEMAND,
            source_id=INDO_SOURCE_ID,
            display_name="National demand",
            unit="MW",
            fact_class="observed",
            observed_at=(
                read.demand.provenance.observed_at
                if read.demand is not None
                else None
            ),
        ),
        await _metric_context(
            history_repository,
            metric=HistoryMetric.NATIONAL_CARBON,
            source_id=NATIONAL_CARBON_SOURCE_ID,
            display_name="National carbon intensity",
            unit="gCO2/kWh",
            fact_class="estimated",
            observed_at=(
                read.carbon.provenance.observed_at
                if read.carbon is not None
                else None
            ),
        ),
    ]
    return CurrentHistoryContextResponse(
        evaluated_at=evaluated_at,
        metrics=metrics,
    )


async def _metric_context(
    repository: NormalizedHistoryRepository | _HistoryLoader,
    *,
    metric: HistoryMetric,
    source_id: str,
    display_name: str,
    unit: str,
    fact_class: str,
    observed_at: datetime | None,
) -> MetricHistoryContext:
    if observed_at is None:
        return MetricHistoryContext(
            metric_id=metric.value,
            display_name=display_name,
            unit=unit,
            fact_class=fact_class,
            source_id=source_id,
            available=False,
            summary="No current compatible reading is available for comparison.",
        )

    reference_at = _exact_half_hour(observed_at)
    window_start = reference_at - HISTORY_LOOKBACK
    window_end = reference_at + HISTORY_INTERVAL
    raw = await repository.load(
        HistorySeriesRequest(
            metric_id=metric,
            source_id=source_id,
            start=window_start,
            end=window_end,
        )
    )
    materialized = materialize_half_hours(
        raw,
        start=window_start,
        end=window_end,
    )
    comparison = compare_history(
        materialized.series,
        reference_start=reference_at,
    )
    reference_interval = next(
        (
            interval
            for interval in materialized.intervals
            if interval.start == reference_at
        ),
        None,
    )
    reference_value = comparison.rolling_28_days.reference_value
    if reference_value is None:
        reference_value = comparison.previous_period.reference_value

    return MetricHistoryContext(
        metric_id=metric.value,
        display_name=display_name,
        unit=unit,
        fact_class=fact_class,
        source_id=source_id,
        available=reference_value is not None,
        reference_at=reference_at,
        reference_value=reference_value,
        summary=_history_summary(display_name, comparison),
        materialization_coverage_fraction=(
            reference_interval.coverage_fraction
            if reference_interval is not None
            else None
        ),
        previous_period=_point_context(comparison.previous_period),
        same_time_yesterday=_point_context(comparison.same_time_yesterday),
        same_time_seven_days_ago=_point_context(
            comparison.same_time_seven_days_ago
        ),
        rolling_28_days=_rolling_context(comparison.rolling_28_days),
    )


def _point_context(value: PointComparisonResult) -> HistoryPointContext:
    return HistoryPointContext(
        status=value.status.value,
        reason=value.reason.value,
        comparison_at=value.comparison_start,
        comparison_value=value.comparison_value,
        reference_minus_comparison=value.reference_minus_comparison,
        percent_change_from_comparison=value.percent_change_from_comparison,
        comparison_sample_count=value.comparison_sample_count,
    )


def _rolling_context(value: Rolling28ComparisonResult) -> HistoryRollingContext:
    return HistoryRollingContext(
        status=value.status.value,
        reason=value.reason.value,
        expected_day_count=value.coverage.expected_day_count,
        valid_day_count=value.coverage.valid_sample_count,
        coverage_fraction=value.coverage.coverage_fraction,
        median=value.median,
        first_quartile=value.first_quartile,
        third_quartile=value.third_quartile,
        reference_minus_median=value.reference_minus_median,
        reference_percentile=value.reference_percentile,
    )


def _history_summary(
    display_name: str,
    comparison: HistoryComparisonSet,
) -> str:
    rolling = comparison.rolling_28_days
    if rolling.status is not ResultStatus.AVAILABLE:
        return (
            f"{display_name} has a current compatible reading, but there is not "
            "enough complete same-time history for a 28-day comparison."
        )
    assert rolling.reference_percentile is not None
    percentile = rolling.reference_percentile
    if percentile <= 20:
        position = "well below"
    elif percentile < 40:
        position = "below"
    elif percentile <= 60:
        position = "near the middle of"
    elif percentile < 80:
        position = "above"
    else:
        position = "well above"
    return (
        f"{display_name} is {position} its prior 28-day same-time distribution "
        f"(percentile {percentile:.0f})."
    )


def _exact_half_hour(value: datetime) -> datetime:
    utc = _aware_utc(value, "observed_at")
    if utc.minute not in (0, 30) or utc.second or utc.microsecond:
        raise ValueError("History context requires an exact half-hour observation")
    return utc


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)
