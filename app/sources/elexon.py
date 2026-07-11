"""Adapters for Elexon's public Insights Solution API.

The raw dataset endpoints are preferred over visualization summaries.  They keep
publication timestamps and source fields intact, which is important for replay,
correction handling, and user-visible provenance.
"""

from __future__ import annotations

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
    DemandRecord,
    FrequencyRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
    ObservationWindow,
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


# Short aliases for callers that prefer the upstream dataset codes.
FuelInstAdapter = FuelInstGenerationAdapter
IndoAdapter = InitialDemandAdapter
FreqAdapter = SystemFrequencyAdapter


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
