from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.events.models import EventStatus
from app.events.relevance import (
    MAX_SELECTED_EVENTS,
    RelevanceMethodology,
    rank_relevant_events,
    score_event,
)
from app.events.revisions import (
    EventAuthority,
    EventLifecycleHistory,
    ReportedEventRevision,
    RevisionField,
    append_revision,
    diff_revisions,
)


NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


def revision(
    event_id: str,
    *,
    revision_number: int = 1,
    status: EventStatus | None = None,
    published_at: datetime | None = None,
    effective_start: datetime | None = NOW - timedelta(hours=1),
    effective_end: datetime | None = NOW + timedelta(hours=7),
    authority: EventAuthority = EventAuthority.AUTHORITATIVE_NOTICE,
    asset_id: str | None = None,
    asset_name: str | None = None,
    reliable_asset: bool = False,
    unavailable_mw: float | None = 500,
    normal_capacity_mw: float | None = 1_000,
    planned: bool | None = False,
    reported_cause: str | None = None,
    evidence_checksum: str | None = None,
    material_reason: str | None = None,
    superseded_by_event_id: str | None = None,
) -> ReportedEventRevision:
    resolved_status = status or (
        EventStatus.OPEN if revision_number == 1 else EventStatus.UPDATED
    )
    resolved_reason = material_reason
    if revision_number > 1 and resolved_reason is None:
        resolved_reason = "Source published a material revision"
    return ReportedEventRevision(
        event_id=event_id,
        revision=revision_number,
        status=resolved_status,
        published_at=published_at
        or NOW - timedelta(hours=2) + timedelta(minutes=revision_number),
        effective_start=effective_start,
        effective_end=effective_end,
        authority=authority,
        asset_id=asset_id,
        asset_name=asset_name,
        asset_identity_reliable=reliable_asset,
        unavailable_mw=unavailable_mw,
        normal_capacity_mw=normal_capacity_mw,
        planned=planned,
        reported_cause=reported_cause,
        evidence_checksum=evidence_checksum
        or f"checksum:{event_id}:{revision_number}",
        material_reason=resolved_reason,
        superseded_by_event_id=superseded_by_event_id,
        source_record_ids=(f"source:{event_id}:{revision_number}",),
    )


def test_revision_delta_tracks_every_required_field_without_mutating_history() -> None:
    first = revision(
        "evt-1",
        unavailable_mw=400,
        normal_capacity_mw=800,
        reported_cause=None,
        evidence_checksum="checksum-1",
    )
    second = revision(
        "evt-1",
        revision_number=2,
        published_at=NOW,
        effective_start=NOW,
        effective_end=NOW + timedelta(days=2),
        unavailable_mw=700,
        normal_capacity_mw=900,
        reported_cause="Reported equipment fault",
        evidence_checksum="checksum-2",
        material_reason="Capacity, timing and cause changed",
    )

    delta = diff_revisions(first, second)

    assert delta.model_version == "50hz.reported-event-delta.v1"
    assert delta.from_revision == 1
    assert delta.to_revision == 2
    assert delta.changed_fields == (
        RevisionField.UNAVAILABLE_MW,
        RevisionField.NORMAL_CAPACITY_MW,
        RevisionField.EFFECTIVE_START,
        RevisionField.EFFECTIVE_END,
        RevisionField.STATUS,
        RevisionField.REPORTED_CAUSE,
        RevisionField.EVIDENCE_CHECKSUM,
        RevisionField.MATERIAL_REASON,
    )
    capacity_change = next(
        change
        for change in delta.changes
        if change.field == RevisionField.UNAVAILABLE_MW
    )
    assert capacity_change.before == 400
    assert capacity_change.after == 700
    status_change = next(
        change for change in delta.changes if change.field == RevisionField.STATUS
    )
    assert status_change.before == EventStatus.OPEN
    assert status_change.after == EventStatus.UPDATED
    assert first.unavailable_mw == 400
    with pytest.raises(ValidationError):
        first.unavailable_mw = 999  # type: ignore[misc]


def test_append_revision_returns_new_immutable_lifecycle_and_delta() -> None:
    first = revision("evt-lifecycle")
    second = revision(
        "evt-lifecycle",
        revision_number=2,
        unavailable_mw=750,
        material_reason="Reported unavailable capacity increased",
    )
    original = EventLifecycleHistory(event_id=first.event_id, revisions=(first,))

    result = append_revision(original, second)

    assert original.current.revision == 1
    assert result.lifecycle.current == second
    assert len(result.lifecycle.revisions) == 2
    assert result.delta.changed_fields == (
        RevisionField.UNAVAILABLE_MW,
        RevisionField.STATUS,
        RevisionField.EVIDENCE_CHECKSUM,
        RevisionField.MATERIAL_REASON,
    )
    assert isinstance(result.lifecycle.revisions, tuple)


