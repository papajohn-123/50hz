from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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

