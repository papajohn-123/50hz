from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class MissionKind(StrEnum):
    FIND_CLEAN_WINDOW = "find_clean_window"
    IDENTIFY_LARGEST_SOURCE = "identify_largest_source"
    INSPECT_INTERCONNECTOR = "inspect_interconnector"
    OPEN_EVENT_EVIDENCE = "open_event_evidence"


class MissionDefinition(BaseModel):
    mission_id: str
    kind: MissionKind
    title: str
    available: bool
    unavailable_reason: str | None = None
    completion_payload: dict[str, str | float | int] = Field(default_factory=dict)


class PredictionChoice(StrEnum):
    IMPORTING = "importing"
    EXPORTING = "exporting"


class PredictionDefinition(BaseModel):
    prediction_id: str
    question: str
    choices: list[PredictionChoice]
    locks_at: datetime
    metric: str
    resolves_from: datetime
    resolves_to: datetime
    rule_version: int = 1


class DailyGame(BaseModel):
    date: str
    missions: list[MissionDefinition]
    prediction: PredictionDefinition | None
    source_fresh: bool


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ResolutionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class PredictionResolutionState(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    VOID = "void"


class PredictionOutcome(StrEnum):
    IMPORTING = "importing"
    EXPORTING = "exporting"


class PredictionEvidenceCoverage(ResolutionModel):
    expected_connector_count: int = Field(ge=0)
    observed_connector_count: int = Field(ge=0)
    coverage_fraction: float = Field(ge=0, le=1)
    complete: bool

    @model_validator(mode="after")
    def counts_are_consistent(self) -> Self:
        if self.observed_connector_count > self.expected_connector_count:
            raise ValueError("observed connectors cannot exceed expected connectors")
        expected = (
            self.observed_connector_count / self.expected_connector_count
            if self.expected_connector_count
            else 0
        )
        if abs(self.coverage_fraction - expected) > 1e-9:
            raise ValueError("prediction evidence coverage is inconsistent")
        if self.complete != (
            self.expected_connector_count > 0
            and self.observed_connector_count == self.expected_connector_count
        ):
            raise ValueError("prediction evidence completeness is inconsistent")
        return self


class PredictionResolution(ResolutionModel):
    schema_version: str = "1.0"
    prediction_id: str = Field(alias="predictionID")
    date: date
    question: str
    choices: list[PredictionChoice]
    metric: str
    rule_version: int = Field(ge=1)
    rule: str
    locks_at: AwareDatetime
    evidence_from: AwareDatetime
    evidence_to: AwareDatetime
    target_at: AwareDatetime
    state: PredictionResolutionState
    outcome: PredictionOutcome | None = None
    observed_value_mw: float | None = Field(default=None, alias="observedValueMW")
    observed_at: AwareDatetime | None = None
    near_balanced_threshold_mw: float = Field(
        alias="nearBalancedThresholdMW",
        ge=0,
    )
    coverage: PredictionEvidenceCoverage
    source_ids: list[str] = Field(default_factory=list, alias="sourceIDs")
    source_record_ids: list[str] = Field(
        default_factory=list,
        alias="sourceRecordIDs",
    )
    source_revision_keys: list[str] = Field(default_factory=list)
    revision_watermark_at: AwareDatetime | None = None
    evidence_checksum: str = Field(min_length=64, max_length=64)
    resolution_revision: int = Field(default=0, ge=0)
    is_correction: bool = False
    computed_at: AwareDatetime
    reason: str

    @model_validator(mode="after")
    def state_matches_evidence(self) -> Self:
        if self.is_correction != (self.resolution_revision > 1):
            raise ValueError(
                "is_correction must identify revisions after the initial result"
            )
        if self.state is PredictionResolutionState.PENDING:
            if self.outcome is not None or self.observed_value_mw is not None:
                raise ValueError("pending predictions cannot expose an outcome")
        if self.state is PredictionResolutionState.RESOLVED:
            if (
                self.outcome is None
                or self.observed_value_mw is None
                or self.observed_at is None
                or not self.coverage.complete
            ):
                raise ValueError("resolved predictions require complete evidence")
        if self.outcome is not None and self.state is not PredictionResolutionState.RESOLVED:
            raise ValueError("only resolved predictions can expose an outcome")
        return self
