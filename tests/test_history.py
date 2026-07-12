from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.history import (
    HalfHourObservation,
    MetricSeries,
    MetricSeriesIdentity,
    ResultReason,
    ResultStatus,
    aggregate_daily_mean,
    assess_daily_coverage,
    compare_history,
    expected_settlement_intervals,
)


LONDON = ZoneInfo("Europe/London")
REFERENCE_START = datetime(2026, 7, 11, 14, tzinfo=UTC)
IDENTITY = MetricSeriesIdentity(
    metric_id="carbon.intensity.gb",
    geography="GB",
    unit="gCO2/kWh",
    fact_class="estimated",
    source_id="neso.carbon",
    methodology_version="neso-national-v2",
)


def observation(
    start: datetime,
    value: float,
    *,
    record_id: str | None = None,
) -> HalfHourObservation:
    return HalfHourObservation(
        start=start,
        end=start + timedelta(minutes=30),
        value=value,
        source_record_id=record_id or f"record:{start.isoformat()}:{value}",
    )


def daily_series(
    day: date,
    *,
    missing_periods: set[int] | None = None,
    duplicate_periods: set[int] | None = None,
    include_out_of_range: bool = False,
) -> MetricSeries:
    missing = missing_periods or set()
    duplicates = duplicate_periods or set()
    observations: list[HalfHourObservation] = []
    for interval in expected_settlement_intervals(day):
        if interval.settlement_period in missing:
            continue
        sample = observation(
            interval.start,
            float(interval.settlement_period),
            record_id=f"{day}:sp-{interval.settlement_period}",
        )
        observations.append(sample)
        if interval.settlement_period in duplicates:
            observations.append(
                sample.model_copy(
                    update={
                        "source_record_id": (
                            f"{day}:sp-{interval.settlement_period}:duplicate"
                        )
                    }
                )
            )
    if include_out_of_range:
        outside = expected_settlement_intervals(day + timedelta(days=1))[0]
        observations.append(
            observation(outside.start, 999, record_id="outside-requested-day")
        )
    return MetricSeries(identity=IDENTITY, observations=observations)


def reference_series(value: float = 100) -> MetricSeries:
    return MetricSeries(
        identity=IDENTITY,
        observations=[observation(REFERENCE_START, value, record_id="reference")],
    )


def rolling_history(
    values: list[float],
    *,
    missing_days_back: set[int] | None = None,
    duplicate_days_back: set[int] | None = None,
    include_previous_period: float | None = None,
    identity: MetricSeriesIdentity = IDENTITY,
) -> MetricSeries:
    missing = missing_days_back or set()
    duplicates = duplicate_days_back or set()
    observations: list[HalfHourObservation] = []
    for days_back, value in enumerate(values, start=1):
        if days_back in missing:
            continue
        start = REFERENCE_START - timedelta(days=days_back)
        sample = observation(
            start,
            value,
            record_id=f"history:day-{days_back}",
        )
        observations.append(sample)
        if days_back in duplicates:
            observations.append(
                sample.model_copy(
                    update={"source_record_id": f"history:day-{days_back}:duplicate"}
                )
            )
    if include_previous_period is not None:
        observations.append(
            observation(
                REFERENCE_START - timedelta(minutes=30),
                include_previous_period,
                record_id="history:previous-period",
            )
        )
    return MetricSeries(identity=identity, observations=observations)


@pytest.mark.parametrize(
    ("day", "expected_count"),
    [
        (date(2026, 1, 15), 48),
        (date(2026, 3, 29), 46),
        (date(2026, 10, 25), 50),
    ],
)
def test_expected_settlement_intervals_follow_london_dst(
    day: date,
    expected_count: int,
) -> None:
    intervals = expected_settlement_intervals(day)

    assert len(intervals) == expected_count
    assert [item.settlement_period for item in intervals] == list(
        range(1, expected_count + 1)
    )
    assert all(item.end - item.start == timedelta(minutes=30) for item in intervals)
    assert all(
        current.start == previous.end
        for previous, current in zip(intervals, intervals[1:])
    )
    assert all(item.start.astimezone(LONDON).date() == day for item in intervals)
    assert intervals[0].start.astimezone(LONDON).hour == 0
    assert intervals[-1].end.astimezone(LONDON).date() == day + timedelta(days=1)


