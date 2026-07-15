from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.api.models import MobileModel
from app.sources.ukpn import normalize_outward_code


class OutageDeliveryState(StrEnum):
    HEALTHY = "healthy"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class OutageSourceStatus(MobileModel):
    source_id: Literal["ukpn.live-faults"] = Field(
        default="ukpn.live-faults",
        alias="sourceID",
    )
    provider: Literal["UK Power Networks"] = "UK Power Networks"
    dataset: Literal["Live Faults"] = "Live Faults"
    delivery_state: OutageDeliveryState
    evaluated_at: AwareDatetime
    last_successful_at: AwareDatetime | None = None
    delivery_age_seconds: int | None = Field(default=None, ge=0)
    expected_cadence_seconds: Literal[300] = 300
    stale_after_seconds: Literal[1800] = 1800
    records_in_latest_snapshot: int = Field(ge=0, le=500)
    empty_snapshot: bool
    data_may_be_partial: Literal[True] = True

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        available = self.last_successful_at is not None
        if available != (self.delivery_age_seconds is not None):
            raise ValueError("successful time and delivery age must appear together")
        if not available and self.delivery_state is not OutageDeliveryState.UNAVAILABLE:
            raise ValueError("missing source delivery must be unavailable")
        if self.empty_snapshot != (
            available and self.records_in_latest_snapshot == 0
        ):
            raise ValueError("empty snapshot requires a successful empty delivery")
        return self


class OutageLocation(MobileModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    precision: Literal["aggregated_incident_point"]


class DistributionIncidentResponse(MobileModel):
    id: str = Field(pattern=r"^dno_[0-9a-f]{20}$")
    incident_reference: str = Field(max_length=120)
    revision: int = Field(ge=0)
    status: Literal["planned", "unplanned", "restored"]
    lifecycle_status: Literal["active", "restored"]
    customers_affected: int = Field(
        ge=0,
        description="Current source-reported aggregate, never a property assertion",
    )
    calls_reported: int = Field(ge=0)
    postcode_sectors: list[str] = Field(max_length=100)
    geography_precision: Literal[
        "aggregated_incident_point", "postcode_sector", "operating_zone"
    ]
    operating_zone: str | None = Field(default=None, max_length=160)
    location: OutageLocation | None = None
    official_message: str | None = Field(default=None, max_length=20_000)
    official_details: str | None = Field(default=None, max_length=20_000)
    restoration_window_text: str | None = Field(default=None, max_length=500)
    incident_category: str | None = Field(default=None, max_length=64)
    source_created_at: AwareDatetime | None = None
    observed_at: AwareDatetime
    first_retrieved_at: AwareDatetime
    last_seen_at: AwareDatetime
    incident_start: AwareDatetime | None = None
    restored_at: AwareDatetime | None = None
    estimated_restoration_at: AwareDatetime | None = None
    evidence_class: Literal["reported"] = "reported"
    customer_impact_precision: Literal["incident_aggregate"] = "incident_aggregate"

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        expected_lifecycle = "restored" if self.status == "restored" else "active"
        if self.lifecycle_status != expected_lifecycle:
            raise ValueError("lifecycle status must match incident status")
        if (self.location is not None) != (
            self.geography_precision == "aggregated_incident_point"
        ):
            raise ValueError("only aggregated geopoints may expose a location")
        return self


class CurrentOutagesResponse(MobileModel):
    schema_version: Literal["1.0"] = "1.0"
    evaluated_at: AwareDatetime
    source_status: OutageSourceStatus
    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0, le=100)
    is_truncated: bool
    incidents: list[DistributionIncidentResponse] = Field(max_length=100)
    geography_boundary: Literal["UK Power Networks licence areas"] = (
        "UK Power Networks licence areas"
    )
    disclaimer: str
    attribution: Literal["Live Faults data supplied by UK Power Networks."] = (
        "Live Faults data supplied by UK Power Networks."
    )
    licence: Literal["CC BY 4.0"] = "CC BY 4.0"

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.returned_count != len(self.incidents):
            raise ValueError("returned count must match incidents")
        if self.total_count < self.returned_count:
            raise ValueError("total count cannot be below returned count")
        if self.is_truncated != (self.total_count > self.returned_count):
            raise ValueError("truncation must match counts")
        return self


class OutageCheckRequest(MobileModel):
    outward_code: str = Field(min_length=2, max_length=4)
    include_restored: bool = False
    limit: int = Field(default=25, ge=1, le=50)

    @field_validator("outward_code", mode="before")
    @classmethod
    def normalize_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("outwardCode must be text")
        return normalize_outward_code(value)


class OutageCheckResponse(MobileModel):
    schema_version: Literal["1.0"] = "1.0"
    evaluated_at: AwareDatetime
    outward_code: str = Field(min_length=2, max_length=4)
    match_precision: Literal["postcode_district"] = "postcode_district"
    household_impact: Literal["unknown"] = "unknown"
    district_has_reported_incidents: bool
    match_statement: str
    source_status: OutageSourceStatus
    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0, le=50)
    is_truncated: bool
    incidents: list[DistributionIncidentResponse] = Field(max_length=50)
    disclaimer: str

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.returned_count != len(self.incidents):
            raise ValueError("returned count must match incidents")
        if self.total_count < self.returned_count:
            raise ValueError("total count cannot be below returned count")
        if self.is_truncated != (self.total_count > self.returned_count):
            raise ValueError("truncation must match counts")
        if self.district_has_reported_incidents != (self.total_count > 0):
            raise ValueError("district match state must match the count")
        return self
