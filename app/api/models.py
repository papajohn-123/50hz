from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.events.models import EventStatus
from app.events.revisions import EventAuthority
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


class EventHistoryChangedField(StrEnum):
    UNAVAILABLE_MW = "unavailableMW"
    NORMAL_CAPACITY_MW = "normalCapacityMW"
    EFFECTIVE_START = "effectiveStart"
    EFFECTIVE_END = "effectiveEnd"
    STATUS = "status"
    REPORTED_CAUSE = "reportedCause"
    EVIDENCE_CHECKSUM = "evidenceChecksum"
    MATERIAL_REASON = "materialReason"


class EventHistoryFieldChange(MobileModel):
    field: EventHistoryChangedField
    before: float | AwareDatetime | str | None
    after: float | AwareDatetime | str | None


class EventHistoryEffectiveWindow(MobileModel):
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.start is None and self.end is None:
            raise ValueError("effective window requires a start or end")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("effective window end cannot precede start")
        return self


class EventHistoryReportedAsset(MobileModel):
    asset_id: str | None = Field(default=None, alias="assetID", max_length=200)
    name: str | None = Field(default=None, max_length=300)
    identity_reliable: bool

    @model_validator(mode="after")
    def reliable_identity_has_id(self) -> Self:
        if self.identity_reliable and self.asset_id is None:
            raise ValueError("reliable asset identity requires assetID")
        if self.asset_id is None and self.name is None:
            raise ValueError("reported asset requires an ID or name")
        return self


class EventHistoryReportedCapacity(MobileModel):
    unavailable_mw: float | None = Field(default=None, alias="unavailableMW", ge=0)
    normal_capacity_mw: float | None = Field(
        default=None,
        alias="normalCapacityMW",
        gt=0,
    )

    @model_validator(mode="after")
    def capacity_has_a_reported_value(self) -> Self:
        if self.unavailable_mw is None and self.normal_capacity_mw is None:
            raise ValueError("reported capacity requires at least one value")
        return self


class EventHistoryRevision(MobileModel):
    revision_number: int = Field(ge=1)
    status: EventStatus
    authority: EventAuthority
    evidence_class: Literal["reported"] = "reported"
    published_at: AwareDatetime
    effective_window: EventHistoryEffectiveWindow | None = None
    reported_asset: EventHistoryReportedAsset | None = None
    reported_capacity: EventHistoryReportedCapacity | None = None
    planned: bool | None = None
    reported_cause: str | None = Field(default=None, max_length=1_000)
    material_reason: str | None = Field(default=None, max_length=500)
    superseded_by_event_id: str | None = Field(
        default=None,
        alias="supersededByEventID",
        pattern=r"^evt_[0-9a-f]{20}$",
    )
    source_ids: list[str] = Field(alias="sourceIDs", min_length=1, max_length=16)
    source_record_ids: list[str] = Field(
        alias="sourceRecordIDs",
        min_length=1,
        max_length=100,
    )
    evidence_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    changes: list[EventHistoryFieldChange] = Field(default_factory=list, max_length=8)


