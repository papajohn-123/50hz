from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.charging import (
    CarbonForecastPoint,
    CarbonForecastSeries,
    FlexibleUseStatus,
    StartNowComparisonStatus,
    compare_charging,
    find_cleanest_window,
    plan_flexible_use,
)


START = datetime(2026, 7, 11, 20, tzinfo=UTC)


def points(values: list[float]) -> list[CarbonForecastPoint]:
    return [
        CarbonForecastPoint(
            start=START + timedelta(minutes=30 * index),
            end=START + timedelta(minutes=30 * (index + 1)),
            intensity_gco2_kwh=value,
            source_record_id=f"carbon:{index}",
        )
        for index, value in enumerate(values)
    ]


def series(
    values: list[float],
    *,
    start: datetime = START,
    series_id: str = "neso-carbon:region-13",
    geography: str = "region-13",
    source_id: str = "neso.carbon",
    methodology_version: str = "neso-regional-v2",
    vintage_at: datetime | None = START - timedelta(minutes=5),
) -> CarbonForecastSeries:
    forecast_points = [
        CarbonForecastPoint(
            start=start + timedelta(minutes=30 * index),
            end=start + timedelta(minutes=30 * (index + 1)),
            intensity_gco2_kwh=value,
            source_record_id=f"{series_id}:{index}",
        )
        for index, value in enumerate(values)
    ]
    return CarbonForecastSeries(
        series_id=series_id,
        geography=geography,
        source_id=source_id,
        methodology_version=methodology_version,
        vintage_at=vintage_at,
        points=forecast_points,
    )


def test_cleanest_window_uses_contiguous_weighted_periods() -> None:
    window = find_cleanest_window(points([120, 80, 40, 30, 70]), duration=timedelta(hours=1))
    assert window is not None
    assert window.start == START + timedelta(hours=1)
    assert window.average_intensity_gco2_kwh == 35
    assert window.source_record_ids == ["carbon:2", "carbon:3"]


def test_gap_does_not_produce_false_window() -> None:
    forecast = [points([20, 30, 100])[0], points([20, 30, 100])[2]]
    assert find_cleanest_window(forecast, duration=timedelta(hours=1)) is None


def test_plan_respects_inclusive_earliest_start_and_latest_finish() -> None:
    result = plan_flexible_use(
        series([5, 100, 80, 20, 20]),
        duration=timedelta(hours=1),
        earliest_start=START + timedelta(minutes=30),
        latest_finish=START + timedelta(hours=2, minutes=30),
        start_now=START,
    )

    assert result.recommended_window is not None
    assert result.recommended_window.start == START + timedelta(hours=1, minutes=30)
    assert result.recommended_window.end == START + timedelta(hours=2, minutes=30)
    assert result.recommended_window.average_intensity_gco2_kwh == 20
    assert result.coverage.candidate_start_count == 3
    assert result.coverage.complete_candidate_count == 3


def test_gap_is_reported_and_only_crossing_candidates_are_rejected() -> None:
    forecast = series([20, 30, 999, 10, 10])
    forecast.points.pop(2)

    result = plan_flexible_use(
        forecast,
        duration=timedelta(hours=1),
        earliest_start=START,
        latest_finish=START + timedelta(hours=2, minutes=30),
        start_now=START,
    )

    assert result.recommended_window is not None
    assert result.recommended_window.start == START + timedelta(hours=1, minutes=30)
    assert result.coverage.expected_interval_count == 5
    assert result.coverage.available_interval_count == 4
    assert result.coverage.coverage_fraction == 0.8
    assert result.coverage.gap_starts == [START + timedelta(hours=1)]
    assert result.coverage.candidate_start_count == 4
    assert result.coverage.complete_candidate_count == 2


def test_insufficient_horizon_returns_explicit_unavailable_result() -> None:
    result = plan_flexible_use(
        series([100, 80, 60]),
        duration=timedelta(hours=2),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1, minutes=30),
        start_now=START,
    )

    assert result.status == FlexibleUseStatus.INSUFFICIENT_COVERAGE
    assert result.recommended_window is None
    assert result.coverage.required_interval_count == 4
    assert result.coverage.candidate_start_count == 0
    assert result.coverage.complete_candidate_count == 0
    assert "not enough contiguous" in result.summary


def test_gaps_can_leave_no_complete_candidate_despite_partial_coverage() -> None:
    forecast = series([20, 30, 40])
    forecast.points.pop(1)

    result = plan_flexible_use(
        forecast,
        duration=timedelta(hours=1),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1, minutes=30),
        start_now=START,
    )

    assert result.status == FlexibleUseStatus.INSUFFICIENT_COVERAGE
    assert result.recommended_window is None
    assert result.coverage.coverage_fraction == pytest.approx(2 / 3, abs=0.0001)
    assert result.coverage.complete_candidate_count == 0