def test_spring_day_has_no_one_oclock_periods_and_fall_has_four() -> None:
    spring_local_starts = [
        item.start.astimezone(LONDON)
        for item in expected_settlement_intervals(date(2026, 3, 29))
    ]
    fall_local_starts = [
        item.start.astimezone(LONDON)
        for item in expected_settlement_intervals(date(2026, 10, 25))
    ]

    assert [item for item in spring_local_starts if item.hour == 1] == []
    assert len([item for item in fall_local_starts if item.hour == 1]) == 4
    assert {item.utcoffset() for item in fall_local_starts if item.hour == 1} == {
        timedelta(hours=1),
        timedelta(0),
    }


def test_calendar_rejects_datetime_instead_of_silently_truncating() -> None:
    with pytest.raises(TypeError, match="date, not a datetime"):
        expected_settlement_intervals(datetime(2026, 7, 11, tzinfo=UTC))


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "start": REFERENCE_START.replace(tzinfo=None),
            "end": (REFERENCE_START + timedelta(minutes=30)).replace(tzinfo=None),
            "value": 1,
            "source_record_id": "naive",
        },
        {
            "start": REFERENCE_START,
            "end": REFERENCE_START + timedelta(minutes=15),
            "value": 1,
            "source_record_id": "short",
        },
        {
            "start": REFERENCE_START + timedelta(minutes=15),
            "end": REFERENCE_START + timedelta(minutes=45),
            "value": 1,
            "source_record_id": "unaligned",
        },
        {
            "start": REFERENCE_START,
            "end": REFERENCE_START + timedelta(minutes=30),
            "value": float("nan"),
            "source_record_id": "nan",
        },
    ],
)
def test_half_hour_observation_rejects_invalid_inputs(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        HalfHourObservation(**kwargs)


def test_complete_daily_mean_exposes_samples_and_full_coverage() -> None:
    day = date(2026, 7, 11)
    result = aggregate_daily_mean(daily_series(day), day)

    assert result.status == ResultStatus.AVAILABLE
    assert result.reason == ResultReason.AVAILABLE
    assert result.value == 24.5
    assert len(result.source_record_ids) == 48
    assert result.coverage.expected_interval_count == 48
    assert result.coverage.raw_sample_count == 48
    assert result.coverage.unique_interval_count == 48
    assert result.coverage.coverage_fraction == 1
    assert result.coverage.missing_starts == []
    assert result.coverage.is_sufficient is True
    assert result.methodology_version == "50hz.history.daily-mean.v1"
    assert result.identity.identity_version == "50hz.metric-series.v1"


@pytest.mark.parametrize(
    ("day", "missing_count", "expected_count", "is_available"),
    [
        (date(2026, 7, 11), 2, 48, True),
        (date(2026, 7, 11), 3, 48, False),
        (date(2026, 3, 29), 2, 46, True),
        (date(2026, 3, 29), 3, 46, False),
        (date(2026, 10, 25), 2, 50, True),
        (date(2026, 10, 25), 3, 50, False),
    ],
)
def test_daily_aggregate_enforces_95_percent_coverage_on_all_day_lengths(
    day: date,
    missing_count: int,
    expected_count: int,
    is_available: bool,
) -> None:
    missing = set(range(1, missing_count + 1))
    result = aggregate_daily_mean(
        daily_series(day, missing_periods=missing),
        day,
    )

    assert result.coverage.expected_interval_count == expected_count
    assert len(result.coverage.missing_starts) == missing_count
    assert result.coverage.coverage_fraction == pytest.approx(
        (expected_count - missing_count) / expected_count
    )
    assert (result.status == ResultStatus.AVAILABLE) is is_available
    assert (result.value is not None) is is_available
    if not is_available:
        assert result.reason == ResultReason.COVERAGE_BELOW_THRESHOLD
        assert result.value is None
        assert result.source_record_ids == []


def test_duplicate_daily_interval_is_explicit_and_invalidates_aggregate() -> None:
    day = date(2026, 7, 11)
    series = daily_series(day, duplicate_periods={12})
    coverage = assess_daily_coverage(series, day)
    result = aggregate_daily_mean(series, day)

    assert coverage.raw_sample_count == 49
    assert coverage.unique_interval_count == 48
    assert coverage.coverage_fraction == 1
    assert coverage.duplicate_starts == [
        expected_settlement_intervals(day)[11].start
    ]
    assert coverage.is_sufficient is False
    assert result.status == ResultStatus.INSUFFICIENT_DATA
    assert result.reason == ResultReason.DUPLICATE_INTERVALS
    assert result.value is None


def test_out_of_range_observation_does_not_inflate_daily_coverage() -> None:
    day = date(2026, 7, 11)
    coverage = assess_daily_coverage(
        daily_series(day, missing_periods={1}, include_out_of_range=True),
        day,
    )

    assert coverage.raw_sample_count == 47
    assert coverage.unique_interval_count == 47
    assert coverage.out_of_range_sample_count == 1
    assert coverage.coverage_fraction == pytest.approx(47 / 48)


def test_point_comparisons_use_exact_compatible_samples() -> None:
    history = rolling_history(
        [80, 70, 60, 50] + [40] * 24,
        include_previous_period=90,
    )
    result = compare_history(
        reference_series(100),
        reference_start=REFERENCE_START,
        history_series=history,
    )

    assert result.previous_period.status == ResultStatus.AVAILABLE
    assert result.previous_period.comparison_value == 90
    assert result.previous_period.comparison_sample_count == 1
    assert result.previous_period.reference_minus_comparison == 10
    assert result.previous_period.percent_change_from_comparison == pytest.approx(
        100 / 9
    )
    assert result.same_time_yesterday.comparison_value == 80
    assert result.same_time_yesterday.reference_minus_comparison == 20
    assert result.same_time_seven_days_ago.comparison_value == 40
    assert result.compatibility_mismatches == []
    assert result.methodology_version == "50hz.history.comparisons.v1"


def test_zero_comparison_is_available_but_has_no_undefined_percent() -> None:
    history = MetricSeries(
        identity=IDENTITY,
        observations=[
            observation(
                REFERENCE_START - timedelta(minutes=30),
                0,
                record_id="zero-baseline",
            )
        ],
    )
    result = compare_history(
        reference_series(5),
        reference_start=REFERENCE_START,
        history_series=history,
    )

    assert result.previous_period.status == ResultStatus.AVAILABLE
    assert result.previous_period.comparison_value == 0
    assert result.previous_period.reference_minus_comparison == 5
    assert result.previous_period.percent_change_from_comparison is None


def test_missing_point_comparisons_return_none_never_synthetic_zero() -> None:
    result = compare_history(
        reference_series(),
        reference_start=REFERENCE_START,
        history_series=MetricSeries(identity=IDENTITY, observations=[]),
    )

    for comparison in (
        result.previous_period,
        result.same_time_yesterday,
        result.same_time_seven_days_ago,
    ):
        assert comparison.status == ResultStatus.INSUFFICIENT_DATA
        assert comparison.reason in {
            ResultReason.MISSING_COMPARISON,
            ResultReason.NONEXISTENT_LOCAL_TIME,
        }
        assert comparison.comparison_value is None
        assert comparison.comparison_sample_count == 0
        assert comparison.reference_minus_comparison is None
        assert comparison.percent_change_from_comparison is None
    assert result.rolling_28_days.status == ResultStatus.INSUFFICIENT_DATA
    assert result.rolling_28_days.median is None
    assert result.rolling_28_days.reference_percentile is None


def test_duplicate_target_interval_is_not_arbitrarily_selected() -> None:
    target = observation(
        REFERENCE_START - timedelta(minutes=30),
        90,
        record_id="target-a",
    )
    history = MetricSeries(
        identity=IDENTITY,
        observations=[
            target,
            target.model_copy(update={"source_record_id": "target-b", "value": 80}),
        ],
    )
    result = compare_history(
        reference_series(),
        reference_start=REFERENCE_START,
        history_series=history,
    )

    assert result.previous_period.status == ResultStatus.INSUFFICIENT_DATA
    assert result.previous_period.reason == ResultReason.DUPLICATE_COMPARISON
    assert result.previous_period.comparison_sample_count == 2
    assert result.previous_period.comparison_value is None
    assert result.previous_period.reference_minus_comparison is None


def test_duplicate_reference_invalidates_every_comparison() -> None:
    reference = observation(REFERENCE_START, 100, record_id="reference-a")
    references = MetricSeries(
        identity=IDENTITY,
        observations=[
            reference,
            reference.model_copy(update={"source_record_id": "reference-b"}),
        ],
    )
    result = compare_history(
        references,
        reference_start=REFERENCE_START,
        history_series=rolling_history([50] * 28),
    )

    assert result.previous_period.reason == ResultReason.DUPLICATE_REFERENCE
    assert result.same_time_yesterday.reason == ResultReason.DUPLICATE_REFERENCE
    assert result.same_time_seven_days_ago.reason == ResultReason.DUPLICATE_REFERENCE
    assert result.rolling_28_days.reason == ResultReason.DUPLICATE_REFERENCE
    assert result.rolling_28_days.median is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity_version", "50hz.metric-series.v2"),
        ("metric_id", "demand.national"),
        ("geography", "region-13"),
        ("unit", "MW"),
        ("fact_class", "forecast"),
        ("source_id", "another.source"),
        ("methodology_version", "another-method-v3"),
    ],
)
def test_incompatible_series_never_emit_comparison_values(
    field: str,
    value: str,
) -> None:
    incompatible_identity = IDENTITY.model_copy(update={field: value})
    result = compare_history(
        reference_series(),
        reference_start=REFERENCE_START,
        history_series=rolling_history(
            [50] * 28,
            include_previous_period=50,
            identity=incompatible_identity,
        ),
    )

    assert result.compatibility_mismatches == [field]
    for comparison in (
        result.previous_period,
        result.same_time_yesterday,
        result.same_time_seven_days_ago,
    ):
        assert comparison.status == ResultStatus.INCOMPATIBLE_SERIES
        assert comparison.reason == ResultReason.INCOMPATIBLE_SERIES
        assert comparison.comparison_value is None
        assert comparison.reference_minus_comparison is None
    assert result.rolling_28_days.status == ResultStatus.INCOMPATIBLE_SERIES
    assert result.rolling_28_days.median is None
    assert result.rolling_28_days.reference_percentile is None


