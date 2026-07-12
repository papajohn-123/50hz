from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.metrics import MetricClassification, MetricFamily


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


class DeliveryState(StrEnum):
    """Health of 50Hz receiving data from a source, based on retrieval age."""

    HEALTHY = "healthy"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class FactState(StrEnum):
    """Currency of the source fact itself, based on observation age."""

    LIVE = "live"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


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
    source_id: str = Field(alias="sourceID")


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
    source_ids: list[str] = Field(alias="sourceIDs")
    is_authoritatively_reported: bool


class DataFamilyStatus(MobileModel):
    """Independent source-delivery and fact-currency state for one metric family."""

    family: MetricFamily
    metric_ids: list[str] = Field(alias="metricIDs", min_length=1)
    source_ids: list[str] = Field(default_factory=list, alias="sourceIDs")
    source_record_ids: list[str] = Field(
        default_factory=list,
        alias="sourceRecordIDs",
    )
    required_for_snapshot: bool
    evaluated_at: AwareDatetime = Field(
        description="Request instant used to calculate ages and states"
    )
    delivery_state: DeliveryState
    fact_state: FactState
    observed_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = Field(
        default=None,
        description="Oldest source publication time, when the source supplies one",
    )
    retrieved_at: AwareDatetime | None = None
    valid_to: AwareDatetime | None = Field(
        default=None,
        description="End of the fact interval; absent for spot measurements",
    )
    observation_age_seconds: int | None = Field(default=None, ge=0)
    retrieval_age_seconds: int | None = Field(default=None, ge=0)
    expected_cadence_seconds: int = Field(gt=0)
    delivery_healthy_seconds: int = Field(gt=0)
    delivery_stale_seconds: int = Field(gt=0)
    fact_live_seconds: int = Field(gt=0)
    fact_stale_seconds: int = Field(gt=0)
    series_count: int = Field(ge=0)

    @model_validator(mode="after")
    def timing_matches_availability(self) -> Self:
        available = self.series_count > 0
        timings = (
            self.observed_at,
            self.published_at,
            self.retrieved_at,
            self.valid_to,
            self.observation_age_seconds,
            self.retrieval_age_seconds,
        )
        required_timings = (
            self.observed_at,
            self.retrieved_at,
            self.observation_age_seconds,
            self.retrieval_age_seconds,
        )
        if available and any(value is None for value in required_timings):
            raise ValueError("Available families require source timing and ages")
        if not available and any(value is not None for value in timings):
            raise ValueError("Unavailable families cannot expose source timing")
        if not available and (
            self.delivery_state is not DeliveryState.UNAVAILABLE
            or self.fact_state is not FactState.UNAVAILABLE
        ):
            raise ValueError("Missing families must be unavailable")
        if self.delivery_healthy_seconds >= self.delivery_stale_seconds:
            raise ValueError("Delivery thresholds must be ordered")
        if self.fact_live_seconds >= self.fact_stale_seconds:
            raise ValueError("Fact thresholds must be ordered")
        return self


class SupplyAccounting(MobileModel):
    """Partial, explicitly bounded accounting behind the legacy display mix."""

    methodology_version: str = "supply-accounting-v1"
    boundary: str
    is_complete: bool = Field(
        description="Whether this is a complete Great Britain supply balance"
    )
    generation_data_available: bool
    interconnector_data_available: bool
    domestic_generation_mw: float = Field(alias="domesticGenerationMW", ge=0)
    gross_imports_mw: float = Field(alias="grossImportsMW", ge=0)
    gross_exports_mw: float = Field(alias="grossExportsMW", ge=0)
    net_imports_mw: float = Field(alias="netImportsMW")
    storage_generation_mw: float = Field(alias="storageGenerationMW", ge=0)
    storage_charging_mw: float | None = Field(
        default=None,
        alias="storageChargingMW",
        ge=0,
        description=(
            "Charging demand when supported by a complete source; null for the "
            "current FUELINST-derived accounting"
        ),
    )
    legacy_displayed_generation_mw: float = Field(
        alias="legacyDisplayedGenerationMW",
        ge=0,
    )
    legacy_mix_basis: str
    note: str


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
    data_status: list[DataFamilyStatus] = Field(default_factory=list)
    supply: SupplyAccounting | None = None

    @model_validator(mode="after")
    def references_are_resolvable(self) -> Self:
        source_ids = {source.id for source in self.sources}
        facts = [self.demand, self.carbon_intensity, *([self.frequency] if self.frequency else [])]
        if any(fact.source_id not in source_ids for fact in facts):
            raise ValueError("Every top-level metric must reference a supplied source")
        status_source_ids = {
            source_id
            for status in self.data_status
            for source_id in status.source_ids
        }
        if not status_source_ids.issubset(source_ids):
            raise ValueError("Every data status source must reference a supplied source")
        families = [status.family for status in self.data_status]
        if len(families) != len(set(families)):
            raise ValueError("Data status families must be unique")
        return self


class GridTimelineSample(MobileModel):
    timestamp: AwareDatetime
    fact_class: FactClass
    demand_mw: float = Field(alias="demandMW")
    carbon_intensity: float
    frequency_hz: float | None = Field(default=None, alias="frequencyHz")
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
    regional_period_end: AwareDatetime
    regional_is_delayed: bool
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
    documentation_url: str | None = None
    licence_url: str | None = None
    attribution: str
    expected_cadence_seconds: int


class MetricDefinitionResponse(MobileModel):
    """Stable metric meaning, provenance boundary, and versioned methodology."""

    id: str
    methodology_version: str
    family: MetricFamily
    display_name: str
    description: str
    unit: str
    classification: MetricClassification
    boundary: str
    resolution_seconds: int = Field(
        gt=0,
        description=(
            "Fact interval, or sampling interval when the metric is a spot value"
        ),
    )
    expected_publication_lag_seconds: int = Field(ge=0)
    source_datasets: list[str] = Field(min_length=1)
    methodology: str
    exclusions: list[str] = Field(min_length=1)
    sign_convention: str | None = None


class MetricRegistryResponse(MobileModel):
    """Versioned registry for metrics exposed by the current grid API."""

    schema_version: str = "1.0"
    registry_version: str
    metrics: list[MetricDefinitionResponse] = Field(min_length=1)

    @model_validator(mode="after")
    def metric_ids_are_unique(self) -> Self:
        identifiers = [metric.id for metric in self.metrics]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Metric registry IDs must be unique")
        return self
