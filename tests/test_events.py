from datetime import UTC, datetime

from app.events.lifecycle import InMemoryEventLifecycle
from app.events.rules import generation_leader_change, interconnector_reversal, reported_unavailability


NOW = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)


def test_generation_leader_is_deterministic_and_deduplicated() -> None:
    candidate = generation_leader_change(
        {"wind": 8_000, "gas": 9_000},
        {"wind": 10_000, "gas": 9_000},
        NOW,
        ["fuelinst:1"],
    )
    assert candidate is not None
    lifecycle = InMemoryEventLifecycle()
    first = lifecycle.apply(candidate, NOW)
    repeated = lifecycle.apply(candidate, NOW)
    assert first.event_id == repeated.event_id
    assert repeated.revision == 1


def test_interconnector_reversal_requires_persistence() -> None:
    assert interconnector_reversal(-500, 600, NOW, ["flow:1"], sustained_samples=1) is None
    event = interconnector_reversal(-500, 600, NOW, ["flow:1", "flow:2"], sustained_samples=2)
    assert event is not None
    assert event.facts[0].value == "importing"


def test_outage_can_only_claim_supplied_reported_cause() -> None:
    event = reported_unavailability(
        asset_id="T_SIZEW-1",
        asset_name="Sizewell B",
        unavailable_mw=610,
        planned=False,
        occurred_at=NOW,
        source_record_ids=["remit:123:2"],
        reported_cause=None,
    )
    assert event.cause_reported is False
    assert all(fact.metric != "reported_cause" for fact in event.facts)

