from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Iterable

from app.history.calendar import INTERVAL, LONDON, expected_settlement_intervals
from app.history.models import (
    MINIMUM_COVERAGE_FRACTION,
    ROLLING_DAY_COUNT,
    DailyAggregateResult,
    DailyCoverage,
    HalfHourObservation,
    HistoryComparisonSet,
    MetricSeries,
    MetricSeriesIdentity,
    PointComparisonKind,
    PointComparisonResult,
    ResultReason,
    ResultStatus,
    Rolling28ComparisonResult,
    RollingCoverage,
)


def assess_daily_coverage(series: MetricSeries, day: date) -> DailyCoverage:
    expected = expected_settlement_intervals(day)
    expected_starts = {interval.start for interval in expected}
    groups = _groups(series.observations)
    present = sorted(start for start in expected_starts if start in groups)
    missing = sorted(expected_starts.difference(groups))
    duplicates = sorted(start for start in present if len(groups[start]) > 1)
    raw_samples = sum(len(groups[start]) for start in present)
    out_of_range = sum(
        len(observations)
        for start, observations in groups.items()
        if start not in expected_starts
    )
    fraction = len(present) / len(expected)
    return DailyCoverage(
        settlement_date=day,
        expected_interval_count=len(expected),
        raw_sample_count=raw_samples,
        unique_interval_count=len(present),
        coverage_fraction=fraction,
        missing_starts=missing,
        duplicate_starts=duplicates,
        out_of_range_sample_count=out_of_range,
        is_sufficient=(
            fraction >= MINIMUM_COVERAGE_FRACTION and not duplicates
        ),
    )


def aggregate_daily_mean(series: MetricSeries, day: date) -> DailyAggregateResult:
    coverage = assess_daily_coverage(series, day)
    if not coverage.is_sufficient:
        reason = (
            ResultReason.DUPLICATE_INTERVALS
            if coverage.duplicate_starts
            else ResultReason.COVERAGE_BELOW_THRESHOLD
        )
        return DailyAggregateResult(
            status=ResultStatus.INSUFFICIENT_DATA,
            reason=reason,
            identity=series.identity,
            settlement_date=day,
            coverage=coverage,
        )

    groups = _groups(series.observations)
    observations = [groups[start][0] for start in _present_expected_starts(day, groups)]
    return DailyAggregateResult(
        status=ResultStatus.AVAILABLE,
        reason=ResultReason.AVAILABLE,
        identity=series.identity,
        settlement_date=day,
        value=sum(observation.value for observation in observations)
        / len(observations),
        source_record_ids=[observation.source_record_id for observation in observations],
        coverage=coverage,
    )


def compare_history(
    reference_series: MetricSeries,
    *,
    reference_start: datetime,
    history_series: MetricSeries | None = None,
) -> HistoryComparisonSet:
    reference_at = _aware_half_hour(reference_start, "reference_start")
    history = history_series or reference_series
    reference_groups = _groups(reference_series.observations)
    reference_samples = reference_groups.get(reference_at, [])
    mismatches = _compatibility_mismatches(
        reference_series.identity,
        history.identity,
    )

    if not reference_samples:
        return _unavailable_comparison_set(
            identity=reference_series.identity,
            reference_start=reference_at,
            reason=ResultReason.MISSING_REFERENCE,
        )
    if len(reference_samples) > 1:
        return _unavailable_comparison_set(
            identity=reference_series.identity,
            reference_start=reference_at,
            reason=ResultReason.DUPLICATE_REFERENCE,
        )

    reference = reference_samples[0]
    if mismatches:
        return _incompatible_comparison_set(
            identity=reference_series.identity,
            reference=reference,
            mismatches=mismatches,
        )

    history_groups = _groups(history.observations)
    previous = _point_comparison(
        kind=PointComparisonKind.PREVIOUS_PERIOD,
        reference=reference,
        target_start=reference_at - INTERVAL,
        groups=history_groups,
    )
    yesterday = _same_local_comparison(
        kind=PointComparisonKind.SAME_TIME_YESTERDAY,
        reference=reference,
        days_back=1,
        groups=history_groups,
    )
    seven_days = _same_local_comparison(
        kind=PointComparisonKind.SAME_TIME_SEVEN_DAYS_AGO,
        reference=reference,
        days_back=7,
        groups=history_groups,
    )
    rolling = _rolling_comparison(reference, history_groups)
    return HistoryComparisonSet(
        identity=reference_series.identity,
        reference_start=reference_at,
        previous_period=previous,
        same_time_yesterday=yesterday,
        same_time_seven_days_ago=seven_days,
        rolling_28_days=rolling,
    )