@pytest.mark.parametrize(
    ("status", "superseded_by"),
    [
        (EventStatus.RESOLVED, None),
        (EventStatus.WITHDRAWN, None),
        (EventStatus.SUPERSEDED, "evt-replacement"),
    ],
)
def test_open_event_can_enter_each_terminal_state(
    status: EventStatus,
    superseded_by: str | None,
) -> None:
    first = revision("evt-terminal")
    terminal = revision(
        "evt-terminal",
        revision_number=2,
        status=status,
        material_reason=f"Source marked the notice {status.value}",
        superseded_by_event_id=superseded_by,
    )

    delta = diff_revisions(first, terminal)

    assert RevisionField.STATUS in delta.changed_fields
    assert terminal.status == status


def test_terminal_state_cannot_be_revised() -> None:
    first = revision("evt-closed")
    resolved = revision(
        "evt-closed",
        revision_number=2,
        status=EventStatus.RESOLVED,
        material_reason="Source resolved the notice",
    )
    later = revision(
        "evt-closed",
        revision_number=3,
        status=EventStatus.UPDATED,
        material_reason="Attempted later update",
    )
    diff_revisions(first, resolved)

    with pytest.raises(ValueError, match="terminal event states"):
        diff_revisions(resolved, later)


def test_revision_chain_requires_same_event_sequence_and_forward_time() -> None:
    first = revision("evt-a")
    non_sequential = revision("evt-a", revision_number=3)
    another_event = revision("evt-b", revision_number=2)
    backwards = revision(
        "evt-a",
        revision_number=2,
        published_at=first.published_at - timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="sequential"):
        diff_revisions(first, non_sequential)
    with pytest.raises(ValueError, match="same event"):
        diff_revisions(first, another_event)
    with pytest.raises(ValueError, match="cannot move backwards"):
        diff_revisions(first, backwards)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"revision_number": 1, "status": EventStatus.RESOLVED},
        {
            "revision_number": 2,
            "status": EventStatus.OPEN,
            "material_reason": "Invalid reopen",
        },
        {
            "revision_number": 2,
            "status": EventStatus.SUPERSEDED,
            "superseded_by_event_id": None,
        },
        {"asset_id": None, "reliable_asset": True},
        {
            "effective_start": NOW,
            "effective_end": NOW - timedelta(minutes=1),
        },
        {"published_at": NOW.replace(tzinfo=None)},
    ],
)
def test_revision_model_rejects_invalid_states(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        revision("evt-invalid", **kwargs)


def test_strong_current_system_warning_has_bounded_auditable_score() -> None:
    event = revision(
        "warning-1",
        authority=EventAuthority.SYSTEM_WARNING,
        published_at=NOW - timedelta(hours=1),
        effective_start=NOW - timedelta(hours=1),
        effective_end=NOW + timedelta(days=1),
        unavailable_mw=1_200,
        normal_capacity_mw=1_200,
        planned=False,
    )

    result = score_event(event, as_of=NOW)

    assert result.eligible is True
    assert result.unavailable_capacity_percent == 100
    assert result.components.authority == 20
    assert result.components.system_warning == 10
    assert result.components.unavailable_mw == 15
    assert result.components.unavailable_capacity_percent == 15
    assert result.components.timing == 15
    assert result.components.duration == 5
    assert result.components.reported_planning_state == 5
    assert result.components.novelty == 5
    assert result.components.material_revision == 0
    assert result.total_score == 90
    assert result.total_score <= RelevanceMethodology().maximum_score


def test_material_updated_revision_scores_revision_without_novelty() -> None:
    event = revision(
        "updated-1",
        revision_number=2,
        status=EventStatus.UPDATED,
        published_at=NOW - timedelta(minutes=5),
        material_reason="Reported capacity changed materially",
    )

    result = score_event(event, as_of=NOW)

    assert result.components.novelty == 0
    assert result.components.material_revision == 10


def test_missing_capacity_and_planning_state_remain_unknown_and_score_zero() -> None:
    event = revision(
        "missing-fields",
        authority=EventAuthority.OTHER_REPORTED,
        effective_start=None,
        effective_end=None,
        unavailable_mw=None,
        normal_capacity_mw=None,
        planned=None,
        reported_cause=None,
        published_at=NOW - timedelta(days=2),
    )

    result = score_event(event, as_of=NOW)
    payload = result.model_dump(mode="json")

    assert result.unavailable_capacity_percent is None
    assert result.event.unavailable_mw is None
    assert result.event.normal_capacity_mw is None
    assert result.event.planned is None
    assert result.event.reported_cause is None
    assert result.components.unavailable_mw == 0
    assert result.components.unavailable_capacity_percent == 0
    assert result.components.reported_planning_state == 0
    assert result.components.timing == 0
    assert "inferred_cause" not in payload["event"]


def test_reported_capacity_percentage_is_not_clamped_but_score_is_bounded() -> None:
    result = score_event(
        revision(
            "over-capacity",
            unavailable_mw=1_500,
            normal_capacity_mw=1_000,
        ),
        as_of=NOW,
    )

    assert result.unavailable_capacity_percent == 150
    assert result.components.unavailable_capacity_percent == 15
    assert result.total_score <= 100


@pytest.mark.parametrize(
    ("planned", "expected"),
    [(False, 5), (True, 2), (None, 0)],
)
def test_planning_score_uses_only_explicit_reported_state(
    planned: bool | None,
    expected: int,
) -> None:
    result = score_event(revision(f"planned-{planned}", planned=planned), as_of=NOW)
    assert result.components.reported_planning_state == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (NOW - timedelta(hours=1), NOW + timedelta(hours=1), 15),
        (NOW + timedelta(hours=2), NOW + timedelta(hours=3), 12),
        (NOW + timedelta(hours=12), NOW + timedelta(hours=13), 8),
        (NOW + timedelta(days=2), NOW + timedelta(days=3), 3),
        (NOW - timedelta(hours=2), NOW - timedelta(hours=1), 0),
        (None, None, 0),
    ],
)
def test_timing_score_is_deterministic_and_missing_safe(
    start: datetime | None,
    end: datetime | None,
    expected: int,
) -> None:
    result = score_event(
        revision("timing", effective_start=start, effective_end=end),
        as_of=NOW,
    )
    assert result.components.timing == expected


