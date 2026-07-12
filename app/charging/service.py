from __future__ import annotations

from datetime import UTC, timedelta

from app.charging.models import (
    CarbonForecastPoint,
    CarbonForecastSeries,
    ChargingComparison,
    ChargingWindow,
)
from app.charging.planner import plan_flexible_use


def find_cleanest_window(
    points: list[CarbonForecastPoint],
    *,
    duration: timedelta,
) -> ChargingWindow | None:
    """Compatibility wrapper around the versioned Local planning engine."""

    if not isinstance(duration, timedelta):
        raise TypeError("duration must be a timedelta")
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    duration_seconds = duration.total_seconds()
    if not duration_seconds.is_integer() or int(duration_seconds) % (30 * 60):
        raise ValueError("duration must be a whole number of half-hour intervals")
    if not points:
        return None
    earliest = min(point.start for point in points).astimezone(UTC)
    latest = max(point.end for point in points).astimezone(UTC)
    forecast = CarbonForecastSeries(
        series_id="legacy-clean-window",
        geography="unspecified",
        source_id="unspecified",
        methodology_version="unspecified",
        points=points,
    )
    result = plan_flexible_use(
        forecast,
        duration=duration,
        earliest_start=earliest,
        latest_finish=latest,
        start_now=earliest,
    )
    return result.recommended_window


def compare_charging(
    *,
    battery_energy_kwh: float,
    charging_efficiency: float,
    now_intensity_gco2_kwh: float,
    window: ChargingWindow,
) -> ChargingComparison:
    if battery_energy_kwh <= 0:
        raise ValueError("battery energy must be positive")
    if not 0 < charging_efficiency <= 1:
        raise ValueError("charging efficiency must be greater than zero and at most one")
    if now_intensity_gco2_kwh < 0:
        raise ValueError("carbon intensity cannot be negative")

    grid_energy = battery_energy_kwh / charging_efficiency
    now_kg = grid_energy * now_intensity_gco2_kwh / 1_000
    window_kg = grid_energy * window.average_intensity_gco2_kwh / 1_000
    return ChargingComparison(
        requested_battery_energy_kwh=battery_energy_kwh,
        assumed_efficiency=charging_efficiency,
        grid_energy_kwh=round(grid_energy, 2),
        now_emissions_kg=round(now_kg, 2),
        window_emissions_kg=round(window_kg, 2),
        avoided_emissions_kg=round(now_kg - window_kg, 2),
    )