def _point_comparison(
    *,
    kind: PointComparisonKind,
    reference: HalfHourObservation,
    target_start: datetime,
    groups: dict[datetime, list[HalfHourObservation]],
) -> PointComparisonResult:
    samples = groups.get(target_start, [])
    if not samples:
        return _unavailable_point(
            kind,
            reference,
            ResultReason.MISSING_COMPARISON,
            comparison_start=target_start,
            comparison_sample_count=0,
        )
    if len(samples) > 1:
        return _unavailable_point(
            kind,
            reference,
            ResultReason.DUPLICATE_COMPARISON,
            comparison_start=target_start,
            comparison_sample_count=len(samples),
        )
    comparison = samples[0]
    delta = reference.value - comparison.value
    percent = (
        delta / abs(comparison.value) * 100 if comparison.value != 0 else None
    )
    return PointComparisonResult(
        kind=kind,
        status=ResultStatus.AVAILABLE,
        reason=ResultReason.AVAILABLE,
        reference_start=reference.start.astimezone(UTC),
        comparison_start=target_start,
        reference_value=reference.value,
        comparison_value=comparison.value,
        comparison_sample_count=1,
        reference_minus_comparison=delta,
        percent_change_from_comparison=percent,
        source_record_ids=[reference.source_record_id, comparison.source_record_id],
    )


def _same_local_comparison(
    *,
    kind: PointComparisonKind,
    reference: HalfHourObservation,
    days_back: int,
    groups: dict[datetime, list[HalfHourObservation]],
) -> PointComparisonResult:
    target, reason = _same_local_target(reference.start, days_back=days_back)
    if target is None:
        return _unavailable_point(kind, reference, reason)
    return _point_comparison(
        kind=kind,
        reference=reference,
        target_start=target,
        groups=groups,
    )


def _rolling_comparison(
    reference: HalfHourObservation,
    groups: dict[datetime, list[HalfHourObservation]],
) -> Rolling28ComparisonResult:
    reference_local = reference.start.astimezone(LONDON)
    missing_dates: list[date] = []
    ambiguous_dates: list[date] = []
    duplicate_starts: list[datetime] = []
    samples: list[HalfHourObservation] = []
    raw_sample_count = 0

    for days_back in range(1, ROLLING_DAY_COUNT + 1):
        target_date = reference_local.date() - timedelta(days=days_back)
        target, reason = _same_local_target(reference.start, days_back=days_back)
        if target is None:
            if reason == ResultReason.AMBIGUOUS_LOCAL_TIME:
                ambiguous_dates.append(target_date)
            else:
                missing_dates.append(target_date)
            continue
        target_samples = groups.get(target, [])
        raw_sample_count += len(target_samples)
        if not target_samples:
            missing_dates.append(target_date)
        elif len(target_samples) > 1:
            duplicate_starts.append(target)
        else:
            samples.append(target_samples[0])

    fraction = len(samples) / ROLLING_DAY_COUNT
    sufficient = (
        fraction >= MINIMUM_COVERAGE_FRACTION
        and not duplicate_starts
        and not ambiguous_dates
    )
    coverage = RollingCoverage(
        raw_sample_count=raw_sample_count,
        valid_sample_count=len(samples),
        coverage_fraction=fraction,
        missing_dates=missing_dates,
        duplicate_starts=duplicate_starts,
        ambiguous_dates=ambiguous_dates,
        is_sufficient=sufficient,
    )
    if not sufficient:
        reason = (
            ResultReason.DUPLICATE_INTERVALS
            if duplicate_starts
            else ResultReason.AMBIGUOUS_LOCAL_TIME
            if ambiguous_dates
            else ResultReason.COVERAGE_BELOW_THRESHOLD
        )
        return Rolling28ComparisonResult(
            status=ResultStatus.INSUFFICIENT_DATA,
            reason=reason,
            reference_start=reference.start.astimezone(UTC),
            reference_value=reference.value,
            coverage=coverage,
        )

    values = sorted(sample.value for sample in samples)
    median = _quantile(values, 0.5)
    first_quartile = _quantile(values, 0.25)
    third_quartile = _quantile(values, 0.75)
    less = sum(value < reference.value for value in values)
    equal = sum(value == reference.value for value in values)
    percentile = (less + 0.5 * equal) / len(values) * 100
    return Rolling28ComparisonResult(
        status=ResultStatus.AVAILABLE,
        reason=ResultReason.AVAILABLE,
        reference_start=reference.start.astimezone(UTC),
        reference_value=reference.value,
        median=median,
        first_quartile=first_quartile,
        third_quartile=third_quartile,
        interquartile_range=third_quartile - first_quartile,
        reference_minus_median=reference.value - median,
        reference_percentile=percentile,
        source_record_ids=[reference.source_record_id]
        + [sample.source_record_id for sample in samples],
        coverage=coverage,
    )


