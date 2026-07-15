"""Truth-preserving mobile contracts for located sites and Elexon evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class AssetAPIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class AssetLifecycle(StrEnum):
    OPERATIONAL = "operational"
    UNDER_CONSTRUCTION = "under_construction"
    PLANNED = "planned"
    DECOMMISSIONED = "decommissioned"
    UNKNOWN = "unknown"


class AssetEvidenceKind(StrEnum):
    REFERENCE = "reference"
    REPORTED_PLAN = "reported_plan"
    SETTLED_METERED = "settled_metered"


class AssetFeedState(StrEnum):
    CURRENT = "current"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class AssetSourceResponse(AssetAPIModel):
    source_id: str = Field(alias="sourceID", min_length=1)
    publisher: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    source_record_id: str = Field(alias="sourceRecordID", min_length=1)
    retrieved_at: AwareDatetime
    canonical_url: str = Field(alias="canonicalURL", min_length=1)
    licence: str = Field(min_length=1)
    attribution: str = Field(min_length=1)


class AssetCoordinateResponse(AssetAPIModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    precision: str = Field(min_length=1)
    source: AssetSourceResponse


class AssetPlanEvidenceResponse(AssetAPIModel):
    level_mw: float = Field(alias="levelMW")
    at: AwareDatetime
    direction: Literal["export", "import", "idle"]
    evidence_kind: Literal[AssetEvidenceKind.REPORTED_PLAN] = (
        AssetEvidenceKind.REPORTED_PLAN
    )
    source_id: str = Field(alias="sourceID", min_length=1)
    retrieved_at: AwareDatetime
    settlement_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    settlement_period: int = Field(ge=1, le=50)
    caveat: str = Field(min_length=1)


class AssetSettledEvidenceResponse(AssetAPIModel):
    energy_mwh: float = Field(alias="energyMWh")
    average_mw: float = Field(alias="averageMW")
    interval_start: AwareDatetime
    interval_end: AwareDatetime
    direction: Literal["export", "import", "idle"]
    evidence_kind: Literal[AssetEvidenceKind.SETTLED_METERED] = (
        AssetEvidenceKind.SETTLED_METERED
    )
    source_id: str = Field(alias="sourceID", min_length=1)
    retrieved_at: AwareDatetime
    settlement_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    settlement_period: int = Field(ge=1, le=50)
    caveat: str = Field(min_length=1)

    @model_validator(mode="after")
    def interval_is_ordered(self) -> AssetSettledEvidenceResponse:
        if self.interval_end <= self.interval_start:
            raise ValueError("settled interval end must follow its start")
        return self


class AssetOperatingEvidenceResponse(AssetAPIModel):
    participant_submitted_plan: AssetPlanEvidenceResponse | None = None
    latest_settled_metered: AssetSettledEvidenceResponse | None = None
    has_live_metered_output: Literal[False] = False


class AssetMapItemResponse(AssetAPIModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    operator_name: str | None = None
    technology: str = Field(min_length=1)
    fuel_type: str = Field(min_length=1)
    lifecycle: AssetLifecycle
    capacity_mw: float | None = Field(default=None, alias="capacityMW", ge=0)
    region: str | None = None
    country: str | None = None
    coordinate: AssetCoordinateResponse
    linked_bm_unit_count: int = Field(alias="linkedBMUnitCount", ge=0)
    operating_evidence: AssetOperatingEvidenceResponse | None = None


class AssetFeedStatusResponse(AssetAPIModel):
    state: AssetFeedState
    last_successful_at: AwareDatetime | None = None
    asset_reference_count: int = Field(ge=0)
    located_asset_count: int = Field(ge=0)


class AssetMapResponse(AssetAPIModel):
    schema_version: str = "1.0"
    evaluated_at: AwareDatetime
    source_status: AssetFeedStatusResponse
    total_count: int = Field(ge=0)
    returned_count: int = Field(ge=0)
    is_truncated: bool
    assets: list[AssetMapItemResponse]
    boundary: str = Field(min_length=1)
    disclaimer: str = Field(min_length=1)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> AssetMapResponse:
        if self.returned_count != len(self.assets):
            raise ValueError("returned_count must equal the number of assets")
        if self.returned_count > self.total_count:
            raise ValueError("returned_count cannot exceed total_count")
        if self.is_truncated != (self.returned_count < self.total_count):
            raise ValueError("is_truncated must reflect the response counts")
        return self


class BMUnitSummaryResponse(AssetAPIModel):
    national_grid_bm_unit: str = Field(alias="nationalGridBMUnit", min_length=1)
    elexon_bm_unit: str | None = Field(default=None, alias="elexonBMUnit")
    name: str | None = None
    fuel_type: str | None = None
    lead_party_name: str | None = None
    generation_capacity_mw: float | None = Field(
        default=None,
        alias="generationCapacityMW",
    )
    demand_capacity_mw: float | None = Field(
        default=None,
        alias="demandCapacityMW",
    )
    gsp_group_name: str | None = None
    eic: str | None = None
    match_method: str = Field(min_length=1)
    match_confidence: float = Field(ge=0, le=1)


class AssetDetailResponse(AssetAPIModel):
    schema_version: str = "1.0"
    evaluated_at: AwareDatetime
    asset: AssetMapItemResponse
    bm_units: list[BMUnitSummaryResponse]
    plan: list[AssetPlanEvidenceResponse]
    settled_metered: list[AssetSettledEvidenceResponse]
    limitations: list[str]
