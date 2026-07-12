from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterator

from app.charging.models import (
    INTERVAL_MINUTES,
    MEANINGFUL_ABSOLUTE_DELTA_GCO2_KWH,
    MEANINGFUL_PERCENT_DELTA,
    CarbonForecastSeries,
    ChargingWindow,
    FlexibleUseComparison,
    FlexibleUsePlan,
    FlexibleUseStatus,
    ForecastCoverage,
    StartNowComparisonStatus,
)


INTERVAL = timedelta(minutes=INTERVAL_MINUTES)
INTERVAL_SECONDS = int(INTERVAL.total_seconds())


@dataclass(frozen=True, slots=True)
class _PreparedPoint:
    start: datetime
    end: datetime
    intensity: float
    source_record_id: str


@dataclass(frozen=True, slots=True)
class _CalculatedWindow:
    raw_average: float
    public: ChargingWindow


def plan_flexible_use(
    forecast: CarbonForecastSeries,
    *,
    duration: timedelta,
    earliest_start: datetime,
    latest_finish: datetime,
    start_now: datetime,
    start_now_forecast: CarbonForecastSeries | None = None,
    continuous: bool = True,
) -> FlexibleUsePlan:
    """Plan one continuous flexible-use window deterministically.

    Candidate recommendations begin on half-hour boundaries.  ``start_now`` may
    be inside a half-hour; its comparison integrates the covered portions of the
    same strict half-hour forecast series for the exact requested duration.
    """

    if continuous is not True:
        raise ValueError("only continuous flexible-use windows are supported")
    duration_seconds = _duration_seconds(duration)
    required_intervals = duration_seconds // INTERVAL_SECONDS
    earliest = _aware_utc(earliest_start, "earliest_start")
    latest = _aware_utc(latest_finish, "latest_finish")
    comparison_start = _aware_utc(start_now, "start_now")

    if earliest >= latest:
        raise ValueError("latest_finish must follow earliest_start")
    if not _is_half_hour_boundary(earliest):
        raise ValueError("earliest_start must be on a half-hour boundary")
    if not _is_half_hour_boundary(latest):
        raise ValueError("latest_finish must be on a half-hour boundary")

    prepared = _prepare_points(forecast)
    by_start = {point.start: point for point in prepared}
    starts = [point.start for point in prepared]
    expected_starts = list(_steps(earliest, latest))
    gap_starts = [start for start in expected_starts if start not in by_start]
    available_intervals = len(expected_starts) - len(gap_starts)

    last_candidate_start = latest - timedelta(seconds=duration_seconds)
    candidate_starts = (
        list(_steps_inclusive(earliest, last_candidate_start))
        if last_candidate_start >= earliest
        else []
    )
    calculated_candidates = [
        candidate
        for candidate in (
            _window_at(
                by_start,
                starts,
                start=start,
                duration_seconds=duration_seconds,
            )
            for start in candidate_starts
        )
        if candidate is not None
    ]
    coverage = ForecastCoverage(
        required_interval_count=required_intervals,
        expected_interval_count=len(expected_starts),
        available_interval_count=available_intervals,
        coverage_fraction=(
            round(available_intervals / len(expected_starts), 4)
            if expected_starts
            else 0.0
        ),
        gap_starts=gap_starts,
        candidate_start_count=len(candidate_starts),
        complete_candidate_count=len(calculated_candidates),
    )

    comparison_series = start_now_forecast or forecast
    comparison = _comparison_without_recommendation(
        forecast,
        comparison_series,
    )

    if not calculated_candidates:
        return FlexibleUsePlan(
            status=FlexibleUseStatus.INSUFFICIENT_COVERAGE,
            summary=(
                "There is not enough contiguous half-hour forecast coverage "
                "for the requested duration within the requested bounds."
            ),
            requested_duration_minutes=duration_seconds // 60,
            earliest_start=earliest,
            latest_finish=latest,
            coverage=coverage,
            comparison=comparison,
        )

    recommended = min(
        calculated_candidates,
        key=lambda candidate: (
            candidate.raw_average,
            candidate.public.start,
        ),
    )
    comparison = _compare_start_now(
        recommended,
        recommended_series=forecast,
        comparison_series=comparison_series,
        start=comparison_start,
        duration_seconds=duration_seconds,
    )

    if comparison.status != StartNowComparisonStatus.COMPATIBLE:
        status = FlexibleUseStatus.WINDOW_FOUND
        summary = (
            "A lowest-intensity window was found, but a compatible start-now "
            "comparison is unavailable."
        )
    elif comparison.is_meaningful:
        status = FlexibleUseStatus.LOWER_CARBON_WINDOW
        summary = "A meaningfully lower-carbon forecast window is available."
    else:
        status = FlexibleUseStatus.NO_MEANINGFUL_DIFFERENCE
        summary = (
            "There is no meaningful lower-carbon difference within the "
            "requested bounds."
        )

    return FlexibleUsePlan(
        status=status,
        summary=summary,
        requested_duration_minutes=duration_seconds // 60,
        earliest_start=earliest,
        latest_finish=latest,
        recommended_window=recommended.public,
        coverage=coverage,
        comparison=comparison,
    )


def _duration_seconds(duration: timedelta) -> int:
    if not isinstance(duration, timedelta):
        raise TypeError("duration must be a timedelta")
    seconds = duration.total_seconds()
    if seconds <= 0:
        raise ValueError("duration must be positive")
    if not seconds.is_integer() or int(seconds) % INTERVAL_SECONDS:
        raise ValueError("duration must be a whole number of half-hour intervals")
    return int(seconds)