def _same_local_target(
    reference_start: datetime,
    *,
    days_back: int,
) -> tuple[datetime | None, ResultReason]:
    reference_local = reference_start.astimezone(LONDON)
    target_date = reference_local.date() - timedelta(days=days_back)
    candidates = [
        interval.start.astimezone(UTC)
        for interval in expected_settlement_intervals(target_date)
        if (
            interval.start.astimezone(LONDON).hour,
            interval.start.astimezone(LONDON).minute,
        )
        == (reference_local.hour, reference_local.minute)
    ]
    if not candidates:
        return None, ResultReason.NONEXISTENT_LOCAL_TIME
    if len(candidates) == 1:
        return candidates[0], ResultReason.AVAILABLE

    same_offset = [
        candidate
        for candidate in candidates
        if candidate.astimezone(LONDON).utcoffset() == reference_local.utcoffset()
    ]
    if len(same_offset) == 1:
        return same_offset[0], ResultReason.AVAILABLE
    return None, ResultReason.AMBIGUOUS_LOCAL_TIME


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("quantiles require at least one value")
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _groups(
    observations: Iterable[HalfHourObservation],
) -> dict[datetime, list[HalfHourObservation]]:
    grouped: dict[datetime, list[HalfHourObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.start.astimezone(UTC)].append(observation)
    return dict(grouped)


def _present_expected_starts(
    day: date,
    groups: dict[datetime, list[HalfHourObservation]],
) -> list[datetime]:
    return [
        interval.start.astimezone(UTC)
        for interval in expected_settlement_intervals(day)
        if interval.start.astimezone(UTC) in groups
    ]


def _compatibility_mismatches(
    left: MetricSeriesIdentity,
    right: MetricSeriesIdentity,
) -> list[str]:
    fields = (
        "identity_version",
        "metric_id",
        "geography",
        "unit",
        "fact_class",
        "source_id",
        "methodology_version",
    )
    return [field for field in fields if getattr(left, field) != getattr(right, field)]


def _aware_half_hour(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    utc = value.astimezone(UTC)
    if utc.minute not in (0, 30) or utc.second or utc.microsecond:
        raise ValueError(f"{field} must be on a UTC half-hour boundary")
    return utc


def _unavailable_point(
    kind: PointComparisonKind,
    reference: HalfHourObservation,
    reason: ResultReason,
    *,
    comparison_start: datetime | None = None,
    comparison_sample_count: int = 0,
) -> PointComparisonResult:
    return PointComparisonResult(
        kind=kind,
        status=ResultStatus.INSUFFICIENT_DATA,
        reason=reason,
        reference_start=reference.start.astimezone(UTC),
        comparison_start=comparison_start,
        reference_value=reference.value,
        comparison_sample_count=comparison_sample_count,
        source_record_ids=[reference.source_record_id],
    )


def _unavailable_comparison_set(
    *,
    identity: MetricSeriesIdentity,
    reference_start: datetime,
    reason: ResultReason,
) -> HistoryComparisonSet:
    points = {
        kind: PointComparisonResult(
            kind=kind,
            status=ResultStatus.INSUFFICIENT_DATA,
            reason=reason,
            reference_start=reference_start,
        )
        for kind in PointComparisonKind
    }
    rolling_coverage = RollingCoverage(
        raw_sample_count=0,
        valid_sample_count=0,
        coverage_fraction=0,
        missing_dates=[
            reference_start.astimezone(LONDON).date() - timedelta(days=days_back)
            for days_back in range(1, ROLLING_DAY_COUNT + 1)
        ],
        is_sufficient=False,
    )
    return HistoryComparisonSet(
        identity=identity,
        reference_start=reference_start,
        previous_period=points[PointComparisonKind.PREVIOUS_PERIOD],
        same_time_yesterday=points[PointComparisonKind.SAME_TIME_YESTERDAY],
        same_time_seven_days_ago=points[
            PointComparisonKind.SAME_TIME_SEVEN_DAYS_AGO
        ],
        rolling_28_days=Rolling28ComparisonResult(
            status=ResultStatus.INSUFFICIENT_DATA,
            reason=reason,
            reference_start=reference_start,
            coverage=rolling_coverage,
        ),
    )


def _incompatible_comparison_set(
    *,
    identity: MetricSeriesIdentity,
    reference: HalfHourObservation,
    mismatches: list[str],
) -> HistoryComparisonSet:
    reference_start = reference.start.astimezone(UTC)
    points = {
        kind: PointComparisonResult(
            kind=kind,
            status=ResultStatus.INCOMPATIBLE_SERIES,
            reason=ResultReason.INCOMPATIBLE_SERIES,
            reference_start=reference_start,
            reference_value=reference.value,
            source_record_ids=[reference.source_record_id],
        )
        for kind in PointComparisonKind
    }
    return HistoryComparisonSet(
        identity=identity,
        reference_start=reference_start,
        compatibility_mismatches=mismatches,
        previous_period=points[PointComparisonKind.PREVIOUS_PERIOD],
        same_time_yesterday=points[PointComparisonKind.SAME_TIME_YESTERDAY],
        same_time_seven_days_ago=points[
            PointComparisonKind.SAME_TIME_SEVEN_DAYS_AGO
        ],
        rolling_28_days=Rolling28ComparisonResult(
            status=ResultStatus.INCOMPATIBLE_SERIES,
            reason=ResultReason.INCOMPATIBLE_SERIES,
            reference_start=reference_start,
            reference_value=reference.value,
            coverage=RollingCoverage(
                raw_sample_count=0,
                valid_sample_count=0,
                coverage_fraction=0,
                is_sufficient=False,
            ),
        ),
    )
