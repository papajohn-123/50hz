"""Source-neutral ingestion contracts.

These types deliberately live outside the database package.  A source adapter can
therefore be tested, replayed, or used by a one-off backfill without importing an
ORM model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Generic, Mapping, Protocol, TypeVar, runtime_checkable


RecordT = TypeVar("RecordT")


def as_utc(value: datetime, *, field_name: str = "datetime") -> datetime:
    """Return an aware UTC datetime and reject ambiguous naive values."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """Half-open source query window: ``start <= value < end`` internally.

    Upstream APIs may treat their upper query bound as inclusive.  Idempotent
    source keys and database upserts are therefore still required.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", as_utc(self.start, field_name="start"))
        object.__setattr__(self, "end", as_utc(self.end, field_name="end"))
        if self.start >= self.end:
            raise ValueError("observation window start must be before end")


class FlowDirection(StrEnum):
    IMPORT = "import"
    EXPORT = "export"
    IDLE = "idle"


class DataClassification(StrEnum):
    """Normalized source semantics assigned by adapters, never by the LLM."""

    OBSERVED = "observed"
    ESTIMATED = "estimated"
    FORECAST = "forecast"
    REPORTED = "reported"


@dataclass(frozen=True, slots=True)
class GenerationRecord:
    source_key: str
    observed_at: datetime
    published_at: datetime
    retrieved_at: datetime
    fuel_code: str
    fuel_type: str
    generation_mw: float
    settlement_date: date | None = None
    settlement_period: int | None = None
    classification: DataClassification = DataClassification.OBSERVED
    source: str = "elexon"
    dataset: str = "FUELINST"


@dataclass(frozen=True, slots=True)
class DemandRecord:
    source_key: str
    observed_at: datetime
    published_at: datetime
    retrieved_at: datetime
    demand_mw: float
    settlement_date: date | None = None
    settlement_period: int | None = None
    classification: DataClassification = DataClassification.OBSERVED
    source: str = "elexon"
    dataset: str = "INDO"


@dataclass(frozen=True, slots=True)
class FrequencyRecord:
    source_key: str
    observed_at: datetime
    retrieved_at: datetime
    frequency_hz: float
    published_at: datetime | None = None
    classification: DataClassification = DataClassification.OBSERVED
    source: str = "elexon"
    dataset: str = "FREQ"


@dataclass(frozen=True, slots=True)
class InterconnectorFlowRecord:
    """A signed interconnector flow using the 50Hz convention.

    Positive ``flow_mw`` means electricity enters Great Britain.  Negative
    ``flow_mw`` means Great Britain exports electricity.
    """

    source_key: str
    observed_at: datetime
    published_at: datetime
    retrieved_at: datetime
    interconnector_id: str
    interconnector_name: str
    flow_mw: float
    settlement_date: date | None = None
    settlement_period: int | None = None
    classification: DataClassification = DataClassification.OBSERVED
    source: str = "elexon"
    dataset: str = "FUELINST"

    @property
    def direction(self) -> FlowDirection:
        if self.flow_mw > 0:
            return FlowDirection.IMPORT
        if self.flow_mw < 0:
            return FlowDirection.EXPORT
        return FlowDirection.IDLE

    @property
    def import_mw(self) -> float:
        return max(self.flow_mw, 0.0)

    @property
    def export_mw(self) -> float:
        return max(-self.flow_mw, 0.0)


@dataclass(frozen=True, slots=True)
class DemandForecastRecord:
    source_key: str
    forecast_for: datetime
    published_at: datetime
    retrieved_at: datetime
    demand_mw: float
    boundary: str | None = None
    settlement_date: date | None = None
    settlement_period: int | None = None
    classification: DataClassification = DataClassification.FORECAST
    source: str = "elexon"
    dataset: str = "NDF"


@dataclass(frozen=True, slots=True)
class WindForecastRecord:
    source_key: str
    forecast_for: datetime
    published_at: datetime
    retrieved_at: datetime
    generation_mw: float
    classification: DataClassification = DataClassification.FORECAST
    source: str = "elexon"
    dataset: str = "WINDFOR"


@dataclass(frozen=True, slots=True)
class GenerationMixShare:
    fuel_type: str
    percent: float