def test_rolling_28_day_distribution_uses_linear_quartiles_and_midrank() -> None:
    result = compare_history(
        reference_series(14.5),
        reference_start=REFERENCE_START,
        history_series=rolling_history([float(value) for value in range(1, 29)]),
    ).rolling_28_days

    assert result.status == ResultStatus.AVAILABLE
    assert result.coverage.valid_sample_count == 28
    assert result.coverage.coverage_fraction == 1
    assert result.median == 14.5
    assert result.first_quartile == 7.75
    assert result.third_quartile == 21.25
    assert result.interquartile_range == 13.5
    assert result.reference_minus_median == 0
    assert result.reference_percentile == 50
    assert len(result.source_record_ids) == 29


def test_rolling_percentile_ties_use_empirical_midrank() -> None:
    result = compare_history(
        reference_series(10),
        reference_start=REFERENCE_START,
        history_series=rolling_history([10] * 14 + [20] * 14),
    ).rolling_28_days

    assert result.status == ResultStatus.AVAILABLE
    assert result.first_quartile == 10
    assert result.median == 15
    assert result.third_quartile == 20
    assert result.interquartile_range == 10
    assert result.reference_percentile == 25


@pytest.mark.parametrize(
    ("missing", "available"),
    [
        ({1}, True),
        ({1, 2}, False),
    ],
)
def test_rolling_distribution_requires_95_percent_sample_coverage(
    missing: set[int],
    available: bool,
) -> None:
    result = compare_history(
        reference_series(),
        reference_start=REFERENCE_START,
        history_series=rolling_history(
            [float(value) for value in range(1, 29)],
            missing_days_back=missing,
        ),
    ).rolling_28_days

    expected_valid = 28 - len(missing)
    assert result.coverage.valid_sample_count == expected_valid
    assert result.coverage.coverage_fraction == pytest.approx(expected_valid / 28)
    assert result.coverage.is_sufficient is available
    assert (result.status == ResultStatus.AVAILABLE) is available
    assert (result.median is not None) is available
    if not available:
        assert result.reason == ResultReason.COVERAGE_BELOW_THRESHOLD


