from datetime import datetime
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceClass(StrEnum):
    REPORTED = "reported"
    OBSERVED = "observed"
    DERIVED = "derived"
    FORECAST = "forecast"


class Severity(StrEnum):
    INFO = "info"
    NOTABLE = "notable"
    IMPORTANT = "important"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EventStatus(StrEnum):
    OPEN = "open"
    UPDATED = "updated"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class EvidenceFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: str
    metric: str
    label: str
    value: int | float | str | bool
    unit: str | None = None
    observed_at: datetime
    source_record_ids: list[str] = Field(min_length=1)


class EventCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    rule_version: int = Field(ge=1)
    event_type: str
    subject_type: str
    subject_id: str | None = None
    occurred_at: datetime
    severity: Severity
    evidence_class: EvidenceClass
    confidence: Confidence
    confidence_reasons: list[str] = Field(min_length=1)
    dedupe_key: str
    facts: list[EvidenceFact] = Field(min_length=1)
    cause_reported: bool = False

    @model_validator(mode="after")
    def reported_cause_requires_reported_evidence(self) -> "EventCandidate":
        if self.cause_reported and self.evidence_class is not EvidenceClass.REPORTED:
            raise ValueError("A reported cause requires reported evidence")
        return self

    @property
    def stable_id(self) -> str:
        digest = sha256(self.dedupe_key.encode("utf-8")).hexdigest()[:20]
        return f"evt_{digest}"


class DetectedEvent(BaseModel):
    event_id: str
    revision: int = Field(ge=1)
    status: EventStatus
    candidate: EventCandidate
    opened_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None

