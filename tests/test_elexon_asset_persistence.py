from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.dialects import postgresql

from app.assets import parse_b1610_metered_energy, parse_bm_unit_references
from app.assets.elexon import parse_physical_notifications
from app.db import B1610SettledEnergyRevision
from app.persistence.ingestion import (
    _B1610_SETTLED_ENERGY_SPEC,
    _PHYSICAL_NOTIFICATION_SPEC,
    _ensure_bm_unit_assets,
    _merge_asset_reference_rows,
    _observation_upsert,
    _persist_asset_reference_snapshot,
    _prepare_immutable_revisions,
    _prune_physical_notification_scope,
)
from app.persistence.records import (
    BM_UNIT_REFERENCE_SOURCE_ID,
    PUBLIC_SOURCE_IDS,
    bm_unit_reference_source_metadata_values,
    map_asset_reference,
    map_b1610_settled_energy,
    map_physical_notification_segment,
    source_metadata_values,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "elexon_assets"
NOW = datetime(2024, 7, 7, 12, 0, tzinfo=UTC)
RAW_1 = UUID("0161d868-9d49-4a60-b7d2-b37839720a04")
RAW_2 = UUID("e36af09d-7f81-4cb5-abdb-e9ba8ce2ca71")
ASSET_ID = UUID("de76e194-3dee-50cb-aa5f-f9c329fd3c77")


def fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def reference_record():
    return parse_bm_unit_references(
        fixture("bm_units.json"),
        retrieved_at=NOW,
    ).records[0]


def pn_record():
    return parse_physical_notifications(
        fixture("physical_notifications_bst.json"),
        retrieved_at=NOW,
    ).records[0]


def b1610_record():
    return parse_b1610_metered_energy(
        fixture("b1610_bst.json"),
        retrieved_at=NOW,
    ).records[0]


def test_source_metadata_exposes_all_three_elexon_asset_families_with_slow_cadence() -> None:
    assert {
        "elexon.bm-unit-reference",
        "elexon.pn",
        "elexon.b1610",
    }.issubset(PUBLIC_SOURCE_IDS)
    reference = bm_unit_reference_source_metadata_values()
    pn = source_metadata_values(
        provider="elexon.pn",
        dataset="PN",
        request_url="https://data.elexon.co.uk/bmrs/api/v1/datasets/PN",
    )
    b1610 = source_metadata_values(
        provider="elexon.b1610",
        dataset="B1610",
        request_url="https://data.elexon.co.uk/bmrs/api/v1/datasets/B1610/stream",
    )

    assert reference["id"] == BM_UNIT_REFERENCE_SOURCE_ID
    assert reference["expected_cadence_seconds"] == 86_400
    assert pn["id"] == "elexon.pn"
    assert pn["expected_cadence_seconds"] == 600
    assert b1610["id"] == "elexon.b1610"
    assert b1610["expected_cadence_seconds"] == 86_400
    assert all(item["active"] is True for item in (reference, pn, b1610))


def test_reference_mapping_preserves_fields_but_never_fabricates_coordinates() -> None:
    record = reference_record()
    values = map_asset_reference(record, source_id=BM_UNIT_REFERENCE_SOURCE_ID)

    assert values["external_id"] == "ABRBO-1"
    assert values["asset_type"] == "bm_unit"
    assert values["fuel_type"] == "wind"
    assert values["capacity_mw"] == 99
    assert values["latitude"] is None
    assert values["longitude"] is None
    assert values["map_x"] is None
    assert values["map_y"] is None
    assert values["attributes"]["elexonBmUnit"] == "T_ABRBO-1"
    assert values["attributes"]["demandCapacityMW"] == -2
    assert values["attributes"]["transmissionLossFactor"] == -0.0200357
    assert values["attributes"]["workingDayCreditAssessmentExportCapabilityMW"] == 39.6
    assert values["attributes"]["creditQualifyingStatus"] is True
    assert values["attributes"]["locationStatus"] == "not_provided_by_elexon"
    assert values["attributes"]["provenance"]["retrievedAt"] == NOW.isoformat()


def test_duplicate_national_id_preserves_each_official_reference_variant() -> None:
    first = map_asset_reference(reference_record(), source_id=BM_UNIT_REFERENCE_SOURCE_ID)
    second = {
        **first,
        "attributes": {**first["attributes"], "eic": "48WSECOND-EIC"},
    }

    rows, duplicate_count = _merge_asset_reference_rows([first, second])

    assert duplicate_count == 1
    assert len(rows) == 1
    assert [variant["eic"] for variant in rows[0]["attributes"]["referenceVariants"]] == [
        "48W00000ABRBO-19",
        "48WSECOND-EIC",
    ]


def test_pn_and_b1610_mappings_keep_semantics_sign_and_asset_fk() -> None:
    pn = map_physical_notification_segment(
        pn_record(),
        source_id="elexon.pn",
        raw_payload_id=RAW_1,
        asset_id=ASSET_ID,
    )
    metered = map_b1610_settled_energy(
        b1610_record(),
        source_id="elexon.b1610",
        raw_payload_id=RAW_1,
        asset_id=ASSET_ID,
    )

    assert pn["classification"] == "reported_plan"
    assert pn["asset_id"] == ASSET_ID
    assert pn["level_from_mw"] == -20
    assert pn["attributes"]["isActualOutput"] is False
    assert metered["classification"] == "settled_metered"
    assert metered["energy_mwh"] == -0.381
    assert metered["average_mw"] == -0.762
    assert metered["attributes"]["isInstantaneousPower"] is False


def test_unchanged_pn_retrieval_does_not_rewrite_the_current_plan() -> None:
    values = map_physical_notification_segment(
        pn_record(),
        source_id="elexon.pn",
        raw_payload_id=RAW_1,
        asset_id=ASSET_ID,
    )
    statement = _observation_upsert(_PHYSICAL_NOTIFICATION_SPEC, [values])
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()
    where = sql.split(" where ", 1)[1]

    assert "level_from_mw is distinct from" in where
    assert "level_to_mw is distinct from" in where
    assert "retrieved_at is distinct from" not in where
    assert "raw_payload_id is distinct from" not in where


def test_b1610_repoll_ignores_delivery_changes_but_appends_source_correction() -> None:
    original_record = b1610_record()
    original_values = map_b1610_settled_energy(
        original_record,
        source_id="elexon.b1610",
        raw_payload_id=RAW_1,
        asset_id=ASSET_ID,
    )
    original = B1610SettledEnergyRevision(**original_values)
    later_delivery = replace(
        original_record,
        provenance=replace(
            original_record.provenance,
            retrieved_at=NOW + timedelta(days=1),
        ),
    )
    repoll = map_b1610_settled_energy(
        later_delivery,
        source_id="elexon.b1610",
        raw_payload_id=RAW_2,
        asset_id=ASSET_ID,
    )
    identity = (
        original.source_id,
        original.asset_id,
        original.settlement_date,
        original.settlement_period,
    )

    prepared, inserted, corrected, unchanged = _prepare_immutable_revisions(
        _B1610_SETTLED_ENERGY_SPEC,
        [repoll],
        {identity: original},
    )
    assert prepared == []
    assert (inserted, corrected, unchanged) == (0, 0, 1)

    corrected_record = replace(later_delivery, energy_mwh=-1.0)
    correction = map_b1610_settled_energy(
        corrected_record,
        source_id="elexon.b1610",
        raw_payload_id=RAW_2,
        asset_id=ASSET_ID,
    )
    prepared, inserted, corrected, unchanged = _prepare_immutable_revisions(
        _B1610_SETTLED_ENERGY_SPEC,
        [correction],
        {identity: original},
    )
    assert (inserted, corrected, unchanged) == (0, 1, 0)
    assert prepared[0]["revision"] == 1
    assert prepared[0]["energy_mwh"] == -1
    assert prepared[0]["average_mw"] == -2


class _Scalars:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return self._values


class _Result:
    def __init__(self, values: list[Any] | None = None, rows: list[Any] | None = None):
        self._values = values or []
        self._rows = rows or []

    def scalars(self) -> _Scalars:
        return _Scalars(self._values)

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return self.results.pop(0)


def test_reference_snapshot_upsert_is_deterministic_and_deactivates_missing_assets() -> None:
    session = _Session([_Result([True]), _Result([UUID(int=5)])])

    outcome = asyncio.run(
        _persist_asset_reference_snapshot(
            session,
            source_id=BM_UNIT_REFERENCE_SOURCE_ID,
            records=(reference_record(),),
        )
    )

    assert outcome == (1, 1, 0)
    insert = session.statements[0]
    params = insert.compile(dialect=postgresql.dialect()).params
    assert any(value == "bm_unit" for value in params.values())
    assert not any(
        value is not None
        for key, value in params.items()
        if "latitude" in key or "longitude" in key or "map_x" in key or "map_y" in key
    )
    deactivation = session.statements[1].compile(dialect=postgresql.dialect())
    deactivation_sql = str(deactivation).lower()
    assert "update assets" in deactivation_sql
    assert "not in" in deactivation_sql
    assert "assets.attributes" in deactivation_sql
    assert "as varchar" in deactivation_sql
    assert "classification" in deactivation.params.values()
    assert "reference" in deactivation.params.values()


def test_dependent_data_creates_null_location_placeholder_before_fk_resolution() -> None:
    session = _Session(
        [
            _Result(),
            _Result(),
            _Result(rows=[("TEST-1", ASSET_ID)]),
        ]
    )
    record = replace(pn_record(), asset_id="TEST-1", source_asset_id="T_TEST-1")

    resolved = asyncio.run(_ensure_bm_unit_assets(session, (record,)))

    assert resolved == {"TEST-1": ASSET_ID}
    placeholder = session.statements[1]
    params = placeholder.compile(dialect=postgresql.dialect()).params
    assert any(
        isinstance(value, dict)
        and value.get("classification") == "reference_placeholder"
        for value in params.values()
    )
    coordinate_values = [
        value
        for key, value in params.items()
        if "latitude" in key or "longitude" in key or "map_x" in key or "map_y" in key
    ]
    assert coordinate_values and all(value is None for value in coordinate_values)


def test_pn_without_elexon_unit_id_maps_and_uses_national_id_placeholder_name() -> None:
    record = replace(
        pn_record(),
        asset_id="AG-GBL01H",
        source_asset_id=None,
    )
    values = map_physical_notification_segment(
        record,
        source_id="elexon.pn",
        raw_payload_id=RAW_1,
        asset_id=ASSET_ID,
    )
    session = _Session(
        [
            _Result(),
            _Result(),
            _Result(rows=[("AG-GBL01H", ASSET_ID)]),
        ]
    )

    resolved = asyncio.run(_ensure_bm_unit_assets(session, (record,)))

    assert values["elexon_bm_unit"] is None
    assert values["national_grid_bm_unit"] == "AG-GBL01H"
    assert resolved == {"AG-GBL01H": ASSET_ID}
    params = session.statements[1].compile(dialect=postgresql.dialect()).params
    assert "AG-GBL01H" in params.values()
    assert any(
        isinstance(value, dict)
        and value.get("elexonBmUnit") is None
        and value.get("nationalGridBmUnit") == "AG-GBL01H"
        for value in params.values()
    )


def test_b1610_elexon_only_unit_maps_to_a_truthful_non_geographic_asset() -> None:
    record = replace(
        b1610_record(),
        asset_id="elexon:2__AALAB000",
        source_asset_id="2__AALAB000",
        national_grid_bm_unit=None,
    )
    values = map_b1610_settled_energy(
        record,
        source_id="elexon.b1610",
        raw_payload_id=RAW_1,
        asset_id=ASSET_ID,
    )
    session = _Session(
        [
            _Result(),
            _Result(),
            _Result(rows=[("elexon:2__AALAB000", ASSET_ID)]),
        ]
    )

    resolved = asyncio.run(_ensure_bm_unit_assets(session, (record,)))

    assert values["national_grid_bm_unit"] is None
    assert values["elexon_bm_unit"] == "2__AALAB000"
    assert values["attributes"]["assetExternalID"] == "elexon:2__AALAB000"
    assert resolved == {"elexon:2__AALAB000": ASSET_ID}
    params = session.statements[1].compile(dialect=postgresql.dialect()).params
    assert any(
        isinstance(value, dict)
        and value.get("nationalGridBmUnit") is None
        and value.get("elexonBmUnit") == "2__AALAB000"
        for value in params.values()
    )


def test_pn_scope_pruning_is_atomic_and_never_deletes_outside_declared_units() -> None:
    row = map_physical_notification_segment(
        pn_record(),
        source_id="elexon.pn",
        raw_payload_id=RAW_1,
        asset_id=ASSET_ID,
    )
    session = _Session([_Result(), _Result()])

    asyncio.run(
        _prune_physical_notification_scope(
            session,
            source_id="elexon.pn",
            metadata={
                "settlementDate": "2024-07-01",
                "settlementPeriod": 20,
                "bmUnits": ["T_TEST-1"],
                "allUnits": False,
            },
            rows=[row],
        )
    )

    stale_compiled = session.statements[0].compile(dialect=postgresql.dialect())
    stale_sql = str(stale_compiled).lower()
    assert "delete from physical_notification_segments_current" in stale_sql
    assert "settlement_date !=" in stale_sql
    assert ["T_TEST-1"] in stale_compiled.params.values()

    compiled = session.statements[1].compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "delete from physical_notification_segments_current" in sql
    assert "elexon_bm_unit in" in sql
    assert "not in" in sql
    assert ["T_TEST-1"] in compiled.params.values()
