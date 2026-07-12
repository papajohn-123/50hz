from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator


METHODOLOGY_VERSION = "50hz.local.flexible-use.v1"
RESULT_VERSION = "1"
INTERVAL_MINUTES = 30
MEANINGFUL_ABSOLUTE_DELTA_GCO2_KWH = 5.0
MEANINGFUL_PERCENT_DELTA = 5.0


class CarbonForecastPoint(BaseModel):
    start: AwareDatetime
    end: AwareDatetime
    intensity_gco2_kwh: float = Field(ge=0, allow_inf_nan=False)
    source_record_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_interval(self) -> "CarbonForecastPoint":
        if self.end <= self.start:
            raise ValueError("forecast point end must follow its start")
        return self


class CarbonForecastSeries(BaseModel):
    """One forecast vintage with an explicit compatibility identity.

    A comparison is like-for-like only when all identity fields match.  In
    particular, regional and national forecasts, or forecasts captured from
    different vintages, are never silently combined.
    """

    series_id: str = Field(min_length=1)
    geography: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    methodology_version: str = Field(min_length=1)
    vintage_at: AwareDatetime | None = None
    fact_class: Literal["forecast"] = "forecast"
    points: list[CarbonForecastPoint] = Field(default_factory=list)

    @property
    def compatibility_key(
        self,
    ) -> tuple[str, str, str, str, str, datetime | None]:
        return (
            self.series_id,
            self.geography,
            self.source_id,
            self.methodology_version,
            self.fact_class,
            self.vintage_at,
        )


class ChargingWindow(BaseModel):
    start: AwareDatetime
    end: AwareDatetime
    average_intensity_gco2_kwh: float = Field(ge=0, allow_inf_nan=False)
    source_record_ids: list[str]
    coverage_fraction: float = Field(default=1.0, ge=0, le=1)


class ChargingComparison(BaseModel):
    requested_battery_energy_kwh: float = Field(gt=0)
    assumed_efficiency: float = Field(gt=0, le=1)
    grid_energy_kwh: float = Field(gt=0)
    now_emissions_kg: float = Field(ge=0)
    window_emissions_kg: float = Field(ge=0)
    avoided_emissions_kg: float


class FlexibleUseStatus(StrEnum):
    LOWER_CARBON_WINDOW = "lower_carbon_window"
    NO_MEANINGFUL_DIFFERENCE = "no_meaningful_difference"
    WINDOW_FOUND = "window_found"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


class StartNowComparisonStatus(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE_SERIES = "incompatible_series"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


class ForecastCoverage(BaseModel):
    interval_minutes: int = INTERVAL_MINUTES
    required_interval_count: int = Field(ge=1)
    expected_interval_count: int = Field(ge=0)
    available_interval_count: int = Field(ge=0)
    coverage_fraction: float = Field(ge=0, le=1)
    gap_starts: list[AwareDatetime] = Field(default_factory=list)
    candidate_start_count: int = Field(ge=0)
    complete_candidate_count: int = Field(ge=0)


class FlexibleUseComparison(BaseModel):
    status: StartNowComparisonStatus
    start_now_window: ChargingWindow | None = None
    incompatibility_fields: list[str] = Field(default_factory=list)
    start_now_minus_recommended_gco2_kwh: float | None = None
    percent_lower_than_start_now: float | None = None
    is_meaningful: bool | None = None

    @model_validator(mode="after")
    def compatible_values_are_consistent(self) -> "FlexibleUseComparison":
        if self.status == StartNowComparisonStatus.COMPATIBLE:
            if self.start_now_window is None:
                raise ValueError("a compatible comparison requires a start-now window")
            if self.start_now_minus_recommended_gco2_kwh is None:
                raise ValueError("a compatible comparison requires an intensity delta")
            if self.is_meaningful is None:
                raise ValueError(
                    "a compatible comparison requires a meaningfulness result"
                )
        elif any(
            value is not None
            for value in (
                self.start_now_minus_recommended_gco2_kwh,
                self.percent_lower_than_start_now,
                self.is_meaningful,
            )
        ):
            raise ValueError("incompatible or incomplete comparisons cannot expose deltas")
        return self


class FlexibleUseMethodology(BaseModel):
    version: Literal["50hz.local.flexible-use.v1"] = METHODOLOGY_VERSION
    interval_minutes: Literal[30] = INTERVAL_MINUTES
    required_window_coverage_percent: Literal[100] = 100
    selection_rule: str = (
        "Lowest unrounded time-weighted average forecast carbon intensity among "
        "complete continuous windows within the requested bounds."
    )
    tie_break_rule: str = "Earliest start wins when averages are equal."
    meaningful_absolute_delta_gco2_kwh: float = (
        MEANINGFUL_ABSOLUTE_DELTA_GCO2_KWH
    )
    meaningful_percent_delta: float = MEANINGFUL_PERCENT_DELTA


class FlexibleUsePlan(BaseModel):
    result_version: Literal["1"] = RESULT_VERSION
    methodology: FlexibleUseMethodology = Field(
        default_factory=FlexibleUseMethodology
    )
    status: FlexibleUseStatus
    summary: str
    continuous: Literal[True] = True
    requested_duration_minutes: int = Field(gt=0)
    earliest_start: AwareDatetime
    latest_finish: AwareDatetime
    recommended_window: ChargingWindow | None = None
    coverage: ForecastCoverage
    comparison: FlexibleUseComparison

    @model_validator(mode="after")
    def status_matches_payload(self) -> "FlexibleUsePlan":
        if self.status == FlexibleUseStatus.INSUFFICIENT_COVERAGE:
            if self.recommended_window is not None:
                raise ValueError("an unavailable plan cannot contain a recommendation")
            return self
        if self.recommended_window is None:
            raise ValueError("an available plan requires a recommendation")
        if self.status == FlexibleUseStatus.LOWER_CARBON_WINDOW:
            if self.comparison.is_meaningful is not True:
                raise ValueError("lower-carbon status requires a meaningful comparison")
        if self.status == FlexibleUseStatus.NO_MEANINGFUL_DIFFERENCE:
            if self.comparison.is_meaningful is not False:
                raise ValueError(
                    "no-meaningful-difference status requires a compatible comparison"
                )
        return self