def test_duplicate_rolling_sample_invalidates_statistics_even_with_coverage() -> None:
    result = compare_history(
        reference_series(),
        reference_start=REFERENCE_START,
        history_series=rolling_history(
            [50] * 28,
            duplicate_days_back={5},
        ),
    ).rolling_28_days

    assert result.coverage.valid_sample_count == 27
    assert result.coverage.coverage_fraction == pytest.approx(27 / 28)
    assert result.coverage.duplicate_starts == [
        REFERENCE_START - timedelta(days=5)
    ]
    assert result.coverage.is_sufficient is False
    assert result.status == ResultStatus.INSUFFICIENT_DATA
    assert result.reason == ResultReason.DUPLICATE_INTERVALS
    assert result.median is None


def test_spring_forward_nonexistent_same_time_is_explicitly_insufficient() -> None:
    reference_start = datetime(2026, 3, 30, 1, 0, tzinfo=LONDON)
    references = MetricSeries(
        identity=IDENTITY,
        observations=[observation(reference_start, 100, record_id="spring-reference")],
    )
    result = compare_history(
        references,
        reference_start=reference_start,
        history_series=MetricSeries(identity=IDENTITY, observations=[]),
    )

    assert result.same_time_yesterday.status == ResultStatus.INSUFFICIENT_DATA
    assert (
        result.same_time_yesterday.reason
        == ResultReason.NONEXISTENT_LOCAL_TIME
    )
    assert result.same_time_yesterday.comparison_start is None
    assert result.same_time_yesterday.comparison_value is None


