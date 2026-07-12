from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Iterable, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.events.models import EventStatus
from app.events.revisions import EventAuthority, ReportedEventRevision


SCORING_METHODOLOGY_VERSION = "50hz.event-relevance.v1"
MAX_SELECTED_EVENTS = 3


class RelevanceMethodology(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal["50hz.event-relevance.v1"] = SCORING_METHODOLOGY_VERSION
    maximum_score: Literal[100] = 100
    maximum_selected_events: Literal[3] = MAX_SELECTED_EVENTS
    causation_rule: str = (
        "Use source-reported attributes only; never infer that an event caused "
        "a simultaneous change in generation, demand, imports, carbon, or frequency."
    )
    grouping_rule: str = (
        "Group only when a non-empty source asset ID is explicitly marked reliable "
        "and the source-reported effective windows overlap."
    )
    stable_tie_breakers: tuple[str, ...] = (
        "score descending",
        "authority descending",
        "reported unavailable MW descending with missing last",
        "publication time descending",
        "event ID ascending",
    )


class ScoreComponents(BaseModel):
    model_config = ConfigDict(frozen=True)

    authority: int = Field(ge=0, le=20)
    system_warning: int = Field(ge=0, le=10)
    unavailable_mw: int = Field(ge=0, le=15)
    unavailable_capacity_percent: int = Field(ge=0, le=15)
    timing: int = Field(ge=0, le=15)
    duration: int = Field(ge=0, le=5)
    reported_planning_state: int = Field(ge=0, le=5)
    novelty: int = Field(ge=0, le=5)
    material_revision: int = Field(ge=0, le=10)

    @property
    def total(self) -> int:
        return sum(
            (
                self.authority,
                self.system_warning,
                self.unavailable_mw,
                self.unavailable_capacity_percent,
                self.timing,
                self.duration,
                self.reported_planning_state,
                self.novelty,
                self.material_revision,
            )
        )


class EventRelevanceScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    methodology_version: Literal["50hz.event-relevance.v1"] = (
        SCORING_METHODOLOGY_VERSION
    )
    as_of: AwareDatetime
    event: ReportedEventRevision
    eligible: bool
    exclusion_reason: str | None = None
    unavailable_capacity_percent: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    components: ScoreComponents
    total_score: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_total(self) -> "EventRelevanceScore":
        if self.total_score != self.components.total:
            raise ValueError("total_score must equal the component sum")
        if not self.eligible and self.total_score != 0:
            raise ValueError("ineligible events must have a zero relevance score")
        return self


class RankedEventGroup(BaseModel):
    model_config = ConfigDict(frozen=True)

    group_key: str
    reliable_asset_id: str | None = None
    member_event_ids: tuple[str, ...] = Field(min_length=1)
    representative: EventRelevanceScore


class RankedEventSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    methodology: RelevanceMethodology = Field(default_factory=RelevanceMethodology)
    as_of: AwareDatetime
    requested_limit: int = Field(ge=1, le=MAX_SELECTED_EVENTS)
    eligible_group_count: int = Field(ge=0)
    selected: tuple[RankedEventGroup, ...]

    @model_validator(mode="after")
    def validate_selection_bound(self) -> "RankedEventSelection":
        if len(self.selected) > self.requested_limit:
            raise ValueError("selected events exceed the requested limit")
        if len(self.selected) > MAX_SELECTED_EVENTS:
            raise ValueError("selected events exceed the product maximum")
        return self


def score_event(
    event: ReportedEventRevision,
    *,
    as_of: datetime,
) -> EventRelevanceScore:
    instant = _aware_utc(as_of, "as_of")
    capacity_percent = _capacity_percent(event)
    if event.status in {
        EventStatus.RESOLVED,
        EventStatus.SUPERSEDED,
        EventStatus.WITHDRAWN,
    }:
        return EventRelevanceScore(
            as_of=instant,
            event=event,
            eligible=False,
            exclusion_reason=f"terminal_status:{event.status.value}",
            unavailable_capacity_percent=capacity_percent,
            components=_zero_components(),
            total_score=0,
        )
    if (
        event.effective_end is not None
        and event.effective_end.astimezone(UTC) <= instant
    ):
        return EventRelevanceScore(
            as_of=instant,
            event=event,
            eligible=False,
            exclusion_reason="reported_window_ended",
            unavailable_capacity_percent=capacity_percent,
            components=_zero_components(),
            total_score=0,
        )

    components = ScoreComponents(
        authority=_authority_score(event.authority),
        system_warning=(10 if event.authority == EventAuthority.SYSTEM_WARNING else 0),
        unavailable_mw=_unavailable_mw_score(event.unavailable_mw),
        unavailable_capacity_percent=_capacity_percent_score(capacity_percent),
        timing=_timing_score(event, instant),
        duration=_duration_score(event),
        reported_planning_state=_planning_score(event.planned),
        novelty=_novelty_score(event, instant),
        material_revision=_material_revision_score(event),
    )
    return EventRelevanceScore(
        as_of=instant,
        event=event,
        eligible=True,
        unavailable_capacity_percent=capacity_percent,
        components=components,
        total_score=components.total,
    )


def rank_relevant_events(
    events: Iterable[ReportedEventRevision],
    *,
    as_of: datetime,
    limit: int = MAX_SELECTED_EVENTS,
) -> RankedEventSelection:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if not 1 <= limit <= MAX_SELECTED_EVENTS:
        raise ValueError("limit must be between one and three")
    instant = _aware_utc(as_of, "as_of")
    latest = _latest_revisions(events)
    scores = [score_event(event, as_of=instant) for event in latest]
    eligible = [score for score in scores if score.eligible]

    ranked_groups: list[RankedEventGroup] = []
    for group_key, reliable_asset_id, members in _group_eligible_scores(eligible):
        representative = min(members, key=_score_sort_key)
        ranked_groups.append(
            RankedEventGroup(
                group_key=group_key,
                reliable_asset_id=reliable_asset_id,
                member_event_ids=tuple(
                    sorted({member.event.event_id for member in members})
                ),
                representative=representative,
            )
        )

    ranked_groups.sort(key=lambda group: _score_sort_key(group.representative))
    return RankedEventSelection(
        as_of=instant,
        requested_limit=limit,
        eligible_group_count=len(ranked_groups),
        selected=tuple(ranked_groups[:limit]),
    )


def _latest_revisions(
    events: Iterable[ReportedEventRevision],
) -> list[ReportedEventRevision]:
    by_event: dict[str, ReportedEventRevision] = {}
    seen_revisions: dict[tuple[str, int], ReportedEventRevision] = {}
    for event in events:
        revision_key = (event.event_id, event.revision)
        duplicate = seen_revisions.get(revision_key)
        if duplicate is not None and duplicate != event:
            raise ValueError("conflicting duplicate event revision")
        seen_revisions[revision_key] = event
        existing = by_event.get(event.event_id)
        if existing is None or event.revision > existing.revision:
            by_event[event.event_id] = event
    return list(by_event.values())


def _group_eligible_scores(
    scores: list[EventRelevanceScore],
) -> list[tuple[str, str | None, list[EventRelevanceScore]]]:
    standalone: list[tuple[str, str | None, list[EventRelevanceScore]]] = []
    by_asset: dict[str, list[EventRelevanceScore]] = defaultdict(list)
    for score in scores:
        event = score.event
        if event.asset_identity_reliable and event.asset_id:
            by_asset[event.asset_id.strip().casefold()].append(score)
        else:
            standalone.append((f"event:{event.event_id}", None, [score]))

    for asset_id in sorted(by_asset):
        remaining = sorted(
            by_asset[asset_id],
            key=lambda score: score.event.event_id,
        )
        components: list[list[EventRelevanceScore]] = []
        while remaining:
            component = [remaining.pop(0)]
            changed = True
            while changed:
                changed = False
                for candidate in tuple(remaining):
                    if any(
                        _reported_windows_overlap(candidate.event, member.event)
                        for member in component
                    ):
                        remaining.remove(candidate)
                        component.append(candidate)
                        changed = True
            components.append(component)

        for component in components:
            suffix = (
                ""
                if len(components) == 1
                else f":{min(item.event.event_id for item in component)}"
            )
            standalone.append(
                (f"asset:{asset_id}{suffix}", asset_id, component)
            )
    return standalone


def _reported_windows_overlap(
    left: ReportedEventRevision,
    right: ReportedEventRevision,
) -> bool:
    if left.effective_start is None or right.effective_start is None:
        return False
    left_start = left.effective_start.astimezone(UTC)
    right_start = right.effective_start.astimezone(UTC)
    left_end = (
        left.effective_end.astimezone(UTC)
        if left.effective_end is not None
        else datetime.max.replace(tzinfo=UTC)
    )
    right_end = (
        right.effective_end.astimezone(UTC)
        if right.effective_end is not None
        else datetime.max.replace(tzinfo=UTC)
    )
    return max(left_start, right_start) < min(left_end, right_end)


def _score_sort_key(score: EventRelevanceScore) -> tuple:
    event = score.event
    unavailable_missing = event.unavailable_mw is None
    unavailable_value = event.unavailable_mw if event.unavailable_mw is not None else 0
    return (
        -score.total_score,
        -_authority_priority(event.authority),
        unavailable_missing,
        -unavailable_value,
        -event.published_at.astimezone(UTC).timestamp(),
        event.event_id,
    )


def _authority_priority(authority: EventAuthority) -> int:
    return {
        EventAuthority.SYSTEM_WARNING: 3,
        EventAuthority.AUTHORITATIVE_NOTICE: 2,
        EventAuthority.OTHER_REPORTED: 1,
    }[authority]


def _authority_score(authority: EventAuthority) -> int:
    return {
        EventAuthority.SYSTEM_WARNING: 20,
        EventAuthority.AUTHORITATIVE_NOTICE: 20,
        EventAuthority.OTHER_REPORTED: 8,
    }[authority]


def _unavailable_mw_score(value: float | None) -> int:
    if value is None or value == 0:
        return 0
    if value < 100:
        return 2
    if value < 500:
        return 6
    if value < 1_000:
        return 10
    return 15


def _capacity_percent(event: ReportedEventRevision) -> float | None:
    if event.unavailable_mw is None or event.normal_capacity_mw is None:
        return None
    return event.unavailable_mw / event.normal_capacity_mw * 100


def _capacity_percent_score(value: float | None) -> int:
    if value is None or value <= 0:
        return 0
    if value < 10:
        return 2
    if value < 25:
        return 5
    if value < 50:
        return 9
    if value < 75:
        return 12
    return 15


def _timing_score(event: ReportedEventRevision, as_of: datetime) -> int:
    start = (
        event.effective_start.astimezone(UTC)
        if event.effective_start is not None
        else None
    )
    end = (
        event.effective_end.astimezone(UTC)
        if event.effective_end is not None
        else None
    )
    if start is None:
        return 0
    if start <= as_of and (end is None or end > as_of):
        return 15
    if start <= as_of:
        return 0
    until_start = start - as_of
    if until_start <= timedelta(hours=6):
        return 12
    if until_start <= timedelta(hours=24):
        return 8
    return 3


def _duration_score(event: ReportedEventRevision) -> int:
    if event.effective_start is None or event.effective_end is None:
        return 0
    duration = event.effective_end - event.effective_start
    if duration >= timedelta(hours=24):
        return 5
    if duration >= timedelta(hours=6):
        return 3
    if duration > timedelta(0):
        return 1
    return 0


def _planning_score(planned: bool | None) -> int:
    if planned is False:
        return 5
    if planned is True:
        return 2
    return 0


def _novelty_score(event: ReportedEventRevision, as_of: datetime) -> int:
    if event.revision != 1:
        return 0
    age = as_of - event.published_at.astimezone(UTC)
    if age < timedelta(0):
        return 0
    if age <= timedelta(hours=6):
        return 5
    if age <= timedelta(hours=24):
        return 3
    return 0


def _material_revision_score(event: ReportedEventRevision) -> int:
    if event.revision > 1 and event.status == EventStatus.UPDATED:
        return 10 if event.material_reason is not None else 0
    return 0


def _zero_components() -> ScoreComponents:
    return ScoreComponents(
        authority=0,
        system_warning=0,
        unavailable_mw=0,
        unavailable_capacity_percent=0,
        timing=0,
        duration=0,
        reported_planning_state=0,
        novelty=0,
        material_revision=0,
    )


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)
