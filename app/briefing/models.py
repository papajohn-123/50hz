"""Typed, source-neutral contracts for deterministic grid briefings."""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


METHODOLOGY_VERSION = "50hz.briefing.v1"
DISPLAY_TIMEZONE = "Europe/London"
MAX_SECTION_ITEMS = 3


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(word.capitalize() for word in tail)


class BriefingModel(BaseModel):
    # Briefing models are also the public mobile API contract.  Field names stay
    # idiomatic snake_case inside Python while FastAPI can serialize the aliases
    # as consistent lower camelCase at the boundary.
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class BriefingStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    OFFLINE = "offline"
    OBSERVED_ONLY = "observed_only"
    EMPTY = "empty"


class BriefingSection(StrEnum):
    NOW = "now"
    CHANGES = "changes"
    NEXT = "next"
    REPORTED_EVENTS = "reported_events"
    BEST_WINDOW = "best_window"


class SourceState(StrEnum):
    LIVE = "live"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class ChangeDirection(StrEnum):
    UP = "up"
    DOWN = "down"


class FutureFactClass(StrEnum):
    FORECAST = "forecast"
    REPORTED = "reported"


class CurrentFactClass(StrEnum):
    OBSERVED = "observed"
    ESTIMATED = "estimated"
    DERIVED = "derived"
    REPORTED = "reported"


class CurrentPositionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ReportedEventTiming(StrEnum):
    ACTIVE = "active"
    UPCOMING = "upcoming"


class ReportedEventSeverity(StrEnum):
    INFO = "info"
    NOTABLE = "notable"
    MATERIAL = "material"
    CRITICAL = "critical"


