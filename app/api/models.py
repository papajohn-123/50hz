from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(word.capitalize() for word in tail)


class MobileModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class FactClass(StrEnum):
    OBSERVED = "observed"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    FORECAST = "forecast"


class MobileFreshness(StrEnum):
    LIVE = "live"
    STALE = "stale"
    OFFLINE = "offline"
    CRITICAL = "critical"


class SourceReference(MobileModel):
    id: str
    name: str
    dataset: str
    observed_at: AwareDatetime
    retrieved_at: AwareDatetime
    cadence_seconds: int = Field(gt=0)


class GridMetric(MobileModel):
    value: float
    unit: str
    fact_class: FactClass
    source_id: str


class FuelReading(MobileModel):
    fuel: str
    megawatts: float
    share: float = Field(ge=0, le=1, description="Fraction of displayed generation, from 0 to 1")
    change_one_hour: float = 0
    rank: int = Field(ge=1)
    fact_class: FactClass


class InterconnectorFlow(MobileModel):
    id: str
    name: str
    country_code: str
    megawatts: float = Field(description="Positive imports into Britain; negative exports")
    fact_class: FactClass


class ConditionHeadline(MobileModel):
    cleanliness: str
    balance: str
    energy_position: str
    interpretation: str


class GridEvent(MobileModel):
    id: str
    title: str
    summary: str
    severity: str
    evidence_class: str
    started_at: AwareDatetime
    source_ids: list[str]
    is_authoritatively_reported: bool


class GridSnapshotResponse(MobileModel):
    schema_version: str = "1.0"
    timestamp: AwareDatetime
    retrieved_at: AwareDatetime
    freshness: MobileFreshness
    freshness_age_seconds: int = Field(ge=0)
    headline: ConditionHeadline
    frequency: GridMetric | None
    demand: GridMetric
    carbon_intensity: GridMetric
    generation: list[FuelReading]
    interconnectors: list[InterconnectorFlow]
    active_event: GridEvent | None = None
    sources: list[SourceReference]

    @model_validator(mode="after")
    def references_are_resolvable(self) -> Self:
        source_ids = {source.id for source in self.sources}
        facts = [self.demand, self.carbon_intensity, *([self.frequency] if self.frequency else [])]
        if any(fact.source_id not in source_ids for fact in facts):
            raise ValueError("Every top-level metric must reference a supplied source")
        return self


class GridTimelineSample(MobileModel):
    timestamp: AwareDatetime
    fact_class: FactClass
    demand_mw: float
    carbon_intensity: float
    frequency_hz: float | None = None
    generation: list[FuelReading]

    @model_validator(mode="after")
    def forecasts_do_not_contain_frequency(self) -> Self:
        if self.fact_class is FactClass.FORECAST and self.frequency_hz is not None:
            raise ValueError("Frequency is not forecast by 50Hz")
        return self


class GridTimelineResponse(MobileModel):
    schema_version: str = "1.0"
    source_resolution_seconds: int = Field(gt=0)
    material_gap_seconds: int = Field(gt=0)
    now_boundary: AwareDatetime
    samples: list[GridTimelineSample]


class RegionResponse(MobileModel):
    name: str
    postcode: str
    carbon_intensity: float = Field(ge=0)
    national_carbon_intensity: float = Field(ge=0)
    rating: str
    cleanest_window_start: AwareDatetime
    cleanest_window_end: AwareDatetime
    charging_window_start: AwareDatetime
    charging_window_end: AwareDatetime
    forecast_issued_at: AwareDatetime
    source: SourceReference


class SourceMetadataResponse(MobileModel):
    id: str
    publisher: str
    dataset: str
    documentation_url: str
    licence_url: str | None = None
    attribution: str
    expected_cadence_seconds: int
