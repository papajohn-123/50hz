from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.geography.repd import (
    OSGB36_BNG_CRS,
    REPD_DATASET_NAME,
    REPD_LICENCE_NAME,
    REPD_LICENCE_URL,
    REPD_PUBLICATION_URL,
    REPD_PUBLISHER,
    WGS84_CRS,
    REPDSchemaError,
    REPDStatus,
    normalize_repd_status,
    osgb36_to_wgs84,
    parse_repd_csv,
)


CURRENT_HEADERS = (
    "Ref ID",
    "Record Last Updated (dd/mm/yyyy)",
    "Operator (or Applicant)",
    "Site Name",
    "Technology Type",
    "Storage Type",
    "Installed Capacity (MWelec)",
    "Development Status",
    "Development Status (short)",
    "Region",
    "Country",
    "X-coordinate",
    "Y-coordinate",
    "Planning Authority",
)


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "Ref ID": "1001",
        "Record Last Updated (dd/mm/yyyy)": "01/04/2026",
        "Operator (or Applicant)": "Fen Power Ltd",
        "Site Name": "Fen Solar Farm",
        "Technology Type": "Solar Photovoltaics",
        "Storage Type": "",
        "Installed Capacity (MWelec)": "42.5",
        "Development Status": "Planning Permission Granted",
        "Development Status (short)": "Awaiting Construction",
        "Region": "East of England",
        "Country": "England",
        "X-coordinate": "651409.903",
        "Y-coordinate": "313177.270",
        "Planning Authority": "Fen District Council",
    }
    row.update(overrides)
    return row