class DisplayPeriodName(StrEnum):
    OVERNIGHT = "overnight"
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class ComparisonPeriod(BriefingModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def end_follows_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("comparison period end must follow start")
        return self


class RevisionWatermark(BriefingModel):
    revision_token: str = Field(min_length=1)
    as_of: AwareDatetime
    observed_through: AwareDatetime | None = None
    forecast_captured_through: AwareDatetime | None = None
    reported_through: AwareDatetime | None = None

    @model_validator(mode="after")
    def component_times_do_not_exceed_watermark(self) -> Self:
        values = (
            self.observed_through,
            self.forecast_captured_through,
            self.reported_through,
        )
        if any(value is not None and value > self.as_of for value in values):
            raise ValueError("revision component times cannot exceed watermark as_of")
        return self


class BriefingSourceStatus(BriefingModel):
    source_id: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    state: SourceState
    revision: int = Field(default=0, ge=0)
    observed_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime | None = None
    detail: str | None = Field(default=None, min_length=1)


class BriefingCoverageInput(BriefingModel):
    missing_families: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CurrentValueInput(BriefingModel):
    stable_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    fact_class: CurrentFactClass
    observed_at: AwareDatetime
    source_ids: list[str] = Field(min_length=1)
    priority: float = Field(ge=0, le=1, allow_inf_nan=False)
    revision: int = Field(default=0, ge=0)


class CurrentPositionInput(BriefingModel):
    values: list[CurrentValueInput] = Field(default_factory=list)
    expected_metric_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def expected_metrics_are_unique(self) -> Self:
        if len(self.expected_metric_ids) != len(set(self.expected_metric_ids)):
            raise ValueError("expected current metric IDs must be unique")
        return self


class ObservedChangeInput(BriefingModel):
    stable_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    current_value: float = Field(allow_inf_nan=False)
    previous_value: float = Field(allow_inf_nan=False)
    delta: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    observed_at: AwareDatetime
    comparison_period_id: str = Field(min_length=1)
    meaningful_threshold: float = Field(gt=0, allow_inf_nan=False)
    significance: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_ids: list[str] = Field(min_length=1)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def delta_matches_values(self) -> Self:
        expected = self.current_value - self.previous_value
        if not math.isclose(self.delta, expected, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError("delta must equal current_value minus previous_value")
        return self


class FutureMomentInput(BriefingModel):
    stable_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    fact_class: FutureFactClass
    importance: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_ids: list[str] = Field(min_length=1)
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1)
    updated_at: AwareDatetime
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def optional_value_and_window_are_consistent(self) -> Self:
        if (self.value is None) != (self.unit is None):
            raise ValueError("future value and unit must be supplied together")
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("future moment end must follow start")
        return self


class ReportedEventInput(BriefingModel):
    stable_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    revision_number: int = Field(ge=0)
    title: str = Field(min_length=1)
    summary: str | None = Field(default=None, min_length=1)
    severity: ReportedEventSeverity
    published_at: AwareDatetime
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def event_window_is_ordered(self) -> Self:
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("reported event end must follow start")
        return self


class BestWindowInput(BriefingModel):
    stable_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    start: AwareDatetime
    end: AwareDatetime
    average_value: float = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    coverage_fraction: Literal[1.0] = 1.0
    fact_class: Literal["forecast"] = "forecast"
    methodology_version: str = Field(min_length=1)
    captured_at: AwareDatetime

    @model_validator(mode="after")
    def window_is_future_and_ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("best window end must follow start")
        return self


class BriefingInput(BriefingModel):
    as_of: AwareDatetime
    now: CurrentPositionInput = Field(default_factory=CurrentPositionInput)
    changes: list[ObservedChangeInput] = Field(default_factory=list)
    next_moments: list[FutureMomentInput] = Field(default_factory=list)
    reported_events: list[ReportedEventInput] = Field(default_factory=list)
    best_window: BestWindowInput | None = None
    comparison_periods: list[ComparisonPeriod] = Field(default_factory=list)
    source_statuses: list[BriefingSourceStatus] = Field(default_factory=list)
    coverage: BriefingCoverageInput = Field(default_factory=BriefingCoverageInput)
    revision_watermark: RevisionWatermark

    @model_validator(mode="after")
    def references_and_watermark_are_valid(self) -> Self:
        period_ids = [period.id for period in self.comparison_periods]
        if len(period_ids) != len(set(period_ids)):
            raise ValueError("comparison period IDs must be unique")
        known_periods = set(period_ids)
        unknown = {
            change.comparison_period_id
            for change in self.changes
            if change.comparison_period_id not in known_periods
        }
        if unknown:
            raise ValueError("every change must reference a comparison period")
        if self.revision_watermark.as_of > self.as_of:
            raise ValueError("revision watermark cannot be after briefing now")
        return self


class BriefingCurrentValue(BriefingModel):
    stable_id: str
    metric_id: str
    label: str
    value: float
    unit: str
    fact_class: CurrentFactClass
    observed_at: AwareDatetime
    source_ids: list[str]


class BriefingCurrentPosition(BriefingModel):
    status: CurrentPositionStatus
    as_of: AwareDatetime | None
    values: list[BriefingCurrentValue] = Field(max_length=MAX_SECTION_ITEMS)
    missing_metric_ids: list[str]
    text: str = Field(min_length=1)


class BriefingObservedChange(BriefingModel):
    stable_id: str
    metric_id: str
    label: str
    direction: ChangeDirection
    current_value: float
    previous_value: float
    delta: float
    unit: str
    observed_at: AwareDatetime
    comparison_period_id: str
    significance: float
    source_ids: list[str]
    text: str = Field(min_length=1)


class BriefingFutureMoment(BriefingModel):
    stable_id: str
    label: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None
    fact_class: FutureFactClass
    importance: float
    source_ids: list[str]
    value: float | None
    unit: str | None
    text: str = Field(min_length=1)


class BriefingReportedEvent(BriefingModel):
    stable_id: str
    revision_id: str
    revision_number: int
    title: str
    severity: ReportedEventSeverity
    timing: ReportedEventTiming
    published_at: AwareDatetime
    starts_at: AwareDatetime | None
    ends_at: AwareDatetime | None
    source_ids: list[str]
    evidence_class: Literal["reported"] = "reported"
    text: str = Field(min_length=1)


class BriefingReportedEvents(BriefingModel):
    items: list[BriefingReportedEvent] = Field(max_length=MAX_SECTION_ITEMS)
    total_count: int = Field(ge=0)


class BriefingBestWindow(BriefingModel):
    stable_id: str
    label: str
    start: AwareDatetime
    end: AwareDatetime
    average_value: float
    unit: str
    source_ids: list[str]
    coverage_fraction: Literal[1.0]
    fact_class: Literal["forecast"]
    methodology_version: str
    captured_at: AwareDatetime
    text: str = Field(min_length=1)


class BriefingCoverage(BriefingModel):
    status: BriefingStatus
    available_sections: list[BriefingSection]
    missing_families: list[str]
    source_counts_by_state: dict[SourceState, int]
    notes: list[str]


class DisplayPeriod(BriefingModel):
    timezone: Literal["Europe/London"] = DISPLAY_TIMEZONE
    local_date: date
    name: DisplayPeriodName
    label: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class BriefingMethodology(BriefingModel):
    version: Literal["50hz.briefing.v1"] = METHODOLOGY_VERSION
    timezone: Literal["Europe/London"] = DISPLAY_TIMEZONE
    max_current_values: Literal[3] = MAX_SECTION_ITEMS
    max_changes: Literal[3] = MAX_SECTION_ITEMS
    max_next_moments: Literal[3] = MAX_SECTION_ITEMS
    max_reported_events: Literal[3] = MAX_SECTION_ITEMS
    meaningful_change_rule: str = (
        "Only non-zero observed deltas meeting the supplied metric threshold qualify."
    )
    current_ranking: str = (
        "Priority descending, observation time descending, then metric ID and "
        "stable ID ascending."
    )
    change_ranking: str = (
        "Significance descending, threshold multiple descending, observation "
        "time descending, then metric ID and stable ID ascending."
    )
    next_ranking: str = (
        "Start time ascending, importance descending, then stable ID ascending."
    )
    event_ranking: str = (
        "Severity descending, active before next-24-hour upcoming reports, then "
        "active publication recency or upcoming start time, and stable ID ascending."
    )
    revision_rule: str = (
        "Stable identities are deduplicated to the highest revision before ranking."
    )
    causal_attribution: Literal[False] = False


class Briefing(BriefingModel):
    schema_version: Literal["1.0"] = "1.0"
    methodology: BriefingMethodology = Field(default_factory=BriefingMethodology)
    generated_at: AwareDatetime
    as_of: AwareDatetime
    now: BriefingCurrentPosition
    display_period: DisplayPeriod
    headline: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    changes: list[BriefingObservedChange] = Field(max_length=MAX_SECTION_ITEMS)
    next_moments: list[BriefingFutureMoment] = Field(max_length=MAX_SECTION_ITEMS)
    reported_events: BriefingReportedEvents
    best_window: BriefingBestWindow | None
    coverage: BriefingCoverage
    source_statuses: list[BriefingSourceStatus]
    comparison_periods: list[ComparisonPeriod]
    revision_watermark: RevisionWatermark
    limitations: list[str] = Field(min_length=1)
