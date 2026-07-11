from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl

from app.events.models import EvidenceFact


class SourceCitation(BaseModel):
    source_id: str
    publisher: str
    title: str
    canonical_url: HttpUrl
    published_at: datetime | None = None


class EvidencePacket(BaseModel):
    event_id: str
    revision: int = Field(ge=1)
    event_type: str
    status: str
    as_of: datetime
    freshness: str
    facts: list[EvidenceFact] = Field(min_length=1)
    permitted_comparisons: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    source_refs: dict[str, SourceCitation]
    cause_reported: bool = False


class GroundedExplanation(BaseModel):
    headline: str = Field(min_length=1, max_length=100)
    plain_language: str = Field(min_length=1, max_length=700)
    why_it_matters: str | None = Field(default=None, max_length=500)
    caveat: str | None = Field(default=None, max_length=400)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    suggested_questions: list[str] = Field(default_factory=list, max_length=3)


class ExplanationResult(BaseModel):
    explanation: GroundedExplanation
    model: str
    used_fallback: bool
    input_tokens: int | None = None
    output_tokens: int | None = None