def test_equal_average_ties_choose_the_earliest_start() -> None:
    result = plan_flexible_use(
        series([30, 10, 30, 10]),
        duration=timedelta(hours=1),
        earliest_start=START,
        latest_finish=START + timedelta(hours=2),
        start_now=START,
    )

    assert result.recommended_window is not None
    assert result.recommended_window.start == START
    assert result.recommended_window.average_intensity_gco2_kwh == 20


def test_start_now_comparison_uses_the_exact_time_with_partial_half_hours() -> None:
    result = plan_flexible_use(
        series([100, 50, 0]),
        duration=timedelta(hours=1),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1, minutes=30),
        start_now=START + timedelta(minutes=15),
    )

    assert result.recommended_window is not None
    assert result.recommended_window.start == START + timedelta(minutes=30)
    assert result.recommended_window.average_intensity_gco2_kwh == 25
    assert result.comparison.start_now_window is not None
    assert result.comparison.start_now_window.start == START + timedelta(minutes=15)
    assert result.comparison.start_now_window.average_intensity_gco2_kwh == 50
    assert result.comparison.start_now_window.source_record_ids == [
        "neso-carbon:region-13:0",
        "neso-carbon:region-13:1",
        "neso-carbon:region-13:2",
    ]
    assert result.comparison.start_now_minus_recommended_gco2_kwh == 25
    assert result.comparison.percent_lower_than_start_now == 50
    assert result.comparison.is_meaningful is True
    assert result.status == FlexibleUseStatus.LOWER_CARBON_WINDOW


def test_compatible_start_now_comparison_exposes_delta_and_percent() -> None:
    result = plan_flexible_use(
        series([120, 120, 40, 40]),
        duration=timedelta(hours=1),
        earliest_start=START,
        latest_finish=START + timedelta(hours=2),
        start_now=START,
    )

    assert result.comparison.status == StartNowComparisonStatus.COMPATIBLE
    assert result.comparison.start_now_minus_recommended_gco2_kwh == 80
    assert result.comparison.percent_lower_than_start_now == 66.67
    assert result.comparison.is_meaningful is True
    assert result.status == FlexibleUseStatus.LOWER_CARBON_WINDOW


def test_small_compatible_delta_states_no_meaningful_difference() -> None:
    result = plan_flexible_use(
        series([100, 98]),
        duration=timedelta(minutes=30),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1),
        start_now=START,
    )

    assert result.recommended_window is not None
    assert result.recommended_window.start == START + timedelta(minutes=30)
    assert result.comparison.start_now_minus_recommended_gco2_kwh == 2
    assert result.comparison.percent_lower_than_start_now == 2
    assert result.comparison.is_meaningful is False
    assert result.status == FlexibleUseStatus.NO_MEANINGFUL_DIFFERENCE
    assert "no meaningful" in result.summary.lower()


def test_meaningful_threshold_is_inclusive_and_versioned() -> None:
    result = plan_flexible_use(
        series([100, 95]),
        duration=timedelta(minutes=30),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1),
        start_now=START,
    )

    assert result.comparison.is_meaningful is True
    assert result.status == FlexibleUseStatus.LOWER_CARBON_WINDOW
    assert result.result_version == "1"
    assert result.methodology.version == "50hz.local.flexible-use.v1"
    assert result.methodology.required_window_coverage_percent == 100
    assert result.methodology.meaningful_absolute_delta_gco2_kwh == 5
    assert result.methodology.meaningful_percent_delta == 5


@pytest.mark.parametrize(
    ("field", "incompatible_value"),
    [
        ("series_id", "neso-carbon:GB"),
        ("geography", "GB"),
        ("source_id", "another.source"),
        ("methodology_version", "neso-national-v3"),
        ("vintage_at", START - timedelta(minutes=35)),
    ],
)
def test_incompatible_start_now_series_suppresses_delta_and_percent(
    field: str,
    incompatible_value: object,
) -> None:
    recommended_series = series([120, 40])
    comparison_series = recommended_series.model_copy(
        update={field: incompatible_value}
    )

    result = plan_flexible_use(
        recommended_series,
        duration=timedelta(minutes=30),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1),
        start_now=START,
        start_now_forecast=comparison_series,
    )

    assert result.recommended_window is not None
    assert result.comparison.status == StartNowComparisonStatus.INCOMPATIBLE_SERIES
    assert result.comparison.start_now_window is not None
    assert result.comparison.incompatibility_fields == [field]
    assert result.comparison.start_now_minus_recommended_gco2_kwh is None
    assert result.comparison.percent_lower_than_start_now is None
    assert result.comparison.is_meaningful is None
    assert result.status == FlexibleUseStatus.WINDOW_FOUND