@pytest.mark.parametrize(
    "status",
    [EventStatus.RESOLVED, EventStatus.WITHDRAWN, EventStatus.SUPERSEDED],
)
def test_terminal_events_are_ineligible_with_zero_score(status: EventStatus) -> None:
    event = revision(
        f"terminal-{status.value}",
        revision_number=2,
        status=status,
        authority=EventAuthority.SYSTEM_WARNING,
        unavailable_mw=2_000,
        normal_capacity_mw=2_000,
        material_reason=f"Source marked event {status.value}",
        superseded_by_event_id=("replacement" if status == EventStatus.SUPERSEDED else None),
    )

    result = score_event(event, as_of=NOW)

    assert result.eligible is False
    assert result.total_score == 0
    assert result.components.total == 0
    assert result.exclusion_reason == f"terminal_status:{status.value}"


def test_open_notice_with_an_ended_reported_window_is_not_relevant() -> None:
    event = revision(
        "ended-open-notice",
        effective_start=NOW - timedelta(hours=2),
        effective_end=NOW - timedelta(hours=1),
    )

    result = score_event(event, as_of=NOW)

    assert result.eligible is False
    assert result.total_score == 0
    assert result.exclusion_reason == "reported_window_ended"


def test_resolved_withdrawn_and_superseded_events_are_not_ranked() -> None:
    open_event = revision("open-event", unavailable_mw=100)
    terminal = [
        revision(
            f"closed-{status.value}",
            revision_number=2,
            status=status,
            material_reason=f"Source marked event {status.value}",
            superseded_by_event_id=("new-event" if status == EventStatus.SUPERSEDED else None),
        )
        for status in (
            EventStatus.RESOLVED,
            EventStatus.WITHDRAWN,
            EventStatus.SUPERSEDED,
        )
    ]

    result = rank_relevant_events([open_event, *terminal], as_of=NOW)

    assert result.eligible_group_count == 1
    assert len(result.selected) == 1
    assert result.selected[0].representative.event.event_id == "open-event"


def test_reliable_asset_identity_groups_duplicate_notices_without_summing() -> None:
    lower = revision(
        "notice-a",
        asset_id=" UNIT-1 ",
        asset_name="Unit One",
        reliable_asset=True,
        unavailable_mw=400,
    )
    higher = revision(
        "notice-b",
        asset_id="unit-1",
        asset_name="Unit One",
        reliable_asset=True,
        unavailable_mw=1_000,
    )

    result = rank_relevant_events([lower, higher, lower], as_of=NOW)

    assert result.eligible_group_count == 1
    assert len(result.selected) == 1
    group = result.selected[0]
    assert group.group_key == "asset:unit-1"
    assert group.reliable_asset_id == "unit-1"
    assert group.member_event_ids == ("notice-a", "notice-b")
    assert group.representative.event.event_id == "notice-b"
    assert group.representative.event.unavailable_mw == 1_000


