"""Parsers for Elexon BM-unit reference, plan, and settled-metered datasets."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any, Callable, Mapping, TypeVar

from app.assets.models import (
    AssetReference,
    EvidenceKind,
    GeoPoint,
    ParsedBatch,
    PlannedProfile,
    PlannedProfileSegment,
    Provenance,
    SettledMeteredEnergy,
)
from app.domain.settlement import settlement_period_at


BM_UNIT_REFERENCE_ENDPOINT = "/reference/bmunits/all"
PHYSICAL_NOTIFICATION_ENDPOINT = "/datasets/PN"
B1610_ENDPOINT = "/datasets/B1610"

RecordT = TypeVar("RecordT")


class AssetSchemaError(ValueError):
    """Raised when an upstream payload has no usable in-scope records."""


def parse_bm_unit_references(
    payload: Any,
    *,
    retrieved_at: datetime,
    endpoint: str = BM_UNIT_REFERENCE_ENDPOINT,
) -> ParsedBatch[AssetReference]:
    provenance = Provenance(
        source_id="elexon",
        dataset="BM_UNIT_REFERENCE",
        endpoint=endpoint,
        retrieved_at=retrieved_at,
        evidence_kind=EvidenceKind.REFERENCE,
    )
    return _parse_batch(
        payload,
        dataset="BM unit reference",
        parser=lambda row: _parse_bm_unit_reference(row, provenance=provenance),
    )


def parse_physical_notifications(
    payload: Any,
    *,
    retrieved_at: datetime,
    endpoint: str = PHYSICAL_NOTIFICATION_ENDPOINT,
) -> ParsedBatch[PlannedProfileSegment]:
    provenance = Provenance(
        source_id="elexon",
        dataset="PN",
        endpoint=endpoint,
        retrieved_at=retrieved_at,
        evidence_kind=EvidenceKind.REPORTED_PLAN,
    )
    return _parse_batch(
        payload,
        dataset="PN",
        parser=lambda row: _parse_physical_notification(row, provenance=provenance),
    )


def parse_b1610_metered_energy(
    payload: Any,
    *,
    retrieved_at: datetime,
    endpoint: str = B1610_ENDPOINT,
) -> ParsedBatch[SettledMeteredEnergy]:
    provenance = Provenance(
        source_id="elexon",
        dataset="B1610",
        endpoint=endpoint,
        retrieved_at=retrieved_at,
        evidence_kind=EvidenceKind.SETTLED_METERED,
    )
    return _parse_batch(
        payload,
        dataset="B1610",
        parser=lambda row: _parse_b1610(row, provenance=provenance),
    )


def consolidate_physical_notifications(
    segments: tuple[PlannedProfileSegment, ...] | list[PlannedProfileSegment],
) -> tuple[PlannedProfile, ...]:
    """Consolidate one or more PN response snapshots into profiles.

    If callers accidentally pass repeated snapshots, only the most recently
    retrieved snapshot for an asset/period is used.  Exact duplicate segments
    within that snapshot are collapsed.  Conflicting overlaps fail closed rather
    than inventing a profile.
    """

    grouped: dict[
        tuple[str, str | None, date, int],
        list[PlannedProfileSegment],
    ] = defaultdict(list)
    for segment in segments:
        key = (
            segment.asset_id,
            segment.source_asset_id,
            segment.settlement_date,
            segment.settlement_period,
        )
        grouped[key].append(segment)

    profiles: list[PlannedProfile] = []
    for key, candidates in grouped.items():
        latest_retrieval = max(item.provenance.retrieved_at for item in candidates)
        latest = [
            item for item in candidates if item.provenance.retrieved_at == latest_retrieval
        ]
        unique: dict[
            tuple[datetime, datetime, float, float],
            PlannedProfileSegment,
        ] = {}
        for item in latest:
            unique[
                (item.start, item.end, item.level_from_mw, item.level_to_mw)
            ] = item
        ordered = tuple(sorted(unique.values(), key=lambda item: (item.start, item.end)))
        for previous, following in zip(ordered, ordered[1:], strict=False):
            if following.start < previous.end:
                raise AssetSchemaError(
                    "conflicting PN segments overlap for "
                    f"{key[1]} on {key[2]} period {key[3]}"
                )
        profiles.append(
            PlannedProfile(
                asset_id=key[0],
                source_asset_id=key[1],
                settlement_date=key[2],
                settlement_period=key[3],
                segments=ordered,
            )
        )
    return tuple(
        sorted(
            profiles,
            key=lambda profile: (
                profile.settlement_date,
                profile.settlement_period,
                profile.source_asset_id or "",
            ),
        )
    )


def _parse_batch(
    payload: Any,
    *,
    dataset: str,
    parser: Callable[[Mapping[str, Any]], RecordT],
) -> ParsedBatch[RecordT]:
    rows = _extract_rows(payload)
    records: list[RecordT] = []
    invalid: list[str] = []
    for index, row in enumerate(rows):
        try:
            records.append(parser(row))
        except (KeyError, TypeError, ValueError) as exc:
            invalid.append(f"row {index}: {exc}")
    if rows and not records:
        raise AssetSchemaError(
            f"no valid {dataset} records; " + "; ".join(invalid[:3])
        )
    warnings = (
        (
            f"ignored {len(invalid)} invalid {dataset} row(s): "
            + "; ".join(invalid[:3]),
        )
        if invalid
        else ()
    )
    return ParsedBatch(records=tuple(records), warnings=warnings)


def _parse_bm_unit_reference(
    row: Mapping[str, Any],
    *,
    provenance: Provenance,
) -> AssetReference:
    _require_dataset(row, expected=None)
    latitude = _optional_number(row, "latitude", "lat")
    longitude = _optional_number(row, "longitude", "lon", "lng")
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must either both be present or both be null")
    location = (
        GeoPoint(latitude=latitude, longitude=longitude)
        if latitude is not None and longitude is not None
        else None
    )
    return AssetReference(
        asset_id=_text(_required(row, "nationalGridBmUnit", "nationalGridBmUnitId")),
        source_asset_id=_optional_text(row, "elexonBmUnit", "bmUnit"),
        display_name=_optional_text(row, "bmUnitName"),
        fuel_type=_optional_text(row, "fuelType"),
        lead_party_name=_optional_text(row, "leadPartyName"),
        lead_party_id=_optional_text(row, "leadPartyId"),
        asset_type=_optional_text(row, "bmUnitType"),
        production_or_consumption=_optional_text(
            row,
            "productionOrConsumptionFlag",
        ),
        submits_physical_notifications=_optional_bool(row, "fpnFlag"),
        generation_capacity_mw=_optional_number(row, "generationCapacity"),
        demand_capacity_mw=_optional_number(row, "demandCapacity"),
        gsp_group_id=_optional_text(row, "gspGroupId"),
        gsp_group_name=_optional_text(row, "gspGroupName"),
        interconnector_id=_optional_text(row, "interconnectorId"),
        eic=_optional_text(row, "eic"),
        location=location,
        provenance=provenance,
        transmission_loss_factor=_optional_number(row, "transmissionLossFactor"),
        working_day_credit_assessment_import_capability_mw=_optional_number(
            row,
            "workingDayCreditAssessmentImportCapability",
        ),
        non_working_day_credit_assessment_import_capability_mw=_optional_number(
            row,
            "nonWorkingDayCreditAssessmentImportCapability",
        ),
        working_day_credit_assessment_export_capability_mw=_optional_number(
            row,
            "workingDayCreditAssessmentExportCapability",
        ),
        non_working_day_credit_assessment_export_capability_mw=_optional_number(
            row,
            "nonWorkingDayCreditAssessmentExportCapability",
        ),
        credit_qualifying_status=_optional_bool(row, "creditQualifyingStatus"),
        demand_in_production=_optional_bool(row, "demandInProductionFlag"),
    )


def _parse_physical_notification(
    row: Mapping[str, Any],
    *,
    provenance: Provenance,
) -> PlannedProfileSegment:
    _require_dataset(row, expected="PN")
    settlement_date = _date(_required(row, "settlementDate"), "settlementDate")
    settlement_period = _integer(
        _required(row, "settlementPeriod"),
        "settlementPeriod",
    )
    return PlannedProfileSegment(
        asset_id=_text(
            _required(row, "nationalGridBmUnit", "nationalGridBmUnitId")
        ),
        source_asset_id=_optional_text(row, "bmUnit", "elexonBmUnit"),
        settlement_date=settlement_date,
        settlement_period=settlement_period,
        start=_datetime(_required(row, "timeFrom"), "timeFrom"),
        end=_datetime(_required(row, "timeTo"), "timeTo"),
        level_from_mw=_number(_required(row, "levelFrom"), "levelFrom"),
        level_to_mw=_number(_required(row, "levelTo"), "levelTo"),
        provenance=provenance,
    )


def _parse_b1610(
    row: Mapping[str, Any],
    *,
    provenance: Provenance,
) -> SettledMeteredEnergy:
    _require_dataset(row, expected="B1610")
    settlement_date = _date(_required(row, "settlementDate"), "settlementDate")
    settlement_period = _integer(
        _required(row, "settlementPeriod"),
        "settlementPeriod",
    )
    period = settlement_period_at(settlement_date, settlement_period)
    # The live B1610 stream has historically emitted this UTC field without a
    # trailing offset even though the documentation example includes ``Z``.
    # Settlement date/period remains authoritative; the field is validated as
    # UTC to catch schema or clock drift without applying local-time ambiguity.
    reported_end = _datetime(
        _required(row, "halfHourEndTime"),
        "halfHourEndTime",
        assume_naive_utc=True,
    )
    if reported_end != period.end_utc:
        raise ValueError("halfHourEndTime does not match settlement date/period")
    national_grid_bm_unit = _optional_text(
        row,
        "nationalGridBmUnitId",
        "nationalGridBmUnit",
    )
    elexon_bm_unit = _optional_text(row, "bmUnit", "elexonBmUnit")
    if national_grid_bm_unit is None and elexon_bm_unit is None:
        raise KeyError("missing nationalGridBmUnitId/nationalGridBmUnit and bmUnit")
    canonical_asset_id = national_grid_bm_unit
    if canonical_asset_id is None:
        assert elexon_bm_unit is not None  # guarded above
        canonical_asset_id = f"elexon:{elexon_bm_unit}"
    return SettledMeteredEnergy(
        asset_id=canonical_asset_id,
        source_asset_id=elexon_bm_unit,
        settlement_date=settlement_date,
        settlement_period=settlement_period,
        interval_start=period.start_utc,
        interval_end=period.end_utc,
        energy_mwh=_number(_required(row, "quantity"), "quantity"),
        psr_type=_optional_text(row, "psrType"),
        provenance=provenance,
        national_grid_bm_unit=national_grid_bm_unit,
    )


def _extract_rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    candidate = payload
    if isinstance(candidate, Mapping):
        matching_key = next(
            (
                key
                for key in candidate
                if str(key).casefold() in {"data", "records", "items", "results"}
            ),
            None,
        )
        if matching_key is None:
            raise AssetSchemaError("response has no data/records array")
        candidate = candidate[matching_key]
    if not isinstance(candidate, list):
        raise AssetSchemaError("response records must be an array")
    if not all(isinstance(row, Mapping) for row in candidate):
        raise AssetSchemaError("every response record must be an object")
    return tuple(candidate)


def _required(row: Mapping[str, Any], *names: str) -> Any:
    value = _optional(row, *names)
    if value is None:
        raise KeyError(f"missing {'/'.join(names)}")
    return value


def _optional(row: Mapping[str, Any], *names: str) -> Any | None:
    folded = {str(key).casefold(): value for key, value in row.items()}
    for name in names:
        if name.casefold() in folded:
            return folded[name.casefold()]
    return None


def _text(value: Any) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("identifier cannot be empty")
    return normalized


def _optional_text(row: Mapping[str, Any], *names: str) -> str | None:
    value = _optional(row, *names)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _optional_number(row: Mapping[str, Any], *names: str) -> float | None:
    value = _optional(row, *names)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _number(value, names[0])


def _integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise TypeError(f"{field_name} must be an integer")
    if isinstance(value, str) and str(result) != value.strip():
        raise TypeError(f"{field_name} must be an integer")
    return result


def _date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc


def _datetime(
    value: Any,
    field_name: str,
    *,
    assume_naive_utc: bool = False,
) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not assume_naive_utc:
            raise ValueError(f"{field_name} must include a UTC offset")
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_bool(row: Mapping[str, Any], *names: str) -> bool | None:
    value = _optional(row, *names)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError(f"{names[0]} must be boolean")
    return value


def _require_dataset(row: Mapping[str, Any], *, expected: str | None) -> None:
    if expected is None:
        return
    value = _optional(row, "dataset")
    if value is not None and str(value).strip().casefold() != expected.casefold():
        raise ValueError(f"dataset must be {expected}")
