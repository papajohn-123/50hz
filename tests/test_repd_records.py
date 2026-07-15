from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.db.models import Asset, SourceMetadata
from app.geography.records import (
    REPD_ASSET_TYPE,
    REPD_EXPECTED_CADENCE_SECONDS,
    REPD_SOURCE_ID,
    map_repd_site,
    normalized_repd_fuel,
    public_repd_site_id,
    repd_snapshot_membership,
    repd_source_metadata_values,
)
from app.geography.repd import (
    OSGB36_BNG_CRS,
    REPD_DATASET_NAME,
    REPD_LICENCE_NAME,
    REPD_LICENCE_URL,
    REPD_PUBLISHER,
    WGS84_CRS,
    REPDCoordinates,
    REPDProvenance,
    REPDSite,
    REPDStatus,
)


RETRIEVED = datetime(
    2026,
    5,
    6,
    13,
    30,
    tzinfo=timezone(timedelta(hours=1)),
)
SOURCE_URL = (
    "https://assets.publishing.service.gov.uk/media/example/"
    "REPD_publication_Q1_2026.csv"
)


def _site(
    *,
    source_id: str = "1234",
    project_name: str = "North Fen Wind Farm",
    operator_name: str | None = "Fen Renewables Limited",
    technology: str = "Wind Onshore",
    capacity_mw: float | None = 42.5,
    status: REPDStatus = REPDStatus.OPERATIONAL,
    source_status: str = "Operational",
    storage_type: str | None = None,
    is_storage: bool = False,
    region: str | None = "East of England",
    country: str | None = "England",
    planning_authority: str | None = "Fen District Council",
    record_last_updated: str | None = "01/04/2026",
    coordinates: REPDCoordinates | None = None,
    provenance: REPDProvenance | None = None,
) -> REPDSite:
    return REPDSite(
        source_id=source_id,
        project_name=project_name,
        operator_name=operator_name,
        technology=technology,
        capacity_mw=capacity_mw,
        status=status,
        source_status=source_status,
        storage_type=storage_type,
        is_storage=is_storage,
        region=region,
        country=country,
        planning_authority=planning_authority,
        record_last_updated=record_last_updated,
        coordinates=coordinates,
        provenance=provenance or _provenance(),
    )


def _provenance() -> REPDProvenance:
    return REPDProvenance(
        publisher=REPD_PUBLISHER,
        dataset=REPD_DATASET_NAME,
        source_url=SOURCE_URL,
        licence_name=REPD_LICENCE_NAME,
        licence_url=REPD_LICENCE_URL,
        retrieved_at=RETRIEVED,
    )


def _coordinates() -> REPDCoordinates:
    return REPDCoordinates(
        easting_m=530_000.0,
        northing_m=180_000.0,
        latitude=51.503983,
        longitude=-0.128354,
        source_fields=("X-coordinate", "Y-coordinate"),
    )


def test_source_metadata_values_match_the_existing_model_columns() -> None:
    values = repd_source_metadata_values()
    writable_columns = {
        column.name
        for column in SourceMetadata.__table__.columns
        if column.name not in {"created_at", "updated_at"}
    }

    assert set(values) == writable_columns
    assert values["id"] == REPD_SOURCE_ID
    assert values["provider"] == "desnz"
    assert values["dataset"] == "REPD"
    assert values["expected_cadence_seconds"] == REPD_EXPECTED_CADENCE_SECONDS
    assert values["active"] is True
    assert values["documentation_url"].startswith("https://www.gov.uk/")
    assert values["licence_name"] == "Open Government Licence v3.0"
    assert values["licence_url"] == REPD_LICENCE_URL
    assert "Department for Energy Security and Net Zero" in values["attribution"]
    assert SourceMetadata(**values).id == REPD_SOURCE_ID


def test_public_site_id_is_stable_trimmed_and_opaque() -> None:
    first = public_repd_site_id(" 1234 ")
    second = public_repd_site_id("1234")

    assert first == second
    assert first.startswith("repd_")
    assert "1234" not in first
    assert len(first) == 37
    assert public_repd_site_id("1235") != first


@pytest.mark.parametrize("source_id", ["", "   "])
def test_public_site_id_rejects_empty_source_identity(source_id: str) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        public_repd_site_id(source_id)


