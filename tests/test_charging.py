from datetime import UTC, datetime, timedelta

import pytest

from app.charging.service import (
    CarbonForecastPoint,
    compare_charging,
    find_cleanest_window,
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


def test_cleanest_window_uses_contiguous_weighted_periods() -> None:
    window = find_cleanest_window(points([120, 80, 40, 30, 70]), duration=timedelta(hours=1))
    assert window is not None
    assert window.start == START + timedelta(hours=1)
    assert window.average_intensity_gco2_kwh == 35
    assert window.source_record_ids == ["carbon:2", "carbon:3"]


def test_gap_does_not_produce_false_window() -> None:
    forecast = [points([20, 30, 100])[0], points([20, 30, 100])[2]]
    assert find_cleanest_window(forecast, duration=timedelta(hours=1)) is None


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