def test_distinct_but_identically_scoped_series_are_compatible() -> None:
    recommendation = series([100, 20])
    baseline = recommendation.model_copy(deep=True)

    result = plan_flexible_use(
        recommendation,
        duration=timedelta(minutes=30),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1),
        start_now=START,
        start_now_forecast=baseline,
    )

    assert result.comparison.status == StartNowComparisonStatus.COMPATIBLE
    assert result.comparison.start_now_minus_recommended_gco2_kwh == 80


def test_missing_start_now_coverage_suppresses_comparison_only() -> None:
    result = plan_flexible_use(
        series([100, 20]),
        duration=timedelta(minutes=30),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1),
        start_now=START - timedelta(minutes=15),
    )

    assert result.recommended_window is not None
    assert result.comparison.status == StartNowComparisonStatus.INSUFFICIENT_COVERAGE
    assert result.comparison.start_now_window is None
    assert result.comparison.start_now_minus_recommended_gco2_kwh is None
    assert result.comparison.percent_lower_than_start_now is None
    assert result.status == FlexibleUseStatus.WINDOW_FOUND


def test_zero_start_now_baseline_has_delta_but_no_undefined_percent() -> None:
    result = plan_flexible_use(
        series([0, 0]),
        duration=timedelta(minutes=30),
        earliest_start=START,
        latest_finish=START + timedelta(hours=1),
        start_now=START,
    )

    assert result.comparison.status == StartNowComparisonStatus.COMPATIBLE
    assert result.comparison.start_now_minus_recommended_gco2_kwh == 0
    assert result.comparison.percent_lower_than_start_now is None
    assert result.comparison.is_meaningful is False
    assert result.status == FlexibleUseStatus.NO_MEANINGFUL_DIFFERENCE


def test_timezone_aware_bounds_and_points_are_compared_by_instant() -> None:
    london = ZoneInfo("Europe/London")
    local_start = START.astimezone(london)
    forecast = series([100, 20], start=local_start)

    result = plan_flexible_use(
        forecast,
        duration=timedelta(minutes=30),
        earliest_start=local_start,
        latest_finish=(START + timedelta(hours=1)).astimezone(london),
        start_now=local_start + timedelta(minutes=10),
    )

    assert result.earliest_start == START
    assert result.latest_finish == START + timedelta(hours=1)
    assert result.recommended_window is not None
    assert result.recommended_window.start == START + timedelta(minutes=30)
    assert result.comparison.start_now_window is not None
    assert result.comparison.start_now_window.start == START + timedelta(minutes=10)


@pytest.mark.parametrize(
    "utc_start",
    [
        datetime(2026, 3, 29, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 0, tzinfo=UTC),
    ],
)
def test_dst_boundaries_keep_absolute_half_hour_contiguity(
    utc_start: datetime,
) -> None:
    london = ZoneInfo("Europe/London")
    forecast = series([100, 80, 20, 20], start=utc_start)

    result = plan_flexible_use(
        forecast,
        duration=timedelta(hours=1),
        earliest_start=utc_start.astimezone(london),
        latest_finish=(utc_start + timedelta(hours=2)).astimezone(london),
        start_now=utc_start.astimezone(london),
    )

    assert result.coverage.expected_interval_count == 4
    assert result.coverage.available_interval_count == 4
    assert result.coverage.coverage_fraction == 1
    assert result.recommended_window is not None
    assert result.recommended_window.start == utc_start + timedelta(hours=1)
    assert result.recommended_window.end == utc_start + timedelta(hours=2)


@pytest.mark.parametrize(
    "duration",
    [
        timedelta(0),
        timedelta(minutes=-30),
        timedelta(minutes=45),
        timedelta(minutes=30, seconds=1),
    ],
)
def test_invalid_duration_is_rejected(duration: timedelta) -> None:
    with pytest.raises(ValueError):
        plan_flexible_use(
            series([100, 20]),
            duration=duration,
            earliest_start=START,
            latest_finish=START + timedelta(hours=1),
            start_now=START,
        )


def test_interruptible_request_is_explicitly_rejected() -> None:
    with pytest.raises(ValueError, match="only continuous"):
        plan_flexible_use(
            series([100, 20]),
            duration=timedelta(minutes=30),
            earliest_start=START,
            latest_finish=START + timedelta(hours=1),
            start_now=START,
            continuous=False,
        )


