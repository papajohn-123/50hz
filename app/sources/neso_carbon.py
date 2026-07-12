"""Adapters for NESO's official Great Britain Carbon Intensity API."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Mapping

from app.sources.client import AsyncJSONClient
from app.sources.exceptions import SourceSchemaError
from app.sources.types import (
    AdapterResult,
    CarbonIntensityRecord,
    DataClassification,
    GenerationMixShare,
    ObservationWindow,
)


class _CarbonAdapter:
    source_id = "neso.carbon"
    dataset = "carbon_intensity"
    endpoint: str

    def __init__(self, client: AsyncJSONClient) -> None:
        self.client = client

    async def _fetch_path(
        self,
        path: str,
        window: ObservationWindow,
        *,
        parser: str,
        include_actual: bool = True,
        include_forecast: bool = True,
    ) -> AdapterResult[CarbonIntensityRecord]:
        response = await self.client.get_json(path)
        if parser == "national":
            records, warnings = _parse_national(
                response.payload,
                retrieved_at=response.retrieved_at,
                include_actual=include_actual,
                include_forecast=include_forecast,
            )
        else:
            records, warnings = _parse_regional(
                response.payload,
                retrieved_at=response.retrieved_at,
                include_actual=include_actual,
                include_forecast=include_forecast,
            )
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=path,
            window=window,
            retrieved_at=response.retrieved_at,
            request_url=response.request_url,
            records=records,
            raw_payload=response.payload,
            raw_body=response.raw_body,
            checksum_sha256=response.checksum_sha256,
            content_type=response.content_type,
            warnings=warnings,
        )


class NationalCarbonCurrentAdapter(_CarbonAdapter):
    """Current national half-hour, retaining actual and forecast as separate facts."""

    source_id = "neso.carbon.national.current"
    dataset = "carbon_intensity_national"
    endpoint = "intensity"

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[CarbonIntensityRecord]:
        return await self._fetch_path(self.endpoint, window, parser="national")


class NationalCarbonForecastAdapter(_CarbonAdapter):
    """National carbon forecast over the requested half-hour range."""

    source_id = "neso.carbon.national.forecast"
    dataset = "carbon_intensity_national"
    endpoint = "intensity/{from}/{to}"

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[CarbonIntensityRecord]:
        path = f"intensity/{_path_datetime(window.start)}/{_path_datetime(window.end)}"
        return await self._fetch_path(
            path,
            window,
            parser="national",
            include_actual=False,
            include_forecast=True,
        )


class LondonCarbonIntensityAdapter(_CarbonAdapter):
    """Current regional forecast for NESO Carbon Intensity region 13 (London)."""

    source_id = "neso.carbon.regional.london"
    dataset = "carbon_intensity_regional"
    endpoint = "regional/regionid/13"

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[CarbonIntensityRecord]:
        return await self._fetch_path(self.endpoint, window, parser="regional")


class PostcodeCarbonIntensityAdapter(_CarbonAdapter):
    """Current regional carbon forecast for a UK outward or full postcode."""

    dataset = "carbon_intensity_regional"
    endpoint = "regional/postcode/{postcode}"

    def __init__(self, client: AsyncJSONClient, postcode: str) -> None:
        super().__init__(client)
        self.postcode = normalize_outward_postcode(postcode)
        self.source_id = f"neso.carbon.postcode.{self.postcode}"

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[CarbonIntensityRecord]:
        path = f"regional/postcode/{self.postcode}"
        return await self._fetch_path(path, window, parser="regional")


def normalize_outward_postcode(postcode: str) -> str:
    if not isinstance(postcode, str):
        raise TypeError("postcode must be a string")
    compact = re.sub(r"\s+", "", postcode).upper()
    if compact in {"GIR", "GIR0AA"}:
        return "GIR"
    # A complete postcode has a three-character inward component: digit + two
    # letters.  Removing it yields the outward code accepted by NESO's endpoint.
    full_match = re.fullmatch(r"([A-Z]{1,2}\d[A-Z\d]?)\d[A-Z]{2}", compact)
    if full_match:
        return full_match.group(1)
    if re.fullmatch(r"[A-Z]{1,2}\d[A-Z\d]?", compact):
        return compact
    raise ValueError("postcode must be a valid UK outward or full postcode")


def _parse_national(
    payload: Any,
    *,
    retrieved_at: datetime,
    include_actual: bool,
    include_forecast: bool,
) -> tuple[tuple[CarbonIntensityRecord, ...], tuple[str, ...]]:
    rows = _data_array(payload)
    return _parse_periods(
        rows,
        retrieved_at=retrieved_at,
        scope="national",
        dataset="carbon_intensity_national",
        include_actual=include_actual,
        include_forecast=include_forecast,
    )


def _parse_regional(
    payload: Any,
    *,
    retrieved_at: datetime,
    include_actual: bool,
    include_forecast: bool,
) -> tuple[tuple[CarbonIntensityRecord, ...], tuple[str, ...]]:
    regions = _data_array(payload)
    records: list[CarbonIntensityRecord] = []
    warnings: list[str] = []
    failures = 0
    for index, region in enumerate(regions):
        try:
            region_id = _integer(_required(region, "regionid", "regionId"), "regionid")
            region_name = _optional_string(region, "shortname", "shortName")
            dno_region = _optional_string(region, "dnoregion", "dnoRegion")
            postcode = _optional_string(region, "postcode")
            periods = _required(region, "data")
            if not isinstance(periods, list) or not all(isinstance(item, dict) for item in periods):
                raise TypeError("regional data must be an array of objects")
            scope = f"region-{region_id}"
            if postcode:
                scope += f"-{postcode.upper()}"
            parsed, period_warnings = _parse_periods(
                tuple(periods),
                retrieved_at=retrieved_at,
                scope=scope,
                dataset="carbon_intensity_regional",
                include_actual=include_actual,
                include_forecast=include_forecast,
                region_id=region_id,
                region_name=region_name,
                dno_region=dno_region,
                postcode=postcode.upper() if postcode else None,
            )
            records.extend(parsed)
            warnings.extend(period_warnings)
        except (KeyError, TypeError, ValueError) as exc:
            failures += 1
            warnings.append(f"region {index} ignored: {exc}")
    if regions and not records and failures:
        raise SourceSchemaError("no valid regional carbon intensity records")
    return tuple(records), tuple(warnings)


def _parse_periods(
    rows: tuple[Mapping[str, Any], ...],
    *,
    retrieved_at: datetime,
    scope: str,
    dataset: str,
    include_actual: bool,
    include_forecast: bool,
    region_id: int | None = None,
    region_name: str | None = None,
    dno_region: str | None = None,
    postcode: str | None = None,
) -> tuple[tuple[CarbonIntensityRecord, ...], tuple[str, ...]]:
    records: list[CarbonIntensityRecord] = []
    warnings: list[str] = []
    invalid = 0
    retrieved_at = _datetime_value(retrieved_at, "retrieved_at")
    for index, row in enumerate(rows):
        try:
            period_start = _datetime(_required(row, "from"), "from")
            period_end = _datetime(_required(row, "to"), "to")
            if period_start >= period_end:
                raise ValueError("period start must precede period end")
            intensity = _required(row, "intensity")
            if not isinstance(intensity, dict):
                raise TypeError("intensity must be an object")
            index_name = _optional_string(intensity, "index")
            mix = _generation_mix(row)
            values: list[tuple[DataClassification, Any]] = []
            actual = _optional(intensity, "actual")
            forecast = _optional(intensity, "forecast")
            if include_actual and actual is not None:
                # NESO names this field ``actual`` to distinguish it from its
                # forecast, but carbon intensity remains a modelled estimate.
                values.append((DataClassification.ESTIMATED, actual))
            if include_forecast and forecast is not None:
                values.append((DataClassification.FORECAST, forecast))
            for classification, raw_value in values:
                value = _integer(raw_value, classification.value)
                if value < 0:
                    raise ValueError("carbon intensity cannot be negative")
                records.append(
                    CarbonIntensityRecord(
                        source_key=(
                            f"neso-carbon:{scope}:{_key_time(period_start)}:"
                            f"{classification.value}"
                        ),
                        period_start=period_start,
                        period_end=period_end,
                        retrieved_at=retrieved_at,
                        intensity_g_co2_per_kwh=value,
                        classification=classification,
                        index=index_name,
                        region_id=region_id,
                        region_name=region_name,
                        dno_region=dno_region,
                        postcode=postcode,
                        generation_mix=mix,
                        dataset=dataset,
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            invalid += 1
            warnings.append(f"period {index} ignored: {exc}")
    if rows and not records and invalid:
        raise SourceSchemaError("no valid carbon intensity periods")
    return tuple(records), tuple(warnings)


def _generation_mix(row: Mapping[str, Any]) -> tuple[GenerationMixShare, ...]:
    raw_mix = _optional(row, "generationmix", "generationMix")
    if raw_mix is None:
        return ()
    if not isinstance(raw_mix, list):
        raise TypeError("generationmix must be an array")
    shares: list[GenerationMixShare] = []
    for item in raw_mix:
        if not isinstance(item, dict):
            raise TypeError("generationmix entries must be objects")
        fuel_type = str(_required(item, "fuel")).strip().casefold().replace(" ", "_")
        percent = _number(_required(item, "perc", "percent"), "generationmix perc")
        if not 0 <= percent <= 100:
            raise ValueError("generation mix percentage must be between 0 and 100")
        shares.append(GenerationMixShare(fuel_type=fuel_type, percent=percent))
    return tuple(shares)


def _data_array(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = _optional(payload, "data")
    else:
        raise SourceSchemaError("carbon response must be an object or array")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SourceSchemaError("carbon response data must be an array of objects")
    return tuple(rows)


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


def _optional_string(row: Mapping[str, Any], *names: str) -> str | None:
    value = _optional(row, *names)
    if value is None:
        return None
    return str(value).strip()


def _datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid ISO-8601 datetime") from exc
    return _datetime_value(parsed, field_name)


def _datetime_value(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(UTC)


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


def _path_datetime(value: datetime) -> str:
    return _datetime_value(value, "path datetime").strftime("%Y-%m-%dT%H:%MZ")


def _key_time(value: datetime) -> str:
    return _datetime_value(value, "key datetime").isoformat(timespec="minutes").replace(
        "+00:00",
        "Z",
    )