def test_site_mapping_matches_asset_shape_and_preserves_source_coordinates() -> None:
    site = _site(coordinates=_coordinates())

    values = map_repd_site(site)
    writable_columns = {
        column.name
        for column in Asset.__table__.columns
        if column.name not in {"id", "created_at", "updated_at"}
    }

    assert set(values) == writable_columns
    assert values["source_id"] == REPD_SOURCE_ID
    assert values["external_id"] == "1234"
    assert values["asset_type"] == REPD_ASSET_TYPE
    assert values["display_name"] == "North Fen Wind Farm"
    assert values["fuel_type"] == "wind"
    assert values["region_code"] == "East of England"
    assert values["counterparty"] == "Fen Renewables Limited"
    assert values["capacity_mw"] == 42.5
    assert values["latitude"] == 51.503983
    assert values["longitude"] == -0.128354
    assert values["map_x"] is None
    assert values["map_y"] is None
    assert values["active"] is True
    assert Asset(**values).asset_type == REPD_ASSET_TYPE


def test_attributes_preserve_lifecycle_planning_coordinate_and_full_provenance() -> None:
    values = map_repd_site(_site(coordinates=_coordinates()))
    attributes = values["attributes"]

    assert attributes["classification"] == "reference"
    assert attributes["snapshotKind"] == "complete_reference"
    assert attributes["activeSemantics"] == (
        "present_in_latest_complete_repd_snapshot"
    )
    assert attributes["sourceRecordId"] == "1234"
    assert attributes["publicId"] == public_repd_site_id("1234")
    assert attributes["projectName"] == "North Fen Wind Farm"
    assert attributes["operatorName"] == "Fen Renewables Limited"
    assert attributes["technology"] == "Wind Onshore"
    assert attributes["normalizedFuel"] == "wind"
    assert attributes["fuelTypeDerivation"] == (
        "broad_ui_mapping_from_repd_technology"
    )
    assert attributes["lifecycleStatus"] == "operational"
    assert attributes["sourceLifecycleStatus"] == "Operational"
    assert attributes["region"] == "East of England"
    assert attributes["country"] == "England"
    assert attributes["planningAuthority"] == "Fen District Council"
    assert attributes["recordLastUpdated"] == "01/04/2026"
    assert attributes["locationStatus"] == "source_coordinate_transformed"
    assert attributes["coordinates"] == {
        "eastingM": 530_000.0,
        "northingM": 180_000.0,
        "latitude": 51.503983,
        "longitude": -0.128354,
        "sourceFields": ["X-coordinate", "Y-coordinate"],
        "sourceCRS": OSGB36_BNG_CRS,
        "outputCRS": WGS84_CRS,
        "transform": (
            "Airy 1830 inverse Transverse Mercator and seven-parameter Helmert"
        ),
    }
    assert attributes["provenance"] == {
        "publisher": REPD_PUBLISHER,
        "dataset": REPD_DATASET_NAME,
        "sourceUrl": SOURCE_URL,
        "licenceName": REPD_LICENCE_NAME,
        "licenceUrl": REPD_LICENCE_URL,
        "retrievedAt": datetime(2026, 5, 6, 12, 30, tzinfo=UTC).isoformat(),
    }


def test_missing_capacity_and_coordinates_remain_null_without_invention() -> None:
    site = _site(
        operator_name=None,
        capacity_mw=None,
        region=None,
        country=None,
        planning_authority=None,
        record_last_updated=None,
        coordinates=None,
    )

    values = map_repd_site(site)

    assert values["capacity_mw"] is None
    assert values["latitude"] is None
    assert values["longitude"] is None
    assert values["map_x"] is None
    assert values["map_y"] is None
    assert values["region_code"] is None
    assert values["counterparty"] is None
    assert values["attributes"]["operatorName"] is None
    assert values["attributes"]["region"] is None
    assert values["attributes"]["country"] is None
    assert values["attributes"]["planningAuthority"] is None
    assert values["attributes"]["coordinates"] is None
    assert values["attributes"]["locationStatus"] == "not_available_from_source"
    assert values["attributes"]["provenance"]["sourceUrl"] == SOURCE_URL


