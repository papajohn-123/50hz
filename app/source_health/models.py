from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.api.models import MetricFamily


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class SourceHealthModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class SourceDeliveryState(StrEnum):
    HEALTHY = "healthy"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class SourceFactState(StrEnum):
    LIVE = "live"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class SourceHealthStatus(SourceHealthModel):
    source_id: str = Field(alias="sourceID")
    publisher: str
    dataset: str
    display_name: str
    documentation_url: str | None = None
    licence_url: str | None = None
    attribution: str
    expected_fact_cadence_seconds: int = Field(gt=0)
    delivery_state: SourceDeliveryState
    delivery_lag_seconds: int | None = Field(default=None, ge=0)
    last_attempted_at: AwareDatetime | None = None
    last_attempt_state: str | None = None
    last_succeeded_at: AwareDatetime | None = None
    fact_state: SourceFactState
    fact_families: list[MetricFamily] = Field(default_factory=list)
    observed_at: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    fact_age_seconds: int | None = Field(default=None, ge=0)
    note: str


class SourceHealthResponse(SourceHealthModel):
    schema_version: str = "1.0"
    evaluated_at: AwareDatetime
    source_count: int = Field(ge=0)
    sources: list[SourceHealthStatus]
    definitions: dict[str, str] = Field(
        default_factory=lambda: {
            "deliveryState": (
                "Whether the 50Hz worker is successfully receiving the source "
                "within a cadence-derived threshold."
            ),
            "factState": (
                "Whether a current fact from that source validly covers the "
                "present grid view; forecast/event-only sources are not applicable."
            ),
        }
    )