class EventHistoryResponse(MobileModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: str = Field(alias="eventID", pattern=r"^evt_[0-9a-f]{20}$")
    lifecycle_status: EventStatus
    revision_order: Literal["newestFirst"] = "newestFirst"
    revision_count: int = Field(ge=1)
    returned_revision_count: int = Field(ge=1, le=100)
    is_truncated: bool
    first_published_at: AwareDatetime
    latest_published_at: AwareDatetime
    revisions: list[EventHistoryRevision] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_history_summary(self) -> Self:
        if self.returned_revision_count != len(self.revisions):
            raise ValueError("returned revision count must match revisions")
        if self.revision_count < self.returned_revision_count:
            raise ValueError("revision count cannot be below the returned slice")
        if self.is_truncated != (self.revision_count > self.returned_revision_count):
            raise ValueError("truncation must match revision counts")
        if self.lifecycle_status is not self.revisions[0].status:
            raise ValueError("lifecycle status must match the newest revision")
        if self.latest_published_at < self.first_published_at:
            raise ValueError("latest publication cannot precede first publication")
        numbers = [revision.revision_number for revision in self.revisions]
        if numbers != sorted(numbers, reverse=True) or len(numbers) != len(set(numbers)):
            raise ValueError("revisions must be unique and newest first")
        return self


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
    forecast_issued_at: AwareDatetime = Field(
        description=(
            "Compatibility field containing the selected forecast capture time; "
            "the source does not publish an issue timestamp"
        )
    )
    regional_fact_class: Literal["forecast"] = "forecast"
    regional_geography_scope: Literal["regional"] = "regional"
    national_fact_class: Literal["forecast"] = "forecast"
    national_geography_scope: Literal["national"] = "national"
    forecast_captured_at: AwareDatetime
    forecast_issue_time_basis: Literal[
        "source_does_not_publish_issue_time"
    ] = "source_does_not_publish_issue_time"
    source: SourceReference


class LocalFlexibleUseStatus(StrEnum):
    LOWER_CARBON_WINDOW = "lower_carbon_window"
    NO_MEANINGFUL_DIFFERENCE = "no_meaningful_difference"
    WINDOW_FOUND = "window_found"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


class LocalComparisonStatus(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE_SERIES = "incompatible_series"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


class LocalChargingWindow(MobileModel):
    start: AwareDatetime
    end: AwareDatetime
    average_intensity_gco2_kwh: float = Field(
        alias="averageIntensityGCO2KWh",
        ge=0,
    )
    source_record_ids: list[str] = Field(alias="sourceRecordIDs")
    coverage_fraction: float = Field(ge=0, le=1)


class LocalForecastCoverage(MobileModel):
    interval_minutes: int = Field(gt=0)
    required_interval_count: int = Field(ge=1)
    expected_interval_count: int = Field(ge=0)
    available_interval_count: int = Field(ge=0)
    coverage_fraction: float = Field(ge=0, le=1)
    gap_starts: list[AwareDatetime]
    candidate_start_count: int = Field(ge=0)
    complete_candidate_count: int = Field(ge=0)


class LocalFlexibleUseComparison(MobileModel):
    status: LocalComparisonStatus
    start_now_window: LocalChargingWindow | None = None
    incompatibility_fields: list[str] = Field(default_factory=list)
    start_now_minus_recommended_gco2_kwh: float | None = Field(
        default=None,
        alias="startNowMinusRecommendedGCO2KWh",
    )
    percent_lower_than_start_now: float | None = None
    is_meaningful: bool | None = None

    @model_validator(mode="after")
    def deltas_require_a_compatible_comparison(self) -> Self:
        deltas = (
            self.start_now_minus_recommended_gco2_kwh,
            self.percent_lower_than_start_now,
            self.is_meaningful,
        )
        if self.status is LocalComparisonStatus.COMPATIBLE:
            if self.start_now_window is None or self.is_meaningful is None:
                raise ValueError("Compatible comparisons require a window and result")
        elif any(value is not None for value in deltas):
            raise ValueError("Unavailable comparisons cannot expose deltas")
        return self


class LocalFlexibleUseMethodology(MobileModel):
    version: str
    interval_minutes: int = Field(gt=0)
    required_window_coverage_percent: int = Field(ge=0, le=100)
    selection_rule: str
    tie_break_rule: str
    meaningful_absolute_delta_gco2_kwh: float = Field(
        alias="meaningfulAbsoluteDeltaGCO2KWh",
        ge=0,
    )
    meaningful_percent_delta: float = Field(ge=0)


class LocalFlexibleUsePlan(MobileModel):
    result_version: str
    methodology: LocalFlexibleUseMethodology
    status: LocalFlexibleUseStatus
    summary: str
    continuous: Literal[True]
    requested_duration_minutes: int = Field(gt=0)
    earliest_start: AwareDatetime
    latest_finish: AwareDatetime
    recommended_window: LocalChargingWindow | None = None
    coverage: LocalForecastCoverage
    comparison: LocalFlexibleUseComparison


class LocalForecastMetadata(MobileModel):
    geography_code: Literal["GB"] = "GB"
    geography_scope: Literal["national"] = "national"
    fact_class: Literal["forecast"] = "forecast"
    series_id: str = Field(alias="seriesID")
    source_id: str = Field(alias="sourceID")
    methodology_version: str
    source_issued_at: AwareDatetime | None = None
    captured_at: AwareDatetime
    vintage_at: AwareDatetime
    vintage_basis: Literal["captured_at", "source_issued_at"]
    issue_time_basis: str
    capture_time_basis: Literal["retrieved_at"] = "retrieved_at"
    capture_age_seconds: int = Field(ge=0)
    capture_stale_after_seconds: int = Field(gt=0)
    capture_state: Literal["live"] = "live"
    source_record_ids: list[str] = Field(alias="sourceRecordIDs")


class LocalSearchBounds(MobileModel):
    earliest_start: AwareDatetime
    latest_finish: AwareDatetime
    earliest_was_defaulted: bool
    latest_was_defaulted: bool
    default_rule: str


class LocalWindowsResponse(MobileModel):
    """Privacy-safe flexible-use plan backed only by a GB national forecast."""

    schema_version: str = "1.0"
    postcode: str = Field(
        description="Normalized UK outward postcode only; full postcodes are never returned"
    )
    evaluated_at: AwareDatetime
    bounds: LocalSearchBounds
    forecast: LocalForecastMetadata
    plan: LocalFlexibleUsePlan
    limitations: list[str]


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