def test_fall_back_same_time_matches_reference_utc_offset() -> None:
    reference_start = datetime(2026, 10, 26, 1, 0, tzinfo=LONDON)
    expected_target = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
    references = MetricSeries(
        identity=IDENTITY,
        observations=[observation(reference_start, 100, record_id="fall-reference")],
    )
    history = MetricSeries(
        identity=IDENTITY,
        observations=[observation(expected_target, 70, record_id="fall-target-gmt")],
    )
    result = compare_history(
        references,
        reference_start=reference_start,
        history_series=history,
    )

    assert result.same_time_yesterday.status == ResultStatus.AVAILABLE
    assert result.same_time_yesterday.comparison_start == expected_target
    assert result.same_time_yesterday.comparison_value == 70
    assert result.same_time_yesterday.reference_minus_comparison == 30


def test_reference_start_accepts_equivalent_london_timezone_instant() -> None:
    london_reference = REFERENCE_START.astimezone(LONDON)
    result = compare_history(
        reference_series(),
        reference_start=london_reference,
        history_series=rolling_history([50] * 28, include_previous_period=90),
    )

    assert result.reference_start == REFERENCE_START
    assert result.previous_period.status == ResultStatus.AVAILABLE


@pytest.mark.parametrize(
    "reference_start",
    [
        REFERENCE_START.replace(tzinfo=None),
        REFERENCE_START + timedelta(minutes=15),
    ],
)
def test_reference_start_requires_aware_half_hour(reference_start: datetime) -> None:
    with pytest.raises(ValueError):
        compare_history(reference_series(), reference_start=reference_start)


def test_results_expose_statistics_without_causal_or_unusual_claims() -> None:
    result = compare_history(
        reference_series(),
        reference_start=REFERENCE_START,
        history_series=rolling_history([50] * 28, include_previous_period=50),
    )
    payload = result.model_dump(mode="json")

    assert "unusual" not in str(payload).lower()
    assert "cause" not in str(payload).lower()
    assert result.result_version == "1"
    assert result.rolling_28_days.result_version == "1"
    assert result.methodology.timezone == "Europe/London"
    assert result.methodology.minimum_coverage_fraction == 0.95
    assert "midrank" in result.methodology.percentile_rule.lower()
