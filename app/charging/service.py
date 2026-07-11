from datetime import datetime, timedelta

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class CarbonForecastPoint(BaseModel):
    start: AwareDatetime
    end: AwareDatetime
    intensity_gco2_kwh: float = Field(ge=0)
    source_record_id: str

    @model_validator(mode="after")
    def valid_interval(self) -> "CarbonForecastPoint":
        if self.end <= self.start:
            raise ValueError("forecast point end must follow its start")
        return self


class ChargingWindow(BaseModel):
    start: AwareDatetime
    end: AwareDatetime
    average_intensity_gco2_kwh: float = Field(ge=0)
    source_record_ids: list[str]


class ChargingComparison(BaseModel):
    requested_battery_energy_kwh: float = Field(gt=0)
    assumed_efficiency: float = Field(gt=0, le=1)
    grid_energy_kwh: float = Field(gt=0)
    now_emissions_kg: float = Field(ge=0)
    window_emissions_kg: float = Field(ge=0)
    avoided_emissions_kg: float


def _validate_points(points: list[CarbonForecastPoint]) -> list[CarbonForecastPoint]:
    ordered = sorted(points, key=lambda point: point.start)
    for previous, current in zip(ordered, ordered[1:]):
        if current.start < previous.end:
            raise ValueError("forecast points must not overlap")
    return ordered


def find_cleanest_window(
    points: list[CarbonForecastPoint],
    *,
    duration: timedelta,
) -> ChargingWindow | None:
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    ordered = _validate_points(points)
    best: tuple[float, datetime, datetime, list[str]] | None = None

    for start_index, first in enumerate(ordered):
        cursor = first.start
        target_end = first.start + duration
        weighted_total = 0.0
        covered_seconds = 0.0
        source_ids: list[str] = []

        for point in ordered[start_index:]:
            if point.start != cursor:
                break
            segment_end = min(point.end, target_end)
            seconds = (segment_end - point.start).total_seconds()
            if seconds <= 0:
                break
            weighted_total += point.intensity_gco2_kwh * seconds
            covered_seconds += seconds
            source_ids.append(point.source_record_id)
            cursor = segment_end
            if cursor >= target_end:
                average = weighted_total / covered_seconds
                candidate = (average, first.start, target_end, source_ids)
                if best is None or candidate[0] < best[0] or (
                    candidate[0] == best[0] and candidate[1] < best[1]
                ):
                    best = candidate
                break
            if segment_end != point.end:
                break

    if best is None:
        return None
    average, start, end, source_ids = best
    return ChargingWindow(
        start=start,
        end=end,
        average_intensity_gco2_kwh=round(average, 2),
        source_record_ids=source_ids,
    )


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