@dataclass(frozen=True, slots=True)
class CarbonIntensityRecord:
    source_key: str
    period_start: datetime
    period_end: datetime
    retrieved_at: datetime
    intensity_g_co2_per_kwh: int
    classification: DataClassification
    index: str | None = None
    region_id: int | None = None
    region_name: str | None = None
    dno_region: str | None = None
    postcode: str | None = None
    generation_mix: tuple[GenerationMixShare, ...] = ()
    source: str = "neso_carbon_intensity"
    dataset: str = "carbon_intensity"


@dataclass(frozen=True, slots=True)
class OutageProfilePoint:
    start: datetime
    end: datetime
    available_capacity_mw: float


@dataclass(frozen=True, slots=True)
class RemitUnavailabilityRecord:
    """A market participant's reported unavailability, not an inferred outage."""

    source_key: str
    mrid: str
    revision_number: int
    message_id: int
    published_at: datetime
    created_at: datetime
    retrieved_at: datetime
    event_start: datetime
    event_end: datetime | None
    message_heading: str | None = None
    event_type: str | None = None
    unavailability_type: str | None = None
    event_status: str | None = None
    participant_id: str | None = None
    asset_id: str | None = None
    asset_type: str | None = None
    affected_unit: str | None = None
    affected_unit_eic: str | None = None
    affected_area: str | None = None
    bidding_zone: str | None = None
    fuel_type: str | None = None
    normal_capacity_mw: float | None = None
    available_capacity_mw: float | None = None
    unavailable_capacity_mw: float | None = None
    duration_uncertainty: str | None = None
    reported_cause: str | None = None
    reported_related_information: str | None = None
    outage_profile: tuple[OutageProfilePoint, ...] = ()
    classification: DataClassification = DataClassification.REPORTED
    source: str = "elexon"
    dataset: str = "REMIT"


@dataclass(frozen=True, slots=True)
class SystemWarningRecord:
    """An immutable reported SYSWARN publication.

    SYSWARN does not expose a revision number.  ``content_sha256`` and the
    publication timestamp form a revision-safe identity so corrected text is not
    silently overwritten.
    """

    source_key: str
    published_at: datetime
    retrieved_at: datetime
    warning_type: str
    warning_text: str
    content_sha256: str
    revision_number: int | None = None
    classification: DataClassification = DataClassification.REPORTED
    source: str = "elexon"
    dataset: str = "SYSWARN"


@dataclass(frozen=True, slots=True)
class DistributionIncidentRecord:
    """A privacy-reduced distribution-network incident publication.

    ``postcode_sectors`` and ``outward_codes`` are deliberately coarse. Source
    adapters must remove full-postcode fields before constructing this record or
    returning an :class:`AdapterResult` for raw-payload retention.
    """

    source_key: str
    incident_reference: str
    status: str
    observed_at: datetime
    retrieved_at: datetime
    content_sha256: str
    source_created_at: datetime | None = None
    incident_start: datetime | None = None
    restored_at: datetime | None = None
    estimated_restoration_at: datetime | None = None
    status_id: int | None = None
    customers_affected: int = 0
    calls_reported: int = 0
    postcode_sectors: tuple[str, ...] = ()
    outward_codes: tuple[str, ...] = ()
    latitude: float | None = None
    longitude: float | None = None
    geography_precision: str = "postcode_sector"
    operating_zone: str | None = None
    official_summary: str | None = None
    official_details: str | None = None
    restoration_window_text: str | None = None
    incident_category: str | None = None
    classification: DataClassification = DataClassification.REPORTED
    source: str = "ukpn"
    dataset: str = "LIVE_FAULTS"


@dataclass(frozen=True, slots=True)
class AdapterResult(Generic[RecordT]):
    """Normalized records plus enough raw provenance for an auditable write."""

    source_id: str
    dataset: str
    endpoint: str
    window: ObservationWindow
    retrieved_at: datetime
    request_url: str
    records: tuple[RecordT, ...]
    raw_payload: Any
    raw_body: bytes
    checksum_sha256: str
    content_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@runtime_checkable
class SourceAdapter(Protocol[RecordT]):
    source_id: str
    dataset: str
    endpoint: str

    async def fetch(self, window: ObservationWindow) -> AdapterResult[RecordT]:
        """Fetch and normalize every source record in ``window``."""