def _prepare_points(series: CarbonForecastSeries) -> list[_PreparedPoint]:
    prepared = sorted(
        (
            _PreparedPoint(
                start=point.start.astimezone(UTC),
                end=point.end.astimezone(UTC),
                intensity=float(point.intensity_gco2_kwh),
                source_record_id=point.source_record_id,
            )
            for point in series.points
        ),
        key=lambda point: point.start,
    )
    for point in prepared:
        if point.end - point.start != INTERVAL:
            raise ValueError("every forecast point must cover exactly 30 minutes")
        if not _is_half_hour_boundary(point.start):
            raise ValueError("forecast points must start on half-hour boundaries")
    for previous, current in zip(prepared, prepared[1:]):
        if current.start < previous.end:
            raise ValueError("forecast points must not overlap")
    return prepared


def _window_at(
    by_start: dict[datetime, _PreparedPoint],
    starts: list[datetime],
    *,
    start: datetime,
    duration_seconds: int,
) -> _CalculatedWindow | None:
    target_end = start + timedelta(seconds=duration_seconds)
    cursor = start
    weighted_total = 0.0
    covered_seconds = 0.0
    source_record_ids: list[str] = []

    while cursor < target_end:
        point = _point_covering(by_start, starts, cursor)
        if point is None:
            return None
        segment_end = min(point.end, target_end)
        seconds = (segment_end - cursor).total_seconds()
        if seconds <= 0:
            return None
        weighted_total += point.intensity * seconds
        covered_seconds += seconds
        if not source_record_ids or source_record_ids[-1] != point.source_record_id:
            source_record_ids.append(point.source_record_id)
        cursor = segment_end

    if covered_seconds != duration_seconds:
        return None
    average = weighted_total / covered_seconds
    return _CalculatedWindow(
        raw_average=average,
        public=ChargingWindow(
            start=start,
            end=target_end,
            average_intensity_gco2_kwh=round(average, 2),
            source_record_ids=source_record_ids,
            coverage_fraction=1.0,
        ),
    )


def _point_covering(
    by_start: dict[datetime, _PreparedPoint],
    starts: list[datetime],
    instant: datetime,
) -> _PreparedPoint | None:
    index = bisect_right(starts, instant) - 1
    if index < 0:
        return None
    point = by_start[starts[index]]
    if not point.start <= instant < point.end:
        return None
    return point


def _compare_start_now(
    recommended: _CalculatedWindow,
    *,
    recommended_series: CarbonForecastSeries,
    comparison_series: CarbonForecastSeries,
    start: datetime,
    duration_seconds: int,
) -> FlexibleUseComparison:
    comparison_points = _prepare_points(comparison_series)
    comparison_window = _window_at(
        {point.start: point for point in comparison_points},
        [point.start for point in comparison_points],
        start=start,
        duration_seconds=duration_seconds,
    )
    if comparison_window is None:
        return FlexibleUseComparison(
            status=StartNowComparisonStatus.INSUFFICIENT_COVERAGE,
        )

    mismatches = _compatibility_mismatches(recommended_series, comparison_series)
    if mismatches:
        return FlexibleUseComparison(
            status=StartNowComparisonStatus.INCOMPATIBLE_SERIES,
            start_now_window=comparison_window.public,
            incompatibility_fields=mismatches,
        )

    delta = comparison_window.raw_average - recommended.raw_average
    percent = (
        delta / comparison_window.raw_average * 100
        if comparison_window.raw_average > 0
        else None
    )
    meaningful = (
        percent is not None
        and delta >= MEANINGFUL_ABSOLUTE_DELTA_GCO2_KWH
        and percent >= MEANINGFUL_PERCENT_DELTA
    )
    return FlexibleUseComparison(
        status=StartNowComparisonStatus.COMPATIBLE,
        start_now_window=comparison_window.public,
        start_now_minus_recommended_gco2_kwh=round(delta, 2),
        percent_lower_than_start_now=(
            round(percent, 2) if percent is not None else None
        ),
        is_meaningful=meaningful,
    )


def _comparison_without_recommendation(
    recommended_series: CarbonForecastSeries,
    comparison_series: CarbonForecastSeries,
) -> FlexibleUseComparison:
    mismatches = _compatibility_mismatches(recommended_series, comparison_series)
    if mismatches:
        return FlexibleUseComparison(
            status=StartNowComparisonStatus.INCOMPATIBLE_SERIES,
            incompatibility_fields=mismatches,
        )
    return FlexibleUseComparison(
        status=StartNowComparisonStatus.INSUFFICIENT_COVERAGE,
    )


def _compatibility_mismatches(
    left: CarbonForecastSeries,
    right: CarbonForecastSeries,
) -> list[str]:
    fields = (
        "series_id",
        "geography",
        "source_id",
        "methodology_version",
        "fact_class",
        "vintage_at",
    )
    return [field for field in fields if getattr(left, field) != getattr(right, field)]


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _is_half_hour_boundary(value: datetime) -> bool:
    value = value.astimezone(UTC)
    return value.minute in (0, 30) and value.second == 0 and value.microsecond == 0


def _steps(start: datetime, end: datetime) -> Iterator[datetime]:
    cursor = start
    while cursor < end:
        yield cursor
        cursor += INTERVAL


def _steps_inclusive(start: datetime, end: datetime) -> Iterator[datetime]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += INTERVAL
