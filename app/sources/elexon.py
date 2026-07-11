"""Adapters for Elexon's public Insights Solution API.

The raw dataset endpoints are preferred over visualization summaries.  They keep
publication timestamps and source fields intact, which is important for replay,
correction handling, and user-visible provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from abc import ABC, abstractmethod
from datetime import UTC, date, datetime
from typing import Any, Generic, Mapping, TypeVar

from app.sources.client import AsyncJSONClient
from app.sources.exceptions import SourceSchemaError
from app.sources.types import (
    AdapterResult,
    DataClassification,
    DemandRecord,
    DemandForecastRecord,
    FrequencyRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
    ObservationWindow,
    OutageProfilePoint,
    RemitUnavailabilityRecord,
    SystemWarningRecord,
    WindForecastRecord,
)


RecordT = TypeVar("RecordT")


FUEL_TYPES: Mapping[str, str] = {
    "BIOMASS": "biomass",
    "CCGT": "gas",
    "COAL": "coal",
    "NPSHYD": "hydro",
    "NUCLEAR": "nuclear",
    "OCGT": "gas",
    "OIL": "oil",
    "OTHER": "other",
    "PS": "pumped_storage",
    "SOLAR": "solar",
    "WIND": "wind",
}

# Stable codes from FUELINST, with the display names used by Elexon's dedicated
# interconnector endpoint.  Unknown future links are retained with a deterministic
# fallback ID rather than dropped.
INTERCONNECTOR_NAMES: Mapping[str, str] = {
    "INTELEC": "Eleclink",
    "INTEW": "East-West Interconnector",
    "INTFR": "IFA",
    "INTGRNL": "Greenlink",
    "INTIFA2": "IFA2",
    "INTIRL": "Moyle",
    "INTNED": "BritNed",
    "INTNEM": "Nemo Link",
    "INTNSL": "North Sea Link",
    "INTVKL": "Viking Link",
}

INTERCONNECTOR_NAME_ALIASES: Mapping[str, str] = {
    "eleclink (intelec)": "INTELEC",
    "ireland(east-west)": "INTEW",
    "ireland (east-west)": "INTEW",
    "france(ifa)": "INTFR",
    "france (ifa)": "INTFR",
    "ireland (greenlink)": "INTGRNL",
    "ifa2 (intifa2)": "INTIFA2",
    "northern ireland(moyle)": "INTIRL",
    "northern ireland (moyle)": "INTIRL",
    "netherlands(britned)": "INTNED",
    "netherlands (britned)": "INTNED",
    "belgium (nemolink)": "INTNEM",
    "north sea link (intnsl)": "INTNSL",
    "denmark (viking link)": "INTVKL",
}


class _IgnoreRow(Exception):
    pass


class ElexonAdapter(ABC, Generic[RecordT]):
    source_id = "elexon"
    dataset: str
    endpoint: str
    window_from_parameter = "publishDateTimeFrom"
    window_to_parameter = "publishDateTimeTo"
    include_format_parameter = True

    def __init__(self, client: AsyncJSONClient) -> None:
        self.client = client

    def request_parameters(self, window: ObservationWindow) -> dict[str, str]:
        params = {
            self.window_from_parameter: _format_datetime(window.start),
            self.window_to_parameter: _format_datetime(window.end),
        }
        if self.include_format_parameter:
            params["format"] = "json"
        return params

    async def fetch(self, window: ObservationWindow) -> AdapterResult[RecordT]:
        response = await self.client.get_json(
            self.endpoint,
            params=self.request_parameters(window),
        )
        records, warnings = self.parse(
            response.payload,
            retrieved_at=response.retrieved_at,
        )
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=response.retrieved_at,
            request_url=response.request_url,
            records=records,
            raw_payload=response.payload,
            raw_body=response.raw_body,
            checksum_sha256=response.checksum_sha256,
            content_type=response.content_type,
            metadata=_extract_metadata(response.payload),
            warnings=warnings,
        )

    def parse(
        self,
        payload: Any,
        *,
        retrieved_at: datetime,
    ) -> tuple[tuple[RecordT, ...], tuple[str, ...]]:
        rows = _extract_rows(payload)
        records: list[RecordT] = []
        warnings: list[str] = []
        ignored_count = 0
        invalid_messages: list[str] = []

        for index, row in enumerate(rows):
            try:
                record = self._parse_row(row, retrieved_at=retrieved_at)
            except _IgnoreRow:
                ignored_count += 1
                continue
            except (KeyError, TypeError, ValueError) as exc:
                invalid_messages.append(f"row {index}: {exc}")
                continue
            records.append(record)
            warning = self._record_warning(record)
            if warning:
                warnings.append(warning)

        if ignored_count:
            warnings.append(f"ignored {ignored_count} out-of-scope row(s)")
        if invalid_messages:
            warnings.append(
                f"ignored {len(invalid_messages)} invalid row(s): "
                + "; ".join(invalid_messages[:3])
            )
        if rows and not records and invalid_messages:
            raise SourceSchemaError(
                f"no valid {self.dataset} records; " + "; ".join(invalid_messages[:3])
            )

        return tuple(records), tuple(warnings)

    @abstractmethod
    def _parse_row(self, row: Mapping[str, Any], *, retrieved_at: datetime) -> RecordT:
        raise NotImplementedError

    def _record_warning(self, record: RecordT) -> str | None:
        return None


class FuelInstGenerationAdapter(ElexonAdapter[GenerationRecord]):
    """Instantaneous generation by fuel, excluding interconnector pseudo-fuels."""

    source_id = "elexon.fuelinst"
    dataset = "FUELINST"
    endpoint = "datasets/FUELINST"

    def _parse_row(
        self,
        row: Mapping[str, Any],
        *,
        retrieved_at: datetime,
    ) -> GenerationRecord:
        fuel_code = str(_required(row, "fuelType", "fuel_type")).strip().upper()
        if fuel_code.startswith("INT"):
            raise _IgnoreRow
        observed_at = _datetime(_required(row, "startTime", "start_time"), "startTime")
        published_at = _datetime(
            _required(row, "publishTime", "publish_time"),
            "publishTime",
        )
        settlement_date, settlement_period = _settlement(row)
        return GenerationRecord(
            source_key=f"elexon:FUELINST:{_key_time(observed_at)}:{fuel_code}",
            observed_at=observed_at,
            published_at=published_at,
            retrieved_at=_aware_utc(retrieved_at, "retrieved_at"),
            fuel_code=fuel_code,
            fuel_type=FUEL_TYPES.get(fuel_code, "unknown"),
            generation_mw=_number(_required(row, "generation", "generationMw"), "generation"),
            settlement_date=settlement_date,
            settlement_period=settlement_period,
        )

    def _record_warning(self, record: GenerationRecord) -> str | None:
        if record.fuel_type == "unknown":
            return f"unknown FUELINST fuel type retained: {record.fuel_code}"
        return None


class InitialDemandAdapter(ElexonAdapter[DemandRecord]):
    """Initial National Demand outturn, published approximately every 15 minutes."""

    source_id = "elexon.indo"
    dataset = "INDO"
    endpoint = "datasets/INDO"

    def _parse_row(
        self,
        row: Mapping[str, Any],
        *,
        retrieved_at: datetime,
    ) -> DemandRecord:
        observed_at = _datetime(_required(row, "startTime", "start_time"), "startTime")
        published_at = _datetime(
            _required(row, "publishTime", "publish_time"),
            "publishTime",
        )
        demand_mw = _number(_required(row, "demand", "demandMw"), "demand")
        if demand_mw < 0:
            raise ValueError("demand cannot be negative")
        settlement_date, settlement_period = _settlement(row)
        return DemandRecord(
            source_key=f"elexon:INDO:{_key_time(observed_at)}",
            observed_at=observed_at,
            published_at=published_at,
            retrieved_at=_aware_utc(retrieved_at, "retrieved_at"),
            demand_mw=demand_mw,
            settlement_date=settlement_date,
            settlement_period=settlement_period,
        )


class SystemFrequencyAdapter(ElexonAdapter[FrequencyRecord]):
    """System frequency from the optimized official FREQ stream endpoint."""

    source_id = "elexon.freq"
    dataset = "FREQ"
    endpoint = "datasets/FREQ/stream"
    window_from_parameter = "measurementDateTimeFrom"
    window_to_parameter = "measurementDateTimeTo"
    include_format_parameter = False

    def _parse_row(
        self,
        row: Mapping[str, Any],
        *,
        retrieved_at: datetime,
    ) -> FrequencyRecord:
        observed_at = _datetime(
            _required(row, "measurementTime", "measurement_time"),
            "measurementTime",
        )
        frequency_hz = _number(
            _required(row, "frequency", "frequencyHz"),
            "frequency",
        )
        # A value outside this range is source corruption, not a credible GB grid
        # state.  Retaining it would distort the visual pulse and event detector.
        if not 40.0 <= frequency_hz <= 60.0:
            raise ValueError("frequency must be between 40 and 60 Hz")
        raw_publish_time = _optional(row, "publishTime", "publish_time")
        published_at = (
            _datetime(raw_publish_time, "publishTime")
            if raw_publish_time is not None
            else None
        )
        return FrequencyRecord(
            source_key=f"elexon:FREQ:{_key_time(observed_at)}",
            observed_at=observed_at,
            published_at=published_at,
            retrieved_at=_aware_utc(retrieved_at, "retrieved_at"),
            frequency_hz=frequency_hz,
        )


class InterconnectorFlowAdapter(ElexonAdapter[InterconnectorFlowRecord]):
    """Five-minute interconnector flows extracted from raw FUELINST records.

    Elexon also exposes ``generation/outturn/interconnectors``.  That endpoint is
    useful for half-hour history and its row shape is accepted by this parser, but
    the raw FUELINST endpoint is the default because it retains the near-live
    five-minute observations required by the map.
    """

    source_id = "elexon.interconnectors"
    dataset = "FUELINST"
    endpoint = "datasets/FUELINST"

    def _parse_row(
        self,
        row: Mapping[str, Any],
        *,
        retrieved_at: datetime,
    ) -> InterconnectorFlowRecord:
        raw_code = _optional(row, "fuelType", "fuel_type")
        raw_name = _optional(row, "interconnectorName", "interconnector_name")

        if raw_code is not None:
            interconnector_id = str(raw_code).strip().upper()
            if not interconnector_id.startswith("INT"):
                raise _IgnoreRow
            interconnector_name = INTERCONNECTOR_NAMES.get(
                interconnector_id,
                interconnector_id,
            )
        elif raw_name is not None:
            interconnector_name = str(raw_name).strip()
            interconnector_id = _interconnector_id(interconnector_name)
        else:
            raise KeyError("missing fuelType/interconnectorName")

        observed_at = _datetime(_required(row, "startTime", "start_time"), "startTime")
        published_at = _datetime(
            _required(row, "publishTime", "publish_time"),
            "publishTime",
        )
        settlement_date, settlement_period = _settlement(row)
        dataset = str(_optional(row, "dataset") or self.dataset)
        return InterconnectorFlowRecord(
            source_key=f"elexon:interconnector:{_key_time(observed_at)}:{interconnector_id}",
            observed_at=observed_at,
            published_at=published_at,
            retrieved_at=_aware_utc(retrieved_at, "retrieved_at"),
            interconnector_id=interconnector_id,
            interconnector_name=interconnector_name,
            # Elexon FUELINST expresses imports as positive generation and exports
            # as negative.  This is already the internal 50Hz convention.
            flow_mw=_number(_required(row, "generation", "flow", "flowMw"), "generation"),
            settlement_date=settlement_date,
            settlement_period=settlement_period,
            dataset=dataset,
        )


class NationalDemandForecastAdapter(ElexonAdapter[DemandForecastRecord]):
    """Revision-preserving day/day-ahead National Demand forecasts (NDF)."""

    source_id = "elexon.ndf"
    dataset = "NDF"
    endpoint = "datasets/NDF/stream"
    include_format_parameter = False

    def _parse_row(
        self,
        row: Mapping[str, Any],
        *,
        retrieved_at: datetime,
    ) -> DemandForecastRecord:
        forecast_for = _datetime(_required(row, "startTime", "start_time"), "startTime")
        published_at = _datetime(
            _required(row, "publishTime", "publish_time"),
            "publishTime",
        )
        demand_mw = _number(_required(row, "demand", "demandMw"), "demand")
        if demand_mw < 0:
            raise ValueError("forecast demand cannot be negative")
        boundary_value = _optional(row, "boundary")
        boundary = str(boundary_value).strip() if boundary_value is not None else None
        settlement_date, settlement_period = _settlement(row)
        boundary_key = boundary or "national"
        return DemandForecastRecord(
            source_key=(
                f"elexon:NDF:{_key_time(published_at)}:{_key_time(forecast_for)}:"
                f"{boundary_key}"
            ),
            forecast_for=forecast_for,
            published_at=published_at,
            retrieved_at=_aware_utc(retrieved_at, "retrieved_at"),
            demand_mw=demand_mw,
            boundary=boundary,
            settlement_date=settlement_date,
            settlement_period=settlement_period,
        )


class WindGenerationForecastAdapter(ElexonAdapter[WindForecastRecord]):
    """Revision-preserving hourly wind generation forecasts (WINDFOR)."""

    source_id = "elexon.windfor"
    dataset = "WINDFOR"
    endpoint = "datasets/WINDFOR/stream"
    include_format_parameter = False

    def _parse_row(
        self,
        row: Mapping[str, Any],
        *,
        retrieved_at: datetime,
    ) -> WindForecastRecord:
        forecast_for = _datetime(_required(row, "startTime", "start_time"), "startTime")
        published_at = _datetime(
            _required(row, "publishTime", "publish_time"),
            "publishTime",
        )
        generation_mw = _number(
            _required(row, "generation", "generationMw"),
            "generation",
        )
        if generation_mw < 0:
            raise ValueError("forecast wind generation cannot be negative")
        return WindForecastRecord(
            source_key=(
                f"elexon:WINDFOR:{_key_time(published_at)}:{_key_time(forecast_for)}"
            ),
            forecast_for=forecast_for,
            published_at=published_at,
            retrieved_at=_aware_utc(retrieved_at, "retrieved_at"),
            generation_mw=generation_mw,
        )


class SystemWarningsAdapter(ElexonAdapter[SystemWarningRecord]):
    """Reported NESO/Elexon system-warning publications without text inference."""

    source_id = "elexon.syswarn"
    dataset = "SYSWARN"
    endpoint = "system/warnings"

    def _parse_row(
        self,
        row: Mapping[str, Any],
        *,
        retrieved_at: datetime,
    ) -> SystemWarningRecord:
        published_at = _datetime(
            _required(row, "publishTime", "publish_time"),
            "publishTime",
        )
        warning_type = str(_required(row, "warningType", "warning_type")).strip()
        warning_text = str(_required(row, "warningText", "warning_text"))
        if not warning_type:
            raise ValueError("warningType cannot be empty")
        if not warning_text.strip():
            raise ValueError("warningText cannot be empty")
        content_hash = hashlib.sha256(
            f"{warning_type}\0{warning_text}".encode("utf-8")
        ).hexdigest()
        return SystemWarningRecord(
            source_key=(
                f"elexon:SYSWARN:{_key_time(published_at)}:{content_hash[:16]}"
            ),
            published_at=published_at,
            retrieved_at=_aware_utc(retrieved_at, "retrieved_at"),
            warning_type=warning_type,
            warning_text=warning_text,
            content_sha256=content_hash,
        )


class RemitUnavailabilityAdapter:
    """Fetch every published REMIT unavailability revision and then its details."""

    source_id = "elexon.remit.unavailability"
    dataset = "REMIT"
    endpoint = "remit/list/by-publish/stream"
    details_endpoint = "remit"

    def __init__(self, client: AsyncJSONClient, *, batch_size: int = 100) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.client = client
        self.batch_size = batch_size

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[RemitUnavailabilityRecord]:
        listing = await self.client.get_json(
            self.endpoint,
            params={
                "from": _format_datetime(window.start),
                "to": _format_datetime(window.end),
                "messageType": "UnavailabilitiesOfElectricityFacilities",
                # Retaining every publication is essential: later revisions may
                # shorten, cancel, or otherwise materially change an event.
                "latestRevisionOnly": False,
                "profileOnly": False,
            },
        )
        listing_rows = _extract_rows(listing.payload)
        ids: list[int] = []
        listing_warnings: list[str] = []
        for index, row in enumerate(listing_rows):
            try:
                message_id = _integer(_required(row, "id", "messageId"), "id")
                if message_id < 1:
                    raise ValueError("id must be positive")
                ids.append(message_id)
            except (KeyError, TypeError, ValueError) as exc:
                listing_warnings.append(f"listing row {index} ignored: {exc}")
        if listing_rows and not ids:
            raise SourceSchemaError("REMIT listing contained no valid message IDs")

        detail_responses = []
        for offset in range(0, len(ids), self.batch_size):
            detail_responses.append(
                await self.client.get_json(
                    self.details_endpoint,
                    params={
                        "messageId": ids[offset : offset + self.batch_size],
                        "format": "json",
                    },
                )
            )

        detail_rows: list[Mapping[str, Any]] = []
        for response in detail_responses:
            detail_rows.extend(_extract_rows(response.payload))
        retrieved_at = (
            detail_responses[-1].retrieved_at
            if detail_responses
            else listing.retrieved_at
        )
        records, parse_warnings = self.parse(
            detail_rows,
            retrieved_at=retrieved_at,
        )

        returned_ids = {record.message_id for record in records}
        missing_ids = sorted(set(ids) - returned_ids)
        missing_warning = (
            (f"detail response omitted {len(missing_ids)} listed message(s)",)
            if missing_ids
            else ()
        )
        raw_payload = {
            "listing": listing.payload,
            "details": [response.payload for response in detail_responses],
        }
        raw_body = canonical_payload_bytes(raw_payload)
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=retrieved_at,
            request_url=listing.request_url,
            records=records,
            raw_payload=raw_payload,
            raw_body=raw_body,
            checksum_sha256=hashlib.sha256(raw_body).hexdigest(),
            content_type="application/json",
            metadata={
                "listingChecksum": listing.checksum_sha256,
                "detailChecksums": [
                    response.checksum_sha256 for response in detail_responses
                ],
                "detailRequestUrls": [response.request_url for response in detail_responses],
            },
            warnings=tuple(listing_warnings) + parse_warnings + missing_warning,
        )

    def parse(
        self,
        rows_or_payload: Any,
        *,
        retrieved_at: datetime,
    ) -> tuple[tuple[RemitUnavailabilityRecord, ...], tuple[str, ...]]:
        rows = _extract_rows(rows_or_payload)
        records: list[RemitUnavailabilityRecord] = []
        warnings: list[str] = []
        invalid = 0
        ignored = 0
        for index, row in enumerate(rows):
            try:
                message_type = str(_required(row, "messageType", "message_type"))
                if message_type.casefold() != "unavailabilitiesofelectricityfacilities".casefold():
                    ignored += 1
                    continue
                records.append(_parse_remit_unavailability(row, retrieved_at=retrieved_at))
            except (KeyError, TypeError, ValueError) as exc:
                invalid += 1
                warnings.append(f"detail row {index} ignored: {exc}")
        if ignored:
            warnings.append(f"ignored {ignored} non-unavailability REMIT message(s)")
        if rows and not records and invalid:
            raise SourceSchemaError("no valid REMIT unavailability details")
        return tuple(records), tuple(warnings)


# Short aliases for callers that prefer the upstream dataset codes.
FuelInstAdapter = FuelInstGenerationAdapter
IndoAdapter = InitialDemandAdapter
FreqAdapter = SystemFrequencyAdapter
NdfAdapter = NationalDemandForecastAdapter
WindForAdapter = WindGenerationForecastAdapter


def _extract_rows(payload: Any) -> tuple[Mapping[str, Any], ...]:
    candidate = payload
    for _ in range(3):
        if isinstance(candidate, list):
            rows = candidate
            break
        if not isinstance(candidate, dict):
            raise SourceSchemaError("expected a JSON object or array")

        matching_key = next(
            (
                key
                for key in candidate
                if str(key).casefold() in {"data", "records", "items", "results"}
            ),
            None,
        )
        if matching_key is None:
            # Optimized endpoints occasionally return one bare record rather than
            # a list.  A dataset marker makes that interpretation unambiguous.
            if any(str(key).casefold() == "dataset" for key in candidate):
                rows = [candidate]
                break
            raise SourceSchemaError("response has no data/records array")
        candidate = candidate[matching_key]
    else:
        raise SourceSchemaError("response envelope is nested too deeply")

    if not isinstance(rows, list):
        raise SourceSchemaError("response records must be an array")
    if not all(isinstance(row, dict) for row in rows):
        raise SourceSchemaError("every response record must be an object")
    return tuple(rows)


def _extract_metadata(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key, value in payload.items():
        if str(key).casefold() == "metadata" and isinstance(value, dict):
            return value
    return {}


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


def _datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO-8601 datetime") from exc
    return _aware_utc(parsed, field_name)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(UTC)


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


def _integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise TypeError(f"{field_name} must be an integer")
    return result


def _optional_number(row: Mapping[str, Any], *names: str) -> float | None:
    value = _optional(row, *names)
    return _number(value, names[0]) if value is not None else None


def _optional_string(row: Mapping[str, Any], *names: str) -> str | None:
    value = _optional(row, *names)
    return str(value) if value is not None else None


def _optional_datetime(row: Mapping[str, Any], *names: str) -> datetime | None:
    value = _optional(row, *names)
    return _datetime(value, names[0]) if value is not None else None


def _parse_remit_unavailability(
    row: Mapping[str, Any],
    *,
    retrieved_at: datetime,
) -> RemitUnavailabilityRecord:
    mrid = str(_required(row, "mrid", "mRID")).strip()
    if not mrid:
        raise ValueError("mrid cannot be empty")
    revision_number = _integer(
        _required(row, "revisionNumber", "revision_number"),
        "revisionNumber",
    )
    if revision_number < 1:
        raise ValueError("revisionNumber must be positive")
    message_id = _integer(_required(row, "id", "messageId"), "id")
    if message_id < 1:
        raise ValueError("id must be positive")
    published_at = _datetime(
        _required(row, "publishTime", "publish_time"),
        "publishTime",
    )
    created_at = _datetime(
        _required(row, "createdTime", "created_time"),
        "createdTime",
    )
    event_start = _datetime(
        _required(row, "eventStartTime", "event_start_time"),
        "eventStartTime",
    )
    event_end = _optional_datetime(row, "eventEndTime", "event_end_time")
    if event_end is not None and event_end < event_start:
        raise ValueError("eventEndTime cannot precede eventStartTime")
    return RemitUnavailabilityRecord(
        source_key=f"elexon:REMIT:{mrid}:r{revision_number}",
        mrid=mrid,
        revision_number=revision_number,
        message_id=message_id,
        published_at=published_at,
        created_at=created_at,
        retrieved_at=_aware_utc(retrieved_at, "retrieved_at"),
        event_start=event_start,
        event_end=event_end,
        message_heading=_optional_string(row, "messageHeading", "message_heading"),
        event_type=_optional_string(row, "eventType", "event_type"),
        unavailability_type=_optional_string(
            row,
            "unavailabilityType",
            "unavailability_type",
        ),
        event_status=_optional_string(row, "eventStatus", "event_status"),
        participant_id=_optional_string(row, "participantId", "participant_id"),
        asset_id=_optional_string(row, "assetId", "asset_id"),
        asset_type=_optional_string(row, "assetType", "asset_type"),
        affected_unit=_optional_string(row, "affectedUnit", "affected_unit"),
        affected_unit_eic=_optional_string(
            row,
            "affectedUnitEIC",
            "affected_unit_eic",
        ),
        affected_area=_optional_string(row, "affectedArea", "affected_area"),
        bidding_zone=_optional_string(row, "biddingZone", "bidding_zone"),
        fuel_type=_optional_string(row, "fuelType", "fuel_type"),
        normal_capacity_mw=_optional_number(row, "normalCapacity", "normal_capacity"),
        available_capacity_mw=_optional_number(
            row,
            "availableCapacity",
            "available_capacity",
        ),
        unavailable_capacity_mw=_optional_number(
            row,
            "unavailableCapacity",
            "unavailable_capacity",
        ),
        duration_uncertainty=_optional_string(
            row,
            "durationUncertainty",
            "duration_uncertainty",
        ),
        reported_cause=_optional_string(row, "cause"),
        reported_related_information=_optional_string(
            row,
            "relatedInformation",
            "related_information",
        ),
        outage_profile=_outage_profile(row),
        classification=DataClassification.REPORTED,
    )


def _outage_profile(row: Mapping[str, Any]) -> tuple[OutageProfilePoint, ...]:
    raw_profile = _optional(row, "outageProfile", "outage_profile")
    if raw_profile is None:
        return ()
    if not isinstance(raw_profile, list):
        raise TypeError("outageProfile must be an array")
    profile: list[OutageProfilePoint] = []
    for item in raw_profile:
        if not isinstance(item, dict):
            raise TypeError("outageProfile entries must be objects")
        start = _datetime(_required(item, "startTime", "start_time"), "outage startTime")
        end = _datetime(_required(item, "endTime", "end_time"), "outage endTime")
        if start > end:
            raise ValueError("outage profile start cannot follow end")
        profile.append(
            OutageProfilePoint(
                start=start,
                end=end,
                available_capacity_mw=_number(
                    _required(item, "capacity"),
                    "outage capacity",
                ),
            )
        )
    return tuple(profile)


def _settlement(row: Mapping[str, Any]) -> tuple[date | None, int | None]:
    raw_date = _optional(row, "settlementDate", "settlement_date")
    raw_period = _optional(row, "settlementPeriod", "settlement_period")
    settlement_date = None
    settlement_period = None
    if raw_date is not None:
        try:
            settlement_date = date.fromisoformat(str(raw_date))
        except ValueError as exc:
            raise ValueError("settlementDate is not a valid date") from exc
    if raw_period is not None:
        if isinstance(raw_period, bool):
            raise TypeError("settlementPeriod must be an integer")
        try:
            settlement_period = int(raw_period)
        except (TypeError, ValueError) as exc:
            raise TypeError("settlementPeriod must be an integer") from exc
        if not 1 <= settlement_period <= 50:
            raise ValueError("settlementPeriod must be between 1 and 50")
    return settlement_date, settlement_period


def _interconnector_id(name: str) -> str:
    alias = INTERCONNECTOR_NAME_ALIASES.get(name.casefold())
    if alias:
        return alias
    parenthetical_codes = re.findall(r"\((INT[A-Z0-9]+)\)", name.upper())
    if parenthetical_codes:
        return parenthetical_codes[-1]
    slug = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return f"UNMAPPED_{slug or 'INTERCONNECTOR'}"


def _format_datetime(value: datetime) -> str:
    return _aware_utc(value, "query datetime").isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def _key_time(value: datetime) -> str:
    return _format_datetime(value)


def canonical_payload_bytes(payload: Any) -> bytes:
    """Canonical JSON helper for deterministic fixture/replay checksums."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
