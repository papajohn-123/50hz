from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    computed_field,
    field_validator,
    model_validator,
)

from app.domain.enums import (
    DataClassification,
    FactQuality,
    FlowDirection,
    FreshnessState,
)


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    """Strict JSON contract shared by the public API and the iOS DTOs."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


class SourceReference(ApiModel):
    id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=160)
    dataset: str = Field(min_length=1, max_length=160)
    url: HttpUrl | None = None
    licence_url: HttpUrl | None = None


class Provenance(ApiModel):
    """Timing and origin carried by every user-visible value.

    ``effective_at`` is the instant the value applies to. Observations also carry
    ``observed_at``; forecasts instead carry the distinct model issue time.
    """

    source: SourceReference
    classification: DataClassification
    effective_at: AwareDatetime
    observed_at: AwareDatetime | None = None
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime
    forecast_issued_at: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    raw_payload_id: UUID | None = None
    methodology_version: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_classification_timestamps(self) -> Self:
        if self.classification is DataClassification.FORECAST:
            if self.forecast_issued_at is None:
                raise ValueError("forecast facts require forecast_issued_at")
            if self.forecast_issued_at > self.effective_at:
                raise ValueError("forecast_issued_at cannot be after effective_at")
        elif self.classification in {
            DataClassification.OBSERVED,
            DataClassification.REPORTED,
        }:
            if self.observed_at is None:
                raise ValueError("observed and reported facts require observed_at")

        if self.valid_until is not None and self.valid_until <= self.effective_at:
            raise ValueError("valid_until must be after effective_at")
        return self


class Freshness(ApiModel):
    state: FreshnessState
    evaluated_at: AwareDatetime
    age_seconds: float | None = Field(default=None, ge=0)
    expected_cadence_seconds: int = Field(gt=0)
    fresh_for_seconds: int = Field(gt=0)
    stale_after_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if self.fresh_for_seconds >= self.stale_after_seconds:
            raise ValueError("fresh threshold must precede stale threshold")
        if self.state is FreshnessState.UNAVAILABLE and self.age_seconds is not None:
            raise ValueError("unavailable freshness cannot have an age")
        if self.state is not FreshnessState.UNAVAILABLE and self.age_seconds is None:
            raise ValueError("available freshness requires an age")
        return self

    @classmethod
    def assess(
        cls,
        source_time: datetime,
        *,
        expected_cadence_seconds: int,
        evaluated_at: datetime | None = None,
        fresh_for_seconds: int | None = None,
        stale_after_seconds: int | None = None,
    ) -> Freshness:
        if source_time.tzinfo is None:
            raise ValueError("source_time must be timezone-aware")
        evaluated_at = evaluated_at or datetime.now(UTC)
        if evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if expected_cadence_seconds <= 0:
            raise ValueError("expected cadence must be positive")

        fresh_for = fresh_for_seconds or max(expected_cadence_seconds * 2, 120)
        stale_after = stale_after_seconds or max(expected_cadence_seconds * 5, 600)
        if fresh_for >= stale_after:
            raise ValueError("fresh threshold must precede stale threshold")

        age = max(0.0, (evaluated_at - source_time).total_seconds())
        if age <= fresh_for:
            state = FreshnessState.FRESH
        elif age < stale_after:
            state = FreshnessState.DELAYED
        else:
            state = FreshnessState.STALE

        return cls(
            state=state,
            evaluated_at=evaluated_at,
            age_seconds=age,
            expected_cadence_seconds=expected_cadence_seconds,
            fresh_for_seconds=fresh_for,
            stale_after_seconds=stale_after,
        )

    @classmethod
    def unavailable(
        cls,
        *,
        expected_cadence_seconds: int,
        evaluated_at: datetime | None = None,
    ) -> Freshness:
        cadence = expected_cadence_seconds
        return cls(
            state=FreshnessState.UNAVAILABLE,
            evaluated_at=evaluated_at or datetime.now(UTC),
            expected_cadence_seconds=cadence,
            fresh_for_seconds=max(cadence * 2, 120),
            stale_after_seconds=max(cadence * 5, 600),
        )


class NumericFact(ApiModel):
    value: float
    unit: str = Field(min_length=1, max_length=32)
    quality: FactQuality = FactQuality.VALIDATED
    provenance: Provenance
    freshness: Freshness

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("fact values must be finite")
        return value


class GenerationFact(ApiModel):
    series_key: str = Field(min_length=1, max_length=120)
    fuel: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    asset_id: str | None = Field(default=None, max_length=120)
    generation: NumericFact

    @model_validator(mode="after")
    def require_megawatts(self) -> Self:
        if self.generation.unit != "MW":
            raise ValueError("generation facts must use MW")
        return self


class InterconnectorFact(ApiModel):
    connector_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)
    counterparty: str = Field(min_length=1, max_length=120)
    flow: NumericFact = Field(
        description="Signed MW: positive imports into Britain; negative exports."
    )

    @model_validator(mode="after")
    def require_megawatts(self) -> Self:
        if self.flow.unit != "MW":
            raise ValueError("interconnector flow facts must use MW")
        return self

    @computed_field
    @property
    def direction(self) -> FlowDirection:
        if self.flow.value > 0:
            return FlowDirection.IMPORT
        if self.flow.value < 0:
            return FlowDirection.EXPORT
        return FlowDirection.NEUTRAL


class GridSnapshot(ApiModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.")
    snapshot_id: UUID
    effective_at: AwareDatetime
    generated_at: AwareDatetime
    freshness: FreshnessState
    generation: list[GenerationFact]
    demand: NumericFact | None = None
    frequency: NumericFact | None = None
    carbon_intensity: NumericFact | None = None
    net_import: NumericFact | None = None
    interconnectors: list[InterconnectorFact] = Field(default_factory=list)
    missing_datasets: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_series(self) -> Self:
        generation_keys = [item.series_key for item in self.generation]
        if len(generation_keys) != len(set(generation_keys)):
            raise ValueError("generation series keys must be unique")
        connector_ids = [item.connector_id for item in self.interconnectors]
        if len(connector_ids) != len(set(connector_ids)):
            raise ValueError("interconnector ids must be unique")
        return self


class TimelinePoint(ApiModel):
    effective_at: AwareDatetime
    generation_total: NumericFact | None = None
    demand: NumericFact | None = None
    frequency: NumericFact | None = None
    carbon_intensity: NumericFact | None = None
    net_import: NumericFact | None = None
    generation: list[GenerationFact] = Field(default_factory=list)


class GridTimeline(ApiModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.")
    window_start: AwareDatetime
    window_end: AwareDatetime
    resolution_seconds: int = Field(gt=0)
    generated_at: AwareDatetime
    observed_through: AwareDatetime | None = None
    forecast_from: AwareDatetime | None = None
    points: list[TimelinePoint]

    @model_validator(mode="after")
    def validate_window_and_points(self) -> Self:
        if self.window_end <= self.window_start:
            raise ValueError("timeline window_end must be after window_start")
        timestamps = [point.effective_at for point in self.points]
        if timestamps != sorted(timestamps):
            raise ValueError("timeline points must be sorted by effective_at")
        if len(timestamps) != len(set(timestamps)):
            raise ValueError("timeline point timestamps must be unique")
        if any(
            timestamp < self.window_start or timestamp > self.window_end
            for timestamp in timestamps
        ):
            raise ValueError("timeline points must fall inside the response window")
        return self