def test_unreliable_asset_ids_never_group() -> None:
    first = revision("notice-a", asset_id="unit-1", reliable_asset=False)
    second = revision("notice-b", asset_id="unit-1", reliable_asset=False)

    result = rank_relevant_events([first, second], as_of=NOW)

    assert result.eligible_group_count == 2
    assert {group.group_key for group in result.selected} == {
        "event:notice-a",
        "event:notice-b",
    }


def test_reliable_asset_id_does_not_merge_separate_reported_windows() -> None:
    first = revision(
        "notice-a",
        asset_id="unit-1",
        reliable_asset=True,
        effective_start=NOW,
        effective_end=NOW + timedelta(hours=1),
    )
    second = revision(
        "notice-b",
        asset_id="unit-1",
        reliable_asset=True,
        effective_start=NOW + timedelta(hours=2),
        effective_end=NOW + timedelta(hours=3),
    )

    result = rank_relevant_events([first, second], as_of=NOW)

    assert result.eligible_group_count == 2
    assert {group.reliable_asset_id for group in result.selected} == {"unit-1"}
    assert {group.member_event_ids for group in result.selected} == {
        ("notice-a",),
        ("notice-b",),
    }


def test_latest_revision_per_event_is_ranked_once() -> None:
    first = revision("revised-event", unavailable_mw=100)
    second = revision(
        "revised-event",
        revision_number=2,
        unavailable_mw=1_000,
        material_reason="Unavailable capacity increased",
    )

    result = rank_relevant_events([first, second], as_of=NOW)

    assert result.eligible_group_count == 1
    assert result.selected[0].member_event_ids == ("revised-event",)
    assert result.selected[0].representative.event.revision == 2
    assert result.selected[0].representative.event.unavailable_mw == 1_000


def test_conflicting_duplicate_revision_is_rejected_not_arbitrarily_selected() -> None:
    first = revision("conflict", evidence_checksum="checksum-a")
    conflict = revision("conflict", evidence_checksum="checksum-b")

    with pytest.raises(ValueError, match="conflicting duplicate"):
        rank_relevant_events([first, conflict], as_of=NOW)


def test_event_heavy_selection_is_bounded_to_three() -> None:
    events = [
        revision(
            f"event-{index:02d}",
            unavailable_mw=float((index + 1) * 100),
            normal_capacity_mw=1_000,
        )
        for index in range(10)
    ]

    result = rank_relevant_events(events, as_of=NOW)

    assert result.requested_limit == 3
    assert result.eligible_group_count == 10
    assert len(result.selected) == MAX_SELECTED_EVENTS
    assert result.selected[0].representative.event.event_id == "event-09"


def test_equal_scores_use_event_id_as_final_stable_tie_breaker() -> None:
    event_b = revision("event-b", evidence_checksum="b")
    event_a = revision("event-a", evidence_checksum="a")

    forward = rank_relevant_events([event_b, event_a], as_of=NOW)
    reverse = rank_relevant_events([event_a, event_b], as_of=NOW)

    assert [group.representative.event.event_id for group in forward.selected] == [
        "event-a",
        "event-b",
    ]
    assert reverse.selected == forward.selected


@pytest.mark.parametrize("limit", [0, 4])
def test_selection_limit_cannot_exceed_product_bound(limit: int) -> None:
    with pytest.raises(ValueError, match="between one and three"):
        rank_relevant_events([revision("event")], as_of=NOW, limit=limit)


def test_selection_limit_rejects_bool_and_non_integer() -> None:
    with pytest.raises(TypeError, match="integer"):
        rank_relevant_events([revision("event")], as_of=NOW, limit=True)
    with pytest.raises(TypeError, match="integer"):
        rank_relevant_events([revision("event")], as_of=NOW, limit=2.5)  # type: ignore[arg-type]


def test_score_and_rank_require_timezone_aware_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        score_event(revision("event"), as_of=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timezone-aware"):
        rank_relevant_events(
            [revision("event")],
            as_of=NOW.replace(tzinfo=None),
        )


def test_scoring_methodology_is_versioned_and_forbids_causal_inference() -> None:
    method = RelevanceMethodology()
    result = rank_relevant_events([revision("event")], as_of=NOW)

    assert method.version == "50hz.event-relevance.v1"
    assert method.maximum_score == 100
    assert method.maximum_selected_events == 3
    assert "never infer" in method.causation_rule.lower()
    assert result.methodology == method
    assert tuple(method.stable_tie_breakers)[-1] == "event ID ascending"
