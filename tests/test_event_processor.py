from datetime import UTC, datetime

from app.events.processor import EventProcessor, GridObservationWindow


def test_processor_emits_only_rules_supported_by_window() -> None:
    candidates = EventProcessor().evaluate(
        GridObservationWindow(
            observed_at=datetime(2026, 7, 11, 12, tzinfo=UTC),
            previous_generation_mw={"gas": 11_000, "wind": 9_000},
            current_generation_mw={"gas": 8_000, "wind": 13_000, "solar": 2_000},
            previous_net_import_mw=-500,
            current_net_import_mw=700,
            net_flow_sustained_samples=2,
            frequency_hz=50.0,
            generation_source_record_ids=["fuel:1"],
            interconnector_source_record_ids=["int:1", "int:2"],
            frequency_source_record_ids=["freq:1"],
        )
    )
    types = {candidate.event_type for candidate in candidates}
    assert "generation_leader_change" in types
    assert "renewable_share_milestone" in types
    assert "energy_position_reversal" in types
    assert "frequency_excursion" not in types