@pytest.mark.parametrize(
    ("technology", "is_storage", "expected"),
    [
        ("Wind Offshore", False, "wind"),
        ("Solar Photovoltaics", False, "solar"),
        ("Biomass (dedicated)", False, "biomass"),
        ("Anaerobic Digestion", False, "biomass"),
        ("Large Hydro", False, "hydro"),
        ("Tidal Stream", False, "other"),
        ("Unknown", False, None),
        ("Pumped Storage Hydroelectricity", True, "storage"),
        ("Battery", True, "storage"),
    ],
)
def test_detailed_technology_maps_to_broad_ui_fuel_without_replacing_source_value(
    technology: str,
    is_storage: bool,
    expected: str | None,
) -> None:
    site = _site(
        technology=technology,
        is_storage=is_storage,
        storage_type="Stand-alone Storage" if is_storage else None,
    )

    assert normalized_repd_fuel(site) == expected
    values = map_repd_site(site)
    assert values["fuel_type"] == expected
    assert values["attributes"]["technology"] == technology


def test_present_in_snapshot_is_not_confused_with_operational_lifecycle() -> None:
    site = _site(
        status=REPDStatus.PLANNED,
        source_status="Awaiting Construction",
    )

    values = map_repd_site(site)

    assert values["active"] is True
    assert values["attributes"]["lifecycleStatus"] == "planned"
    assert values["attributes"]["sourceLifecycleStatus"] == "Awaiting Construction"


@pytest.mark.parametrize("capacity", [-1.0, float("inf"), float("nan")])
def test_invalid_capacity_fails_closed(capacity: float) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        map_repd_site(_site(capacity_mw=capacity))


def test_wrong_source_or_provenance_cannot_be_persisted_as_authoritative_repd() -> None:
    with pytest.raises(ValueError, match="canonical DESNZ"):
        map_repd_site(_site(), source_id="worker.repd")

    wrong_publisher = replace(_provenance(), publisher="Example Publisher")
    with pytest.raises(ValueError, match="publisher provenance"):
        map_repd_site(_site(provenance=wrong_publisher))

    wrong_dataset = replace(_provenance(), dataset="Synthetic REPD")
    with pytest.raises(ValueError, match="dataset provenance"):
        map_repd_site(_site(provenance=wrong_dataset))


def test_full_source_name_survives_when_bounded_asset_columns_are_truncated() -> None:
    project_name = "P" * 180
    operator_name = "O" * 140
    region = "R" * 80

    values = map_repd_site(
        _site(
            project_name=project_name,
            operator_name=operator_name,
            region=region,
        )
    )

    assert values["display_name"] == project_name[:160]
    assert values["counterparty"] == operator_name[:120]
    assert values["region_code"] == region[:64]
    assert values["attributes"]["projectName"] == project_name
    assert values["attributes"]["operatorName"] == operator_name
    assert values["attributes"]["region"] == region


def test_complete_snapshot_membership_is_sorted_scoped_and_deduplicated() -> None:
    membership = repd_snapshot_membership(
        (
            _site(source_id="20"),
            _site(source_id="3"),
            _site(source_id="20"),
        )
    )

    assert membership.is_complete is True
    assert membership.source_id == REPD_SOURCE_ID
    assert membership.asset_type == REPD_ASSET_TYPE
    assert membership.deactivation_scope == {
        "source_id": REPD_SOURCE_ID,
        "asset_type": REPD_ASSET_TYPE,
    }
    assert membership.active_external_ids == ("20", "3")
    assert membership.absent_external_ids(("2", "3", "4", "4")) == ("2", "4")


def test_successful_empty_complete_snapshot_marks_every_existing_key_absent() -> None:
    membership = repd_snapshot_membership(())

    assert membership.is_complete is True
    assert membership.active_external_ids == ()
    assert membership.absent_external_ids(("30", "10", "20")) == (
        "10",
        "20",
        "30",
    )


def test_snapshot_membership_rejects_noncanonical_scope_and_empty_keys() -> None:
    with pytest.raises(ValueError, match="canonical DESNZ"):
        repd_snapshot_membership((), source_id="other.repd")

    with pytest.raises(ValueError, match="cannot be empty"):
        repd_snapshot_membership((_site(source_id=" "),))

    membership = repd_snapshot_membership(())
    with pytest.raises(TypeError, match="must be a string"):
        membership.absent_external_ids((123,))  # type: ignore[arg-type]


def test_snapshot_membership_validates_authoritative_provenance_and_asset_key_size() -> None:
    wrong_publisher = replace(_provenance(), publisher="Example Publisher")
    with pytest.raises(ValueError, match="publisher provenance"):
        repd_snapshot_membership((_site(provenance=wrong_publisher),))

    with pytest.raises(ValueError, match="external_id capacity"):
        repd_snapshot_membership((_site(source_id="x" * 121),))
