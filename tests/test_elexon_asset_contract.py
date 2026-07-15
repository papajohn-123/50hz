from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.assets import (
    AssetSchemaError,
    EvidenceKind,
    PowerDirection,
    consolidate_physical_notifications,
    parse_b1610_metered_energy,
    parse_bm_unit_references,
    parse_physical_notifications,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "elexon_assets"
RETRIEVED_AT = datetime(2024, 7, 7, 12, 0, tzinfo=UTC)


def fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def test_bm_unit_reference_preserves_nullable_geography_and_signed_capacities() -> None:
    batch = parse_bm_unit_references(
        fixture("bm_units.json"),
        retrieved_at=datetime(2024, 7, 7, 13, 0, tzinfo=timezone(timedelta(hours=1))),
    )

    assert len(batch.records) == 2
    wind = next(record for record in batch.records if record.asset_id == "ABRBO-1")
    assert wind.source_asset_id == "T_ABRBO-1"
    assert wind.generation_capacity_mw == 99
    assert wind.demand_capacity_mw == -2
    assert wind.location is None
    assert wind.gsp_group_name is None
    assert wind.provenance.evidence_kind is EvidenceKind.REFERENCE
    assert wind.provenance.retrieved_at == datetime(2024, 7, 7, 12, 0, tzinfo=UTC)
    assert "reference" in wind.provenance.dataset.casefold()
    assert any("ignored 1 invalid" in warning for warning in batch.warnings)


def test_physical_notification_is_consolidated_and_linearly_interpolated() -> None:
    batch = parse_physical_notifications(
        fixture("physical_notifications_bst.json"),
        retrieved_at=RETRIEVED_AT,
    )
    profiles = consolidate_physical_notifications(batch.records)

    assert len(batch.records) == 4
    assert len(profiles) == 2
    assert any("ignored 1 invalid PN row" in warning for warning in batch.warnings)

    generation = next(profile for profile in profiles if profile.asset_id == "TEST-1")
    assert len(generation.segments) == 2  # the exact duplicate was collapsed
    halfway_first_segment = generation.level_at(
        datetime(2024, 7, 1, 8, 37, 30, tzinfo=UTC)
    )
    assert halfway_first_segment is not None
    assert halfway_first_segment.level_mw == pytest.approx(10)
    assert halfway_first_segment.direction is PowerDirection.EXPORT
    assert halfway_first_segment.provenance.evidence_kind is EvidenceKind.REPORTED_PLAN

    # A touching boundary belongs to the following segment, with no ambiguity.
    boundary = generation.level_at(datetime(2024, 7, 1, 8, 45, tzinfo=UTC))
    assert boundary is not None
    assert boundary.level_mw == 40
    assert generation.level_at(datetime(2024, 7, 1, 9, 0, tzinfo=UTC)) is None

    demand = next(profile for profile in profiles if profile.asset_id == "DEMAND-1")
    planned_import = demand.level_at(datetime(2024, 7, 1, 8, 45, tzinfo=UTC))
    assert planned_import is not None
    assert planned_import.level_mw == -40
    assert planned_import.direction is PowerDirection.IMPORT


def test_physical_notification_uses_bst_settlement_period_in_utc() -> None:
    batch = parse_physical_notifications(
        fixture("physical_notifications_bst.json"),
        retrieved_at=RETRIEVED_AT,
    )
    first = batch.records[0]

    # Period 20 is 09:30-10:00 BST, which is 08:30-09:00 UTC.
    assert first.start == datetime(2024, 7, 1, 8, 30, tzinfo=UTC)
    assert first.end == datetime(2024, 7, 1, 8, 45, tzinfo=UTC)


def test_physical_notification_does_not_extrapolate_across_a_gap() -> None:
    batch = parse_physical_notifications(
        {
            "data": [
                {
                    "dataset": "PN",
                    "settlementDate": "2024-07-01",
                    "settlementPeriod": 20,
                    "timeFrom": "2024-07-01T08:30:00Z",
                    "timeTo": "2024-07-01T08:40:00Z",
                    "levelFrom": 10,
                    "levelTo": 20,
                    "nationalGridBmUnit": "TEST-1",
                    "bmUnit": "T_TEST-1",
                },
                {
                    "dataset": "PN",
                    "settlementDate": "2024-07-01",
                    "settlementPeriod": 20,
                    "timeFrom": "2024-07-01T08:50:00Z",
                    "timeTo": "2024-07-01T09:00:00Z",
                    "levelFrom": 30,
                    "levelTo": 40,
                    "nationalGridBmUnit": "TEST-1",
                    "bmUnit": "T_TEST-1",
                },
            ]
        },
        retrieved_at=RETRIEVED_AT,
    )
    profile = consolidate_physical_notifications(batch.records)[0]

    assert profile.level_at(datetime(2024, 7, 1, 8, 45, tzinfo=UTC)) is None


def test_consolidation_uses_latest_snapshot_and_rejects_conflicting_overlap() -> None:
    batch = parse_physical_notifications(
        fixture("physical_notifications_bst.json"),
        retrieved_at=RETRIEVED_AT,
    )
    first = batch.records[0]
    newer = replace(
        first,
        level_from_mw=100,
        level_to_mw=200,
        provenance=replace(
            first.provenance,
            retrieved_at=RETRIEVED_AT + timedelta(minutes=1),
        ),
    )
    profile = consolidate_physical_notifications((first, newer))[0]
    assert profile.segments == (newer,)

    overlapping = replace(
        first,
        start=datetime(2024, 7, 1, 8, 35, tzinfo=UTC),
        end=datetime(2024, 7, 1, 8, 50, tzinfo=UTC),
    )
    with pytest.raises(AssetSchemaError, match="conflicting PN segments overlap"):
        consolidate_physical_notifications((first, overlapping))


def test_b1610_converts_half_hour_energy_to_average_power_without_losing_sign() -> None:
    batch = parse_b1610_metered_energy(
        fixture("b1610_bst.json"),
        retrieved_at=RETRIEVED_AT,
    )

    assert len(batch.records) == 2
    assert any("ignored 1 invalid B1610 row" in warning for warning in batch.warnings)
    consumed = next(record for record in batch.records if record.asset_id == "ABRBO-1")
    generated = next(record for record in batch.records if record.asset_id == "TEST-1")
    assert consumed.energy_mwh == -0.381
    assert consumed.average_mw == pytest.approx(-0.762)
    assert consumed.direction is PowerDirection.IMPORT
    assert generated.average_mw == pytest.approx(232.218)
    assert generated.direction is PowerDirection.EXPORT
    assert generated.provenance.evidence_kind is EvidenceKind.SETTLED_METERED
    assert generated.provenance.published_at is None
    assert generated.age_at_retrieval > timedelta(days=5)


def test_b1610_derives_fall_back_period_50_without_local_time_ambiguity() -> None:
    batch = parse_b1610_metered_energy(
        [
            {
                "dataset": "B1610",
                "psrType": "Generation",
                "bmUnit": "T_TEST-1",
                "nationalGridBmUnitId": "TEST-1",
                "settlementDate": "2026-10-25",
                "settlementPeriod": 50,
                "halfHourEndTime": "2026-10-26T00:00:00Z",
                "quantity": 25,
            }
        ],
        retrieved_at=datetime(2026, 11, 1, 0, 0, tzinfo=UTC),
    )
    record = batch.records[0]

    assert record.interval_start == datetime(2026, 10, 25, 23, 30, tzinfo=UTC)
    assert record.interval_end == datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
    assert record.average_mw == 50


@pytest.mark.parametrize(
    ("parser", "payload"),
    [
        (parse_bm_unit_references, [{"elexonBmUnit": "missing-national-id"}]),
        (
            parse_physical_notifications,
            {"data": [{"dataset": "PN", "settlementDate": "not-a-date"}]},
        ),
        (
            parse_b1610_metered_energy,
            [{"dataset": "B1610", "quantity": "NaN"}],
        ),
    ],
)
def test_parsers_fail_closed_when_every_row_is_malformed(parser, payload) -> None:
    with pytest.raises(AssetSchemaError, match="no valid"):
        parser(payload, retrieved_at=RETRIEVED_AT)


def test_parsers_reject_naive_retrieval_time() -> None:
    with pytest.raises(ValueError, match="retrieved_at must be timezone-aware"):
        parse_bm_unit_references(
            fixture("bm_units.json"),
            retrieved_at=datetime(2024, 7, 7, 12, 0),
        )
