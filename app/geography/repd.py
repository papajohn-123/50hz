"""Pure adapter for DESNZ's Renewable Energy Planning Database CSV.

REPD is quarterly reference data, not a live operating feed.  The adapter keeps
the source's lifecycle label alongside a small, stable status vocabulary and
never invents a location when REPD has no usable British National Grid point.

The built-in OSGB36-to-WGS84 conversion uses the published Airy 1830 inverse
Transverse Mercator followed by the standard seven-parameter Helmert transform.
It is dependency-free and appropriate for map display (normally within several
metres); it deliberately does not claim OSTN15 survey-grade accuracy.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


REPD_PUBLICATION_URL = (
    "https://www.gov.uk/government/publications/"
    "renewable-energy-planning-database-quarterly-extract"
)
REPD_DATASET_NAME = "Renewable Energy Planning Database (REPD)"
REPD_PUBLISHER = "Department for Energy Security and Net Zero"
REPD_LICENCE_NAME = "Open Government Licence v3.0"
REPD_LICENCE_URL = (
    "https://www.nationalarchives.gov.uk/doc/"
    "open-government-licence/version/3/"
)
OSGB36_BNG_CRS = "OSGB36 / British National Grid (EPSG:27700)"
WGS84_CRS = "WGS 84 (EPSG:4326)"
DEFAULT_TRANSFORM_NAME = (
    "Airy 1830 inverse Transverse Mercator and seven-parameter Helmert"
)

CoordinateTransformer = Callable[[float, float], tuple[float, float]]


class REPDSchemaError(ValueError):
    """Raised when a CSV does not contain a recognisable REPD schema."""


class REPDStatus(StrEnum):
    """App-facing lifecycle states retained from REPD."""

    OPERATIONAL = "operational"
    UNDER_CONSTRUCTION = "under_construction"
    PLANNED = "planned"


@dataclass(frozen=True, slots=True)
class REPDProvenance:
    publisher: str
    dataset: str
    source_url: str
    licence_name: str
    licence_url: str
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        object.__setattr__(self, "retrieved_at", self.retrieved_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class REPDCoordinates:
    """A source BNG point and its derived WGS84 representation."""

    easting_m: float
    northing_m: float
    latitude: float
    longitude: float
    source_fields: tuple[str, str]
    source_crs: str = OSGB36_BNG_CRS
    output_crs: str = WGS84_CRS
    transform: str = DEFAULT_TRANSFORM_NAME

    def __post_init__(self) -> None:
        values = (
            self.easting_m,
            self.northing_m,
            self.latitude,
            self.longitude,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("coordinates must be finite")
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")


@dataclass(frozen=True, slots=True)
class REPDSite:
    source_id: str
    project_name: str
    operator_name: str | None
    technology: str
    capacity_mw: float | None
    status: REPDStatus
    source_status: str
    storage_type: str | None
    is_storage: bool
    region: str | None
    country: str | None
    planning_authority: str | None
    record_last_updated: str | None
    coordinates: REPDCoordinates | None
    provenance: REPDProvenance


@dataclass(frozen=True, slots=True)
class REPDParseResult:
    sites: tuple[REPDSite, ...]
    warnings: tuple[str, ...]
    encoding: str
    input_rows: int
    retained_rows: int
    excluded_status_rows: int
    invalid_rows: int
    duplicate_rows: int
    missing_capacity_rows: int
    invalid_coordinate_rows: int


_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "source_id": (
        "Ref ID",
        "REPD Ref ID",
        "REPD Reference ID",
        "Reference ID",
    ),
    "project_name": ("Site Name", "Project Name", "Development Name"),
    "operator_name": (
        "Operator (or Applicant)",
        "Operator or Applicant",
        "Operator",
        "Applicant",
    ),
    "technology": ("Technology Type", "Technology", "Generation Technology"),
    "capacity": (
        "Installed Capacity (MWelec)",
        "Installed Capacity (MW)",
        "Installed Capacity MWelec",
        "Capacity (MW)",
    ),
    "status_short": (
        "Development Status (short)",
        "Development Status Short",
        "Short Development Status",
    ),
    "status_full": ("Development Status", "Project Status", "Status"),
    "storage_type": ("Storage Type", "Energy Storage Type"),
    "region": ("Region",),
    "country": ("Country",),
    "planning_authority": ("Planning Authority",),
    "record_last_updated": (
        "Record Last Updated (dd/mm/yyyy)",
        "Record Last Updated",
        "Last Updated",
    ),
    "easting": (
        "X-coordinate",
        "X coordinate",
        "OSGB Easting",
        "OS Grid Easting",
        "Easting",
    ),
    "northing": (
        "Y-coordinate",
        "Y coordinate",
        "OSGB Northing",
        "OS Grid Northing",
        "Northing",
    ),
}

_ACTIVE_STATUS_ALIASES: dict[str, REPDStatus] = {
    "operational": REPDStatus.OPERATIONAL,
    "operating": REPDStatus.OPERATIONAL,
    "commissioned": REPDStatus.OPERATIONAL,
    "under construction": REPDStatus.UNDER_CONSTRUCTION,
    "in construction": REPDStatus.UNDER_CONSTRUCTION,
    "construction": REPDStatus.UNDER_CONSTRUCTION,
    "awaiting construction": REPDStatus.PLANNED,
    "planning permission granted": REPDStatus.PLANNED,
    "application granted": REPDStatus.PLANNED,
    "permission granted": REPDStatus.PLANNED,
    "consented": REPDStatus.PLANNED,
    "consent granted": REPDStatus.PLANNED,
    "application submitted": REPDStatus.PLANNED,
    "planning application submitted": REPDStatus.PLANNED,
    "appeal lodged": REPDStatus.PLANNED,
    "no application required": REPDStatus.PLANNED,
    "in planning": REPDStatus.PLANNED,
    "pre planning": REPDStatus.PLANNED,
    "preplanning": REPDStatus.PLANNED,
}

_INACTIVE_STATUS_ALIASES = {
    "abandoned",
    "appeal refused",
    "appeal withdrawn",
    "application refused",
    "application withdrawn",
    "decommissioned",
    "expired",
    "planning application withdrawn",
    "planning permission expired",
    "planning permission refused",
    "refused",
    "revised",
    "secretary of state refusal",
    "secretary of state refused",
    "withdrawn",
}

_STORAGE_TECHNOLOGIES = {
    "battery",
    "compressed air energy storage",
    "flywheel",
    "flywheels",
    "liquid air energy storage",
    "pumped storage hydroelectricity",
}

_NULL_MARKERS = {"", "-", "n/a", "na", "not applicable", "not set", "null"}


def parse_repd_csv(
    payload: bytes | str,
    *,
    retrieved_at: datetime,
    source_url: str = REPD_PUBLICATION_URL,
    transformer: CoordinateTransformer | None = None,
    transformer_name: str | None = None,
) -> REPDParseResult:
    """Parse a quarterly DESNZ REPD CSV into active, map-ready sites.

    Terminal lifecycle rows remain represented by aggregate counts, but are not
    returned as sites.  Active rows with absent capacity or coordinates are kept
    with ``None`` for the missing value so source gaps are not silently turned
    into fabricated facts.
    """

    if transformer is None:
        transformer = osgb36_to_wgs84
    if transformer_name is None:
        transformer_name = (
            DEFAULT_TRANSFORM_NAME
            if transformer is osgb36_to_wgs84
            else "injected coordinate transformer"
        )
    if not source_url.strip():
        raise ValueError("source_url cannot be empty")

    text, encoding = _decode_csv(payload)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise REPDSchemaError("REPD CSV has no header row")
    headers = _resolve_headers(reader.fieldnames)

    provenance = REPDProvenance(
        publisher=REPD_PUBLISHER,
        dataset=REPD_DATASET_NAME,
        source_url=source_url.strip(),
        licence_name=REPD_LICENCE_NAME,
        licence_url=REPD_LICENCE_URL,
        retrieved_at=retrieved_at,
    )

    sites_by_id: dict[str, REPDSite] = {}
    issue_counts: dict[str, int] = defaultdict(int)
    issue_examples: dict[str, list[str]] = defaultdict(list)
    input_rows = 0
    excluded_status_rows = 0
    invalid_rows = 0
    duplicate_rows = 0
    missing_capacity_rows = 0
    invalid_coordinate_rows = 0

    for row_number, row in enumerate(reader, start=2):
        input_rows += 1
        status, source_status, status_recognised = _status_from_row(row, headers)
        if status is None:
            excluded_status_rows += 1
            if not status_recognised:
                _record_issue(
                    issue_counts,
                    issue_examples,
                    "unrecognised status",
                    f"row {row_number}: {source_status or 'blank'}",
                )
            continue

        try:
            source_id = _required_source_text(row, headers["source_id"], "source ID")
            project_name = _required_source_text(
                row,
                headers["project_name"],
                "project name",
            )
            technology = _required_source_text(
                row,
                headers["technology"],
                "technology",
                allow_unknown=True,
            )
        except ValueError as exc:
            invalid_rows += 1
            _record_issue(
                issue_counts,
                issue_examples,
                "invalid active record",
                f"row {row_number}: {exc}",
            )
            continue

        capacity_mw, capacity_valid = _capacity(row.get(headers["capacity"]))
        if capacity_mw is None:
            missing_capacity_rows += 1
        if not capacity_valid:
            _record_issue(
                issue_counts,
                issue_examples,
                "invalid capacity",
                f"row {row_number} ({source_id})",
            )

        storage_header = headers.get("storage_type")
        storage_type = (
            _optional_source_text(row.get(storage_header))
            if storage_header is not None
            else None
        )
        operator_name = _optional_field(row, headers, "operator_name")
        region = _optional_field(row, headers, "region")
        country = _optional_field(row, headers, "country")
        planning_authority = _optional_field(row, headers, "planning_authority")
        record_last_updated = _optional_field(row, headers, "record_last_updated")
        coordinates, coordinate_error = _coordinates(
            row,
            easting_header=headers["easting"],
            northing_header=headers["northing"],
            transformer=transformer,
            transformer_name=transformer_name,
        )
        if coordinate_error is not None:
            invalid_coordinate_rows += 1
            _record_issue(
                issue_counts,
                issue_examples,
                "unusable coordinate",
                f"row {row_number} ({source_id}): {coordinate_error}",
            )

        technology_key = _normalise_words(technology)
        candidate = REPDSite(
            source_id=source_id,
            project_name=project_name,
            operator_name=operator_name,
            technology=technology,
            capacity_mw=capacity_mw,
            status=status,
            source_status=source_status,
            storage_type=storage_type,
            is_storage=(
                storage_type is not None or technology_key in _STORAGE_TECHNOLOGIES
            ),
            region=region,
            country=country,
            planning_authority=planning_authority,
            record_last_updated=record_last_updated,
            coordinates=coordinates,
            provenance=provenance,
        )

        existing = sites_by_id.get(source_id)
        if existing is not None:
            duplicate_rows += 1
            _record_issue(
                issue_counts,
                issue_examples,
                "duplicate source ID",
                f"row {row_number}: {source_id}",
            )
            if _site_preference(candidate) > _site_preference(existing):
                sites_by_id[source_id] = candidate
            continue
        sites_by_id[source_id] = candidate

    warnings = tuple(
        _format_issue(kind, count, issue_examples[kind])
        for kind, count in issue_counts.items()
    )
    sites = tuple(sites_by_id.values())
    return REPDParseResult(
        sites=sites,
        warnings=warnings,
        encoding=encoding,
        input_rows=input_rows,
        retained_rows=len(sites),
        excluded_status_rows=excluded_status_rows,
        invalid_rows=invalid_rows,
        duplicate_rows=duplicate_rows,
        missing_capacity_rows=missing_capacity_rows,
        invalid_coordinate_rows=invalid_coordinate_rows,
    )


def normalize_repd_status(value: object) -> REPDStatus | None:
    """Map a source lifecycle label to a retained status, or ``None``."""

    if value is None:
        return None
    return _ACTIVE_STATUS_ALIASES.get(_normalise_words(str(value)))


def osgb36_to_wgs84(easting_m: float, northing_m: float) -> tuple[float, float]:
    """Convert an EPSG:27700 point to WGS84 latitude/longitude.

    This is the standard national Helmert approximation.  It is deterministic,
    dependency-free, and intentionally returns latitude first.
    """

    if not math.isfinite(easting_m) or not math.isfinite(northing_m):
        raise ValueError("easting and northing must be finite")

    latitude, longitude = _bng_to_osgb36_geodetic(easting_m, northing_m)
    x, y, z = _geodetic_to_cartesian(
        latitude,
        longitude,
        semi_major=6_377_563.396,
        semi_minor=6_356_256.909,
    )

    # OSGB36 -> WGS84, position-vector convention.  Rotations are arcseconds
    # and scale is parts per million before conversion below.
    tx, ty, tz = 446.448, -125.157, 542.060
    scale = 20.4894 * 1e-6
    arcseconds_to_radians = math.pi / (180.0 * 3_600.0)
    rx = 0.1502 * arcseconds_to_radians
    ry = 0.2470 * arcseconds_to_radians
    rz = 0.8421 * arcseconds_to_radians
    factor = 1.0 + scale

    transformed_x = tx + factor * x - rz * y + ry * z
    transformed_y = ty + rz * x + factor * y - rx * z
    transformed_z = tz - ry * x + rx * y + factor * z

    wgs84_latitude, wgs84_longitude = _cartesian_to_geodetic(
        transformed_x,
        transformed_y,
        transformed_z,
        semi_major=6_378_137.0,
        semi_minor=6_356_752.3141,
    )
    return math.degrees(wgs84_latitude), math.degrees(wgs84_longitude)


def _decode_csv(payload: bytes | str) -> tuple[str, str]:
    if isinstance(payload, str):
        if not payload.strip():
            raise REPDSchemaError("REPD CSV is empty")
        return payload.removeprefix("\ufeff"), "unicode"
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes or str")
    if not payload:
        raise REPDSchemaError("REPD CSV is empty")
    try:
        return payload.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return payload.decode("cp1252"), "cp1252"


def _resolve_headers(fieldnames: list[str]) -> dict[str, str]:
    available: dict[str, str] = {}
    for header in fieldnames:
        if header is not None:
            available.setdefault(_normalise_header(header), header)

    resolved: dict[str, str] = {}
    for logical_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            actual = available.get(_normalise_header(alias))
            if actual is not None:
                resolved[logical_name] = actual
                break

    required = {
        "source_id",
        "project_name",
        "technology",
        "capacity",
        "easting",
        "northing",
    }
    missing = sorted(required - resolved.keys())
    if "status_short" not in resolved and "status_full" not in resolved:
        missing.append("development status")
    if missing:
        raise REPDSchemaError(
            "REPD CSV is missing required column(s): " + ", ".join(missing)
        )
    return resolved


def _status_from_row(
    row: Mapping[str, object],
    headers: Mapping[str, str],
) -> tuple[REPDStatus | None, str, bool]:
    # The short status is REPD's canonical lifecycle field.  The full field is
    # only a schema fallback: using it to override an unfamiliar short value
    # could accidentally resurrect a terminal record after a vocabulary change.
    short_status = (
        _optional_source_text(row.get(headers["status_short"]))
        if "status_short" in headers
        else None
    )
    full_status = (
        _optional_source_text(row.get(headers["status_full"]))
        if "status_full" in headers
        else None
    )
    candidate = short_status or full_status
    if candidate is None:
        return None, "", False
    status = normalize_repd_status(candidate)
    if status is not None:
        return status, candidate, True
    if _normalise_words(candidate) in _INACTIVE_STATUS_ALIASES:
        return None, candidate, True
    return None, candidate, False


def _optional_field(
    row: Mapping[str, object],
    headers: Mapping[str, str],
    logical_name: str,
) -> str | None:
    header = headers.get(logical_name)
    return _optional_source_text(row.get(header)) if header is not None else None


def _required_source_text(
    row: Mapping[str, object],
    header: str,
    field_name: str,
    *,
    allow_unknown: bool = False,
) -> str:
    value = _optional_source_text(row.get(header))
    if value is None or (not allow_unknown and value.casefold() == "unknown"):
        raise ValueError(f"{field_name} is missing")
    return value


def _optional_source_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.casefold() in _NULL_MARKERS:
        return None
    return text


def _capacity(value: object) -> tuple[float | None, bool]:
    text = _optional_source_text(value)
    if text is None:
        return None, True
    cleaned = text.replace(",", "").strip()
    cleaned = re.sub(r"\s*(?:mw(?:elec)?)\s*$", "", cleaned, flags=re.IGNORECASE)
    try:
        capacity = float(cleaned)
    except ValueError:
        return None, False
    if not math.isfinite(capacity) or capacity <= 0:
        return None, False
    return capacity, True


def _coordinates(
    row: Mapping[str, object],
    *,
    easting_header: str,
    northing_header: str,
    transformer: CoordinateTransformer,
    transformer_name: str,
) -> tuple[REPDCoordinates | None, str | None]:
    easting_text = _optional_source_text(row.get(easting_header))
    northing_text = _optional_source_text(row.get(northing_header))
    if easting_text is None and northing_text is None:
        return None, "source point is missing"
    if easting_text is None or northing_text is None:
        return None, "source point is incomplete"
    try:
        easting = float(easting_text.replace(",", ""))
        northing = float(northing_text.replace(",", ""))
    except ValueError:
        return None, "source point is not numeric"
    if not math.isfinite(easting) or not math.isfinite(northing):
        return None, "source point is not finite"

    # The nominal BNG grid extends to 700km east, but official REPD offshore
    # points currently reach just beyond it.  These deliberately conservative
    # projection bounds retain those records while rejecting obvious bad data.
    if not (-100_000 <= easting <= 800_000 and -100_000 <= northing <= 1_400_000):
        return None, "source point is outside plausible British National Grid bounds"
    try:
        latitude, longitude = transformer(easting, northing)
        coordinates = REPDCoordinates(
            easting_m=easting,
            northing_m=northing,
            latitude=latitude,
            longitude=longitude,
            source_fields=(easting_header, northing_header),
            transform=transformer_name,
        )
    except (ArithmeticError, TypeError, ValueError) as exc:
        return None, f"coordinate transform failed ({exc})"
    return coordinates, None


def _site_preference(site: REPDSite) -> tuple[int, int, int]:
    status_priority = {
        REPDStatus.PLANNED: 1,
        REPDStatus.UNDER_CONSTRUCTION: 2,
        REPDStatus.OPERATIONAL: 3,
    }
    return (
        status_priority[site.status],
        int(site.coordinates is not None),
        int(site.capacity_mw is not None),
    )


def _record_issue(
    counts: dict[str, int],
    examples: dict[str, list[str]],
    kind: str,
    detail: str,
) -> None:
    counts[kind] += 1
    if len(examples[kind]) < 3:
        examples[kind].append(detail)


def _format_issue(kind: str, count: int, examples: list[str]) -> str:
    suffix = "; examples: " + "; ".join(examples) if examples else ""
    return f"{count} {kind} row(s){suffix}"


def _normalise_header(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _normalise_words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _bng_to_osgb36_geodetic(easting: float, northing: float) -> tuple[float, float]:
    semi_major = 6_377_563.396
    semi_minor = 6_356_256.909
    scale = 0.999_601_271_7
    origin_latitude = math.radians(49.0)
    origin_longitude = math.radians(-2.0)
    false_northing = -100_000.0
    false_easting = 400_000.0
    eccentricity_squared = 1.0 - (semi_minor * semi_minor) / (
        semi_major * semi_major
    )
    third_flattening = (semi_major - semi_minor) / (semi_major + semi_minor)

    latitude = origin_latitude
    meridional_arc = 0.0
    for _ in range(20):
        latitude += (northing - false_northing - meridional_arc) / (
            semi_major * scale
        )
        meridional_arc = _meridional_arc(
            latitude,
            origin_latitude=origin_latitude,
            semi_minor=semi_minor,
            scale=scale,
            third_flattening=third_flattening,
        )
        if abs(northing - false_northing - meridional_arc) < 1e-5:
            break
    else:
        raise ValueError("BNG inverse projection did not converge")

    sin_latitude = math.sin(latitude)
    cos_latitude = math.cos(latitude)
    tangent = math.tan(latitude)
    nu = semi_major * scale / math.sqrt(
        1.0 - eccentricity_squared * sin_latitude * sin_latitude
    )
    rho = (
        semi_major
        * scale
        * (1.0 - eccentricity_squared)
        / (1.0 - eccentricity_squared * sin_latitude * sin_latitude) ** 1.5
    )
    eta_squared = nu / rho - 1.0
    delta_easting = easting - false_easting

    vii = tangent / (2.0 * rho * nu)
    viii = tangent / (24.0 * rho * nu**3) * (
        5.0 + 3.0 * tangent**2 + eta_squared - 9.0 * tangent**2 * eta_squared
    )
    ix = tangent / (720.0 * rho * nu**5) * (
        61.0 + 90.0 * tangent**2 + 45.0 * tangent**4
    )
    x = 1.0 / (cos_latitude * nu)
    xi = 1.0 / (cos_latitude * 6.0 * nu**3) * (nu / rho + 2.0 * tangent**2)
    xii = 1.0 / (cos_latitude * 120.0 * nu**5) * (
        5.0 + 28.0 * tangent**2 + 24.0 * tangent**4
    )
    xiia = 1.0 / (cos_latitude * 5_040.0 * nu**7) * (
        61.0 + 662.0 * tangent**2 + 1_320.0 * tangent**4 + 720.0 * tangent**6
    )

    latitude = (
        latitude
        - vii * delta_easting**2
        + viii * delta_easting**4
        - ix * delta_easting**6
    )
    longitude = (
        origin_longitude
        + x * delta_easting
        - xi * delta_easting**3
        + xii * delta_easting**5
        - xiia * delta_easting**7
    )
    return latitude, longitude


def _meridional_arc(
    latitude: float,
    *,
    origin_latitude: float,
    semi_minor: float,
    scale: float,
    third_flattening: float,
) -> float:
    n = third_flattening
    ma = (1.0 + n + 1.25 * n**2 + 1.25 * n**3) * (
        latitude - origin_latitude
    )
    mb = (3.0 * n + 3.0 * n**2 + 21.0 / 8.0 * n**3) * math.sin(
        latitude - origin_latitude
    ) * math.cos(latitude + origin_latitude)
    mc = (15.0 / 8.0 * n**2 + 15.0 / 8.0 * n**3) * math.sin(
        2.0 * (latitude - origin_latitude)
    ) * math.cos(2.0 * (latitude + origin_latitude))
    md = 35.0 / 24.0 * n**3 * math.sin(
        3.0 * (latitude - origin_latitude)
    ) * math.cos(3.0 * (latitude + origin_latitude))
    return semi_minor * scale * (ma - mb + mc - md)


def _geodetic_to_cartesian(
    latitude: float,
    longitude: float,
    *,
    semi_major: float,
    semi_minor: float,
) -> tuple[float, float, float]:
    eccentricity_squared = 1.0 - (semi_minor * semi_minor) / (
        semi_major * semi_major
    )
    sin_latitude = math.sin(latitude)
    nu = semi_major / math.sqrt(
        1.0 - eccentricity_squared * sin_latitude * sin_latitude
    )
    x = nu * math.cos(latitude) * math.cos(longitude)
    y = nu * math.cos(latitude) * math.sin(longitude)
    z = (1.0 - eccentricity_squared) * nu * sin_latitude
    return x, y, z


def _cartesian_to_geodetic(
    x: float,
    y: float,
    z: float,
    *,
    semi_major: float,
    semi_minor: float,
) -> tuple[float, float]:
    eccentricity_squared = 1.0 - (semi_minor * semi_minor) / (
        semi_major * semi_major
    )
    horizontal = math.hypot(x, y)
    longitude = math.atan2(y, x)
    latitude = math.atan2(z, horizontal * (1.0 - eccentricity_squared))
    for _ in range(20):
        previous = latitude
        nu = semi_major / math.sqrt(
            1.0 - eccentricity_squared * math.sin(latitude) ** 2
        )
        latitude = math.atan2(
            z + eccentricity_squared * nu * math.sin(latitude),
            horizontal,
        )
        if abs(latitude - previous) < 1e-12:
            break
    else:
        raise ValueError("Cartesian-to-geodetic conversion did not converge")
    return latitude, longitude
