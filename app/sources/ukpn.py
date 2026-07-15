"""Privacy-reducing adapter for UK Power Networks' Live Faults feed."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.sources.client import AsyncJSONClient
from app.sources.exceptions import SourceSchemaError
from app.sources.types import (
    AdapterResult,
    DistributionIncidentRecord,
    ObservationWindow,
    as_utc,
)


DEFAULT_UKPN_BASE_URL = "https://ukpowernetworks.opendatasoft.com/"
UKPN_LIVE_FAULTS_ENDPOINT = (
    "api/explore/v2.1/catalog/datasets/ukpn-live-faults/records"
)
UKPN_DATASET_URL = (
    "https://ukpowernetworks.opendatasoft.com/explore/dataset/ukpn-live-faults/"
)
PAGE_SIZE = 100
MAX_UPSTREAM_RECORDS = 500

_LONDON = ZoneInfo("Europe/London")
_OUTWARD_RE = re.compile(r"[A-Z]{1,2}\d[A-Z\d]?")
_SECTOR_RE = re.compile(r"([A-Z]{1,2}\d[A-Z\d]?)(\d)")
_FULL_POSTCODE_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{1,2}\d[A-Z\d]?)\s*(\d)[A-Z]{2}(?![A-Z0-9])",
    re.IGNORECASE,
)
_PRIVATE_FIELD_MARKERS = frozenset(
    {
        "fullpostcode",
        "fullpostcodedata",
        "postcodefull",
        "postcodedetail",
    }
)
_VALID_STATUSES = frozenset({"planned", "unplanned", "restored"})


class UKPNLiveFaultsAdapter:
    """Fetch a bounded current snapshot and discard exact postcodes immediately."""

    source_id = "ukpn.live_faults"
    dataset = "LIVE_FAULTS"
    endpoint = UKPN_LIVE_FAULTS_ENDPOINT

    def __init__(
        self,
        client: AsyncJSONClient,
        *,
        max_records: int = MAX_UPSTREAM_RECORDS,
    ) -> None:
        if not 1 <= max_records <= MAX_UPSTREAM_RECORDS:
            raise ValueError(
                f"max_records must be between 1 and {MAX_UPSTREAM_RECORDS}"
            )
        self.client = client
        self.max_records = max_records

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[DistributionIncidentRecord]:
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        total_count: int | None = None
        first_url: str | None = None
        content_type: str | None = None
        retrieved_at: datetime | None = None
        offset = 0

        while offset < self.max_records:
            limit = min(PAGE_SIZE, self.max_records - offset)
            response = await self.client.get_json(
                self.endpoint,
                params={
                    "limit": limit,
                    "offset": offset,
                    "order_by": "incidentreference",
                },
            )
            sanitized = sanitize_ukpn_payload(response.payload)
            page_total, page_rows = _page(sanitized)
            if offset == 0 and page_total > 0 and not page_rows:
                raise SourceSchemaError(
                    "UKPN returned an empty first page for a non-empty snapshot"
                )
            if total_count is None:
                total_count = page_total
            elif page_total != total_count:
                warnings.append(
                    "upstream total changed while the paginated snapshot was read"
                )
                total_count = page_total

            rows.extend(page_rows)
            first_url = first_url or response.request_url
            content_type = content_type or response.content_type
            retrieved_at = max(
                retrieved_at or response.retrieved_at,
                response.retrieved_at,
            )

            offset += len(page_rows)
            if not page_rows or offset >= total_count:
                break

        assert total_count is not None
        assert first_url is not None
        assert retrieved_at is not None
        if total_count > self.max_records:
            warnings.append(
                f"upstream snapshot truncated at {self.max_records} of {total_count} records"
            )
        elif len(rows) < total_count:
            raise SourceSchemaError(
                "UKPN pagination ended before the declared record count"
            )

        raw_payload = {
            "total_count": total_count,
            "results": rows,
        }
        if contains_full_postcode(raw_payload):  # defense in depth
            raise SourceSchemaError(
                "privacy reduction failed: a full postcode remained in UKPN payload"
            )
        raw_body = _canonical_json(raw_payload)
        records, parse_warnings = self.parse(
            raw_payload,
            retrieved_at=retrieved_at,
        )
        warnings.extend(parse_warnings)
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=retrieved_at,
            request_url=first_url,
            records=records,
            raw_payload=raw_payload,
            raw_body=raw_body,
            checksum_sha256=hashlib.sha256(raw_body).hexdigest(),
            content_type=content_type,
            metadata={
                "upstreamTotalCount": total_count,
                "retainedRecordCount": len(rows),
                "privacyReduction": (
                    "full-postcode fields removed; full postcodes reduced to sectors"
                ),
                "geopointPrecision": "upstream aggregated incident point",
                "sourceTimeZone": "Europe/London",
            },
            warnings=tuple(warnings),
        )

    def parse(
        self,
        payload: Any,
        *,
        retrieved_at: datetime,
    ) -> tuple[tuple[DistributionIncidentRecord, ...], tuple[str, ...]]:
        _, rows = _page(payload)
        by_reference: dict[str, DistributionIncidentRecord] = {}
        warnings: list[str] = []
        invalid: list[str] = []
        duplicate_count = 0
        aggregate_count = 0

        for index, row in enumerate(rows):
            if str(row.get("powercuttype", "")).strip().lower() == "multiple":
                # UKPN also emits map-cluster rows which only reference the
                # separately published component incidents. Retaining them
                # would double count incidents and customers.
                aggregate_count += 1
                continue
            try:
                record = _parse_incident(row, retrieved_at=as_utc(retrieved_at))
            except (KeyError, TypeError, ValueError) as error:
                invalid.append(f"row {index}: {error}")
                continue
            if record.incident_reference in by_reference:
                duplicate_count += 1
            existing = by_reference.get(record.incident_reference)
            if existing is None or record.observed_at >= existing.observed_at:
                by_reference[record.incident_reference] = record

        if rows and not by_reference:
            raise SourceSchemaError(
                "no valid UKPN live-fault records; " + "; ".join(invalid[:3])
            )
        if invalid:
            warnings.append(
                f"ignored {len(invalid)} malformed UKPN row(s): "
                + "; ".join(invalid[:3])
            )
        if duplicate_count:
            warnings.append(
                f"collapsed {duplicate_count} duplicate incident reference(s)"
            )
        if aggregate_count:
            warnings.append(
                f"ignored {aggregate_count} UKPN aggregate map row(s)"
            )
        return (
            tuple(
                sorted(
                    by_reference.values(),
                    key=lambda record: record.incident_reference,
                )
            ),
            tuple(warnings),
        )


def ukpn_authorization_headers(api_key: str | None) -> dict[str, str]:
    """Return the documented header form without putting a key in a URL."""

    if api_key is None or not api_key.strip():
        return {}
    return {"Authorization": f"Apikey {api_key.strip()}"}


def normalize_outward_code(value: str) -> str:
    """Normalize an outward code and intentionally reject full postcodes."""

    if not isinstance(value, str):
        raise TypeError("outward code must be a string")
    compact = re.sub(r"\s+", "", value).upper()
    if compact == "GIR":
        return compact
    if _OUTWARD_RE.fullmatch(compact):
        return compact
    raise ValueError("enter a UK outward code only, for example SW1A")


def sanitize_ukpn_payload(value: Any) -> Any:
    """Remove private fields and reduce any stray full postcode to its sector."""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if any(marker in normalized_key for marker in _PRIVATE_FIELD_MARKERS):
                continue
            sanitized[str(key)] = sanitize_ukpn_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_ukpn_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_ukpn_payload(item) for item in value]
    if isinstance(value, str):
        return _FULL_POSTCODE_RE.sub(
            lambda match: f"{match.group(1).upper()} {match.group(2)}",
            value,
        )
    return value


def contains_full_postcode(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            contains_full_postcode(key) or contains_full_postcode(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_full_postcode(item) for item in value)
    return isinstance(value, str) and _FULL_POSTCODE_RE.search(value) is not None


def _page(payload: Any) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(payload, Mapping):
        raise SourceSchemaError("UKPN response must be an object")
    raw_total = payload.get("total_count")
    if isinstance(raw_total, bool):
        raise SourceSchemaError("UKPN total_count must be a non-negative integer")
    try:
        total_count = int(raw_total)
    except (TypeError, ValueError) as error:
        raise SourceSchemaError(
            "UKPN total_count must be a non-negative integer"
        ) from error
    if total_count < 0:
        raise SourceSchemaError("UKPN total_count must be a non-negative integer")
    results = payload.get("results")
    if not isinstance(results, list) or not all(
        isinstance(row, dict) for row in results
    ):
        raise SourceSchemaError("UKPN results must be an array of objects")
    return total_count, results


def _parse_incident(
    row: Mapping[str, Any],
    *,
    retrieved_at: datetime,
) -> DistributionIncidentRecord:
    if contains_full_postcode(row):
        raise ValueError("row contains an exact postcode after privacy reduction")
    reference = _required_text(row, "incidentreference", limit=120).upper()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,119}", reference) is None:
        raise ValueError("incidentreference is malformed")
    status = _required_text(row, "powercuttype", limit=32).lower()
    if status not in _VALID_STATUSES:
        raise ValueError("powercuttype is not planned, unplanned, or restored")

    source_created_at = _optional_datetime(row.get("creationdatetime"))
    received_at = _optional_datetime(row.get("receiveddate"))
    restored_at = _optional_datetime(row.get("restoreddatetime"))
    planned_at = _optional_datetime(row.get("planneddate"))
    estimated_restoration_at = _optional_datetime(
        row.get("estimatedrestorationdate")
    )
    observed_candidates = tuple(
        value
        for value in (source_created_at, received_at, restored_at)
        if value is not None
    )
    observed_at = max(observed_candidates, default=retrieved_at)
    incident_start = (
        planned_at
        if status == "planned"
        else received_at or source_created_at
    )

    sectors, outward_codes = _postcode_geographies(row.get("postcodesaffected"))
    latitude, longitude = _geopoint(row.get("geopoint"))
    geography_precision = (
        "aggregated_incident_point"
        if latitude is not None and longitude is not None
        else "postcode_sector"
        if sectors
        else "operating_zone"
    )
    current_customers = _nonnegative_integer(
        row.get("nocustomeraffected"),
        "nocustomeraffected",
        default=0,
    )
    planned_customers = _nonnegative_integer(
        row.get("noplannedcustomers"),
        "noplannedcustomers",
        default=0,
    )
    customers_affected = (
        max(current_customers, planned_customers)
        if status == "planned"
        else current_customers
    )
    calls_reported = _nonnegative_integer(
        row.get("nocallsreported"),
        "nocallsreported",
        default=0,
    )
    official_summary = _optional_text(
        row.get("mainmessage") or row.get("message"),
        limit=20_000,
    )
    official_details = _optional_text(
        row.get("incidentcategorycustomerfriendlydescription")
        or row.get("incidentdescription")
        or (row.get("plannedincidentreason") if status == "planned" else None),
        limit=20_000,
    )
    restoration_window_text = _optional_text(
        row.get("incidenttypetbcestimatedfriendlydescription"),
        limit=500,
    )
    status_id = _optional_integer(row.get("statusid"), "statusid")
    incident_category = _optional_text(row.get("incidentcategory"), limit=64)
    operating_zone = _optional_text(row.get("operatingzone"), limit=160)

    factual = {
        "incidentReference": reference,
        "status": status,
        "statusID": status_id,
        "sourceCreatedAt": _iso(source_created_at),
        "observedAt": _iso(observed_at),
        "incidentStart": _iso(incident_start),
        "restoredAt": _iso(restored_at),
        "estimatedRestorationAt": _iso(estimated_restoration_at),
        "customersAffected": customers_affected,
        "callsReported": calls_reported,
        "postcodeSectors": sectors,
        "outwardCodes": outward_codes,
        "latitude": latitude,
        "longitude": longitude,
        "geographyPrecision": geography_precision,
        "operatingZone": operating_zone,
        "officialSummary": official_summary,
        "officialDetails": official_details,
        "restorationWindowText": restoration_window_text,
        "incidentCategory": incident_category,
    }
    content_sha256 = hashlib.sha256(_canonical_json(factual)).hexdigest()
    return DistributionIncidentRecord(
        source_key=f"ukpn:LIVE_FAULTS:{reference}",
        incident_reference=reference,
        status=status,
        status_id=status_id,
        source_created_at=source_created_at,
        observed_at=observed_at,
        retrieved_at=retrieved_at,
        incident_start=incident_start,
        restored_at=restored_at,
        estimated_restoration_at=estimated_restoration_at,
        customers_affected=customers_affected,
        calls_reported=calls_reported,
        postcode_sectors=sectors,
        outward_codes=outward_codes,
        latitude=latitude,
        longitude=longitude,
        geography_precision=geography_precision,
        operating_zone=operating_zone,
        official_summary=official_summary,
        official_details=official_details,
        restoration_window_text=restoration_window_text,
        incident_category=incident_category,
        content_sha256=content_sha256,
    )


def _postcode_geographies(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if value is None:
        return (), ()
    if not isinstance(value, str):
        raise TypeError("postcodesaffected must be text")
    sectors: set[str] = set()
    outward_codes: set[str] = set()
    for raw in re.split(r"[;,]", value):
        compact = re.sub(r"\s+", "", raw).upper()
        if not compact:
            continue
        full = re.fullmatch(r"([A-Z]{1,2}\d[A-Z\d]?)(\d)[A-Z]{2}", compact)
        sector = _SECTOR_RE.fullmatch(compact)
        outward = _OUTWARD_RE.fullmatch(compact)
        if full:
            outward_code, sector_digit = full.groups()
            sectors.add(f"{outward_code} {sector_digit}")
            outward_codes.add(outward_code)
        elif sector:
            outward_code, sector_digit = sector.groups()
            sectors.add(f"{outward_code} {sector_digit}")
            outward_codes.add(outward_code)
        elif outward:
            outward_codes.add(compact)
        else:
            raise ValueError("postcodesaffected contains malformed geography")
    return tuple(sorted(sectors)), tuple(sorted(outward_codes))


def _geopoint(value: Any) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        raise TypeError("geopoint must be an object")
    try:
        latitude = float(value["lat"])
        longitude = float(value["lon"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("geopoint requires numeric lat and lon") from error
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("geopoint coordinates are out of bounds")
    return round(latitude, 5), round(longitude, 5)


def _optional_datetime(value: Any) -> datetime | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise TypeError("source datetime must be text")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("source datetime is not ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=_LONDON)
    return parsed.astimezone(UTC)


def _required_text(row: Mapping[str, Any], key: str, *, limit: int) -> str:
    if key not in row:
        raise KeyError(key)
    value = _optional_text(row[key], limit=limit)
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _optional_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise TypeError("source text field must be text")
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _nonnegative_integer(value: Any, name: str, *, default: int) -> int:
    parsed = _optional_integer(value, name)
    if parsed is None:
        return default
    if parsed < 0:
        raise ValueError(f"{name} cannot be negative")
    return parsed


def _optional_integer(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{name} must be an integer") from error
    return parsed


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