@pytest.mark.parametrize("field", ["earliest_start", "latest_finish"])
def test_unaligned_search_bounds_are_rejected(field: str) -> None:
    kwargs = {
        "earliest_start": START,
        "latest_finish": START + timedelta(hours=1),
    }
    kwargs[field] += timedelta(minutes=15)
    with pytest.raises(ValueError, match="half-hour boundary"):
        plan_flexible_use(
            series([100, 20, 10]),
            duration=timedelta(minutes=30),
            start_now=START,
            **kwargs,
        )


@pytest.mark.parametrize("field", ["earliest_start", "latest_finish", "start_now"])
def test_naive_planner_times_are_rejected(field: str) -> None:
    kwargs = {
        "earliest_start": START,
        "latest_finish": START + timedelta(hours=1),
        "start_now": START,
    }
    kwargs[field] = kwargs[field].replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        plan_flexible_use(
            series([100, 20]),
            duration=timedelta(minutes=30),
            **kwargs,
        )


def test_latest_finish_must_follow_earliest_start() -> None:
    with pytest.raises(ValueError, match="must follow"):
        plan_flexible_use(
            series([100, 20]),
            duration=timedelta(minutes=30),
            earliest_start=START + timedelta(hours=1),
            latest_finish=START,
            start_now=START,
        )


def test_non_half_hour_forecast_interval_is_rejected() -> None:
    forecast = series([100])
    forecast.points[0] = forecast.points[0].model_copy(
        update={"end": START + timedelta(minutes=15)}
    )
    with pytest.raises(ValueError, match="exactly 30 minutes"):
        plan_flexible_use(
            forecast,
            duration=timedelta(minutes=30),
            earliest_start=START,
            latest_finish=START + timedelta(minutes=30),
            start_now=START,
        )


def test_unaligned_forecast_point_is_rejected() -> None:
    forecast = series([100])
    forecast.points[0] = forecast.points[0].model_copy(
        update={
            "start": START + timedelta(minutes=15),
            "end": START + timedelta(minutes=45),
        }
    )
    with pytest.raises(ValueError, match="start on half-hour"):
        plan_flexible_use(
            forecast,
            duration=timedelta(minutes=30),
            earliest_start=START,
            latest_finish=START + timedelta(hours=1),
            start_now=START,
        )


def test_overlapping_forecast_points_are_rejected() -> None:
    forecast = series([100])
    forecast.points.append(forecast.points[0].model_copy())
    with pytest.raises(ValueError, match="must not overlap"):
        plan_flexible_use(
            forecast,
            duration=timedelta(minutes=30),
            earliest_start=START,
            latest_finish=START + timedelta(minutes=30),
            start_now=START,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_carbon_intensity_is_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        CarbonForecastPoint(
            start=START,
            end=START + timedelta(minutes=30),
            intensity_gco2_kwh=value,
            source_record_id="bad",
        )


def test_legacy_wrapper_uses_strict_half_hour_duration() -> None:
    with pytest.raises(ValueError, match="half-hour intervals"):
        find_cleanest_window(points([100, 20]), duration=timedelta(minutes=45))


def test_legacy_wrapper_validates_duration_even_for_empty_input() -> None:
    with pytest.raises(ValueError, match="half-hour intervals"):
        find_cleanest_window([], duration=timedelta(minutes=45))


def test_comparison_exposes_efficiency_assumption() -> None:
    window = find_cleanest_window(points([40, 40]), duration=timedelta(hours=1))
    assert window is not None
    result = compare_charging(
        battery_energy_kwh=40,
        charging_efficiency=0.90,
        now_intensity_gco2_kwh=120,
        window=window,
    )
    assert result.grid_energy_kwh == 44.44
    assert result.now_emissions_kg == 5.33
    assert result.window_emissions_kg == 1.78
    assert result.avoided_emissions_kg == 3.56


def test_invalid_efficiency_is_rejected() -> None:
    window = find_cleanest_window(points([40, 40]), duration=timedelta(hours=1))
    assert window is not None
    with pytest.raises(ValueError):
        compare_charging(
            battery_energy_kwh=40,
            charging_efficiency=0,
            now_intensity_gco2_kwh=120,
            window=window,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("battery_energy_kwh", 0, "battery energy"),
        ("battery_energy_kwh", -1, "battery energy"),
        ("charging_efficiency", 1.01, "efficiency"),
        ("now_intensity_gco2_kwh", -1, "carbon intensity"),
    ],
)
def test_other_invalid_emissions_comparison_inputs_are_rejected(
    field: str,
    value: float,
    message: str,
) -> None:
    window = find_cleanest_window(points([40]), duration=timedelta(minutes=30))
    assert window is not None
    kwargs = {
        "battery_energy_kwh": 40,
        "charging_efficiency": 0.9,
        "now_intensity_gco2_kwh": 120,
        "window": window,
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        compare_charging(**kwargs)