def _csv_bytes(
    rows: list[dict[str, str]],
    *,
    headers: tuple[str, ...] = CURRENT_HEADERS,
    encoding: str = "utf-8",
    bom: bool = False,
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    data = output.getvalue().encode(encoding)
    return (b"\xef\xbb\xbf" + data) if bom else data


def _retrieved_at() -> datetime:
    return datetime(2026, 5, 6, 12, 0, tzinfo=UTC)


def test_osgb36_transform_matches_known_national_grid_fixture() -> None:
    # TG 51409 13177 is the canonical OS worked example.  A national Helmert
    # transform is expected to be map-grade rather than OSTN15 survey-grade.
    latitude, longitude = osgb36_to_wgs84(651_409.903, 313_177.270)

    assert latitude == pytest.approx(52.6579786, abs=1e-5)
    assert longitude == pytest.approx(1.7160519, abs=1e-5)


def test_parses_utf8_bom_and_preserves_active_storage_site() -> None:
    payload = _csv_bytes(
        [
            _row(
                **{
                    "Technology Type": "Battery",
                    "Storage Type": "Stand-alone Storage",
                    "Installed Capacity (MWelec)": "1,234.5 MW",
                }
            )
        ],
        bom=True,
    )

    result = parse_repd_csv(payload, retrieved_at=_retrieved_at())

    assert result.encoding == "utf-8"
    assert result.input_rows == 1
    assert result.retained_rows == 1
    site = result.sites[0]
    assert site.source_id == "1001"
    assert site.project_name == "Fen Solar Farm"
    assert site.operator_name == "Fen Power Ltd"
    assert site.technology == "Battery"
    assert site.capacity_mw == 1_234.5
    assert site.status is REPDStatus.PLANNED
    assert site.source_status == "Awaiting Construction"
    assert site.storage_type == "Stand-alone Storage"
    assert site.is_storage is True
    assert site.region == "East of England"
    assert site.country == "England"
    assert site.planning_authority == "Fen District Council"
    assert site.record_last_updated == "01/04/2026"
    assert site.coordinates is not None


def test_falls_back_to_cp1252_without_corrupting_project_name() -> None:
    payload = _csv_bytes(
        [_row(**{"Site Name": "St Mary’s Solar Farm"})],
        encoding="cp1252",
    )

    result = parse_repd_csv(payload, retrieved_at=_retrieved_at())

    assert result.encoding == "cp1252"
    assert result.sites[0].project_name == "St Mary’s Solar Farm"


def test_accepts_drifted_column_labels_and_injected_transformer() -> None:
    headers = (
        "REPD Reference ID",
        "Development Name",
        "Generation Technology",
        "Energy Storage Type",
        "Capacity (MW)",
        "Project Status",
        "OS Grid Easting",
        "OS Grid Northing",
    )
    row = {
        "REPD Reference ID": "R-7",
        "Development Name": "North Store",
        "Generation Technology": "Liquid Air Energy Storage",
        "Energy Storage Type": "Stand-alone Storage",
        "Capacity (MW)": "60",
        "Project Status": "Under Construction",
        "OS Grid Easting": "400000",
        "OS Grid Northing": "300000",
    }

    result = parse_repd_csv(
        _csv_bytes([row], headers=headers),
        retrieved_at=_retrieved_at(),
        transformer=lambda _easting, _northing: (53.0, -2.0),
    )

    site = result.sites[0]
    assert site.status is REPDStatus.UNDER_CONSTRUCTION
    assert site.coordinates is not None
    assert site.coordinates.latitude == 53.0
    assert site.coordinates.longitude == -2.0
    assert site.coordinates.source_fields == ("OS Grid Easting", "OS Grid Northing")
    assert site.coordinates.transform == "injected coordinate transformer"


@pytest.mark.parametrize(
    ("source_status", "expected"),
    [
        ("Operational", REPDStatus.OPERATIONAL),
        (" operational ", REPDStatus.OPERATIONAL),
        ("Under-Construction", REPDStatus.UNDER_CONSTRUCTION),
        ("Awaiting Construction", REPDStatus.PLANNED),
        ("Planning Application Submitted", REPDStatus.PLANNED),
        ("Appeal Lodged", REPDStatus.PLANNED),
        ("No Application Required", REPDStatus.PLANNED),
        ("Application Withdrawn", None),
        ("Decommissioned", None),
        ("Revised", None),
        ("future mystery state", None),
    ],
)
def test_status_normalization(
    source_status: str,
    expected: REPDStatus | None,
) -> None:
    assert normalize_repd_status(source_status) is expected


def test_terminal_and_unknown_statuses_are_excluded_fail_closed() -> None:
    rows = [
        _row(**{"Ref ID": "1", "Development Status (short)": "Operational"}),
        _row(**{"Ref ID": "2", "Development Status (short)": "Abandoned"}),
        _row(**{"Ref ID": "3", "Development Status (short)": "Future mystery"}),
    ]

    result = parse_repd_csv(_csv_bytes(rows), retrieved_at=_retrieved_at())

    assert [site.source_id for site in result.sites] == ["1"]
    assert result.excluded_status_rows == 2
    assert any("unrecognised status" in warning for warning in result.warnings)


def test_invalid_coordinates_do_not_discard_authoritative_site_metadata() -> None:
    rows = [
        _row(**{"Ref ID": "1", "X-coordinate": "", "Y-coordinate": ""}),
        _row(**{"Ref ID": "2", "X-coordinate": "east", "Y-coordinate": "200000"}),
        _row(**{"Ref ID": "3", "X-coordinate": "400000", "Y-coordinate": ""}),
        _row(**{"Ref ID": "4", "X-coordinate": "999999", "Y-coordinate": "200000"}),
    ]

    result = parse_repd_csv(_csv_bytes(rows), retrieved_at=_retrieved_at())

    assert result.retained_rows == 4
    assert result.invalid_coordinate_rows == 4
    assert all(site.coordinates is None for site in result.sites)
    assert any("unusable coordinate" in warning for warning in result.warnings)


def test_non_finite_transform_output_is_rejected_without_dropping_site() -> None:
    result = parse_repd_csv(
        _csv_bytes([_row()]),
        retrieved_at=_retrieved_at(),
        transformer=lambda _easting, _northing: (float("nan"), -1.0),
    )

    assert result.retained_rows == 1
    assert result.sites[0].coordinates is None
    assert result.invalid_coordinate_rows == 1


def test_duplicate_source_id_is_collapsed_to_most_advanced_record() -> None:
    rows = [
        _row(
            **{
                "Ref ID": "77",
                "Development Status (short)": "Application Submitted",
            }
        ),
        _row(
            **{
                "Ref ID": "77",
                "Development Status (short)": "Operational",
                "Installed Capacity (MWelec)": "45",
            }
        ),
    ]

    result = parse_repd_csv(_csv_bytes(rows), retrieved_at=_retrieved_at())

    assert result.input_rows == 2
    assert result.retained_rows == 1
    assert result.duplicate_rows == 1
    assert result.sites[0].status is REPDStatus.OPERATIONAL
    assert result.sites[0].capacity_mw == 45.0
    assert any("duplicate source ID" in warning for warning in result.warnings)


def test_missing_and_malformed_capacity_are_explicit_but_sites_are_retained() -> None:
    rows = [
        _row(**{"Ref ID": "1", "Installed Capacity (MWelec)": ""}),
        _row(**{"Ref ID": "2", "Installed Capacity (MWelec)": "about forty"}),
    ]

    result = parse_repd_csv(_csv_bytes(rows), retrieved_at=_retrieved_at())

    assert [site.capacity_mw for site in result.sites] == [None, None]
    assert result.missing_capacity_rows == 2
    assert any("invalid capacity" in warning for warning in result.warnings)


def test_provenance_and_coordinate_metadata_are_attached_to_each_site() -> None:
    retrieved_at = datetime(
        2026,
        5,
        6,
        13,
        30,
        tzinfo=timezone(timedelta(hours=1)),
    )
    source_url = "https://assets.publishing.service.gov.uk/example/repd.csv"

    result = parse_repd_csv(
        _csv_bytes([_row()]),
        retrieved_at=retrieved_at,
        source_url=source_url,
    )

    site = result.sites[0]
    assert site.provenance.publisher == REPD_PUBLISHER
    assert site.provenance.dataset == REPD_DATASET_NAME
    assert site.provenance.source_url == source_url
    assert site.provenance.licence_name == REPD_LICENCE_NAME
    assert site.provenance.licence_url == REPD_LICENCE_URL
    assert site.provenance.retrieved_at == datetime(2026, 5, 6, 12, 30, tzinfo=UTC)
    assert site.coordinates is not None
    assert site.coordinates.easting_m == 651_409.903
    assert site.coordinates.northing_m == 313_177.270
    assert site.coordinates.source_crs == OSGB36_BNG_CRS
    assert site.coordinates.output_crs == WGS84_CRS


def test_default_provenance_uses_stable_official_publication_url() -> None:
    result = parse_repd_csv(_csv_bytes([_row()]), retrieved_at=_retrieved_at())

    assert result.sites[0].provenance.source_url == REPD_PUBLICATION_URL


def test_full_status_is_used_when_short_status_column_is_absent() -> None:
    headers = tuple(
        header for header in CURRENT_HEADERS if header != "Development Status (short)"
    )
    row = _row()
    row.pop("Development Status (short)")

    result = parse_repd_csv(
        _csv_bytes([row], headers=headers),
        retrieved_at=_retrieved_at(),
    )

    assert result.sites[0].status is REPDStatus.PLANNED
    assert result.sites[0].source_status == "Planning Permission Granted"


def test_missing_required_columns_raise_clear_schema_error() -> None:
    payload = b"Ref ID,Site Name,Development Status\n1,Project,Operational\n"

    with pytest.raises(REPDSchemaError, match="missing required column"):
        parse_repd_csv(payload, retrieved_at=_retrieved_at())


def test_naive_retrieval_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_repd_csv(
            _csv_bytes([_row()]),
            retrieved_at=datetime(2026, 5, 6),
        )
