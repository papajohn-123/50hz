"""Economical official Elexon collectors for BM-unit-level evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, timedelta
from typing import Any, Sequence

from app.assets.elexon import (
    AssetSchemaError,
    consolidate_physical_notifications,
    parse_b1610_metered_energy,
    parse_bm_unit_references,
    parse_physical_notifications,
)
from app.assets.models import (
    AssetReference,
    PlannedProfileSegment,
    SettledMeteredEnergy,
)
from app.domain.settlement import GB_TIME_ZONE, settlement_period_for_instant
from app.sources.client import AsyncJSONClient
from app.sources.types import AdapterResult, ObservationWindow


class BMUnitReferenceAdapter:
    """Fetch Elexon's complete current BM-unit reference catalogue."""

    source_id = "elexon.bm-unit-reference"
    dataset = "BM_UNIT_REFERENCE"
    endpoint = "reference/bmunits/all"

    def __init__(self, client: AsyncJSONClient, *, max_records: int = 10_000) -> None:
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self.client = client
        self.max_records = max_records

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[AssetReference]:
        response = await self.client.get_json(self.endpoint)
        if _payload_record_count(response.payload) > self.max_records:
            raise AssetSchemaError("BM-unit reference snapshot exceeds safety limit")
        batch = parse_bm_unit_references(
            response.payload,
            retrieved_at=response.retrieved_at,
            endpoint=f"/{self.endpoint}",
        )
        if not batch.records:
            raise AssetSchemaError("BM-unit reference snapshot cannot be empty")
        if batch.warnings:
            raise AssetSchemaError("BM-unit reference snapshot was only partially valid")
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=response.retrieved_at,
            request_url=response.request_url,
            records=batch.records,
            raw_payload=response.payload,
            raw_body=response.raw_body,
            checksum_sha256=response.checksum_sha256,
            content_type=response.content_type,
            metadata={"snapshotKind": "complete_reference"},
            warnings=batch.warnings,
        )


class PhysicalNotificationAdapter:
    """Fetch a single current GB settlement-period PN snapshot.

    Omitting ``bm_units`` asks Elexon for all units, but the query remains bounded
    to exactly one settlement period. An explicit unit set is capped so this
    adapter cannot accidentally become an unbounded fan-out collector.
    """

    source_id = "elexon.pn"
    dataset = "PN"
    endpoint = "datasets/PN"

    def __init__(
        self,
        client: AsyncJSONClient,
        *,
        bm_units: Sequence[str] = (),
        max_units: int = 250,
        max_records: int = 20_000,
    ) -> None:
        if max_units <= 0:
            raise ValueError("max_units must be positive")
        if any(not isinstance(unit, str) for unit in bm_units):
            raise TypeError("PN BM-unit filters must be strings")
        normalized = tuple(
            dict.fromkeys(unit.strip() for unit in bm_units if unit.strip())
        )
        if len(normalized) > max_units:
            raise ValueError("PN BM-unit filter exceeds safety limit")
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self.client = client
        self.bm_units = normalized
        self.max_records = max_records

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[PlannedProfileSegment]:
        period = settlement_period_for_instant(window.end)
        params: dict[str, Any] = {
            "settlementDate": period.settlement_date.isoformat(),
            "settlementPeriod": period.period,
            "format": "json",
        }
        if self.bm_units:
            params["bmUnit"] = self.bm_units
        response = await self.client.get_json(self.endpoint, params=params)
        if _payload_record_count(response.payload) > self.max_records:
            raise AssetSchemaError("PN snapshot exceeds safety limit")
        batch = parse_physical_notifications(
            response.payload,
            retrieved_at=response.retrieved_at,
            endpoint=f"/{self.endpoint}",
        )
        if batch.warnings:
            raise AssetSchemaError("PN snapshot was only partially valid")
        consolidate_physical_notifications(batch.records)
        for record in batch.records:
            if (
                record.settlement_date != period.settlement_date
                or record.settlement_period != period.period
            ):
                raise AssetSchemaError("PN response escaped the requested settlement period")
            if self.bm_units and record.source_asset_id not in self.bm_units:
                raise AssetSchemaError("PN response contained an unrequested BM unit")
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=response.retrieved_at,
            request_url=response.request_url,
            records=batch.records,
            raw_payload=response.payload,
            raw_body=response.raw_body,
            checksum_sha256=response.checksum_sha256,
            content_type=response.content_type,
            metadata={
                "snapshotKind": "replace_query_scope",
                "settlementDate": period.settlement_date.isoformat(),
                "settlementPeriod": period.period,
                "bmUnits": list(self.bm_units),
                "allUnits": not self.bm_units,
            },
            warnings=batch.warnings,
        )


class B1610DelayedHistoryAdapter:
    """Fetch bounded delayed B1610 settlement days, never a live window.

    The first lag collects newly available interim-settlement data. The later
    lag revisits one older day so subsequent settlement-run corrections become
    immutable local revisions without downloading an unbounded history range.
    """

    source_id = "elexon.b1610"
    dataset = "B1610"
    endpoint = "datasets/B1610/stream"
    minimum_lag_days = 5

    def __init__(
        self,
        client: AsyncJSONClient,
        *,
        revision_lags_days: Sequence[int] = (7, 35),
        max_records_per_day: int = 20_000,
    ) -> None:
        if any(
            isinstance(lag, bool) or not isinstance(lag, int)
            for lag in revision_lags_days
        ):
            raise TypeError("B1610 target lags must be whole days")
        lags = tuple(dict.fromkeys(revision_lags_days))
        if not lags or any(lag < self.minimum_lag_days for lag in lags):
            raise ValueError("B1610 target lags must be at least five days")
        if len(lags) > 4:
            raise ValueError("B1610 target set exceeds safety limit")
        if max_records_per_day <= 0:
            raise ValueError("max_records_per_day must be positive")
        self.client = client
        self.revision_lags_days = lags
        self.max_records_per_day = max_records_per_day

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[SettledMeteredEnergy]:
        anchor_date = window.end.astimezone(GB_TIME_ZONE).date()
        target_dates = tuple(
            anchor_date - timedelta(days=lag)
            for lag in self.revision_lags_days
        )
        records: list[SettledMeteredEnergy] = []
        warnings: list[str] = []
        responses: list[dict[str, Any]] = []
        request_urls: list[str] = []
        retrieved_at = window.end.astimezone(UTC)
        content_type: str | None = None

        for target_date in target_dates:
            response = await self.client.get_json(
                self.endpoint,
                params={
                    "from": target_date.isoformat(),
                    "to": target_date.isoformat(),
                },
            )
            if _payload_record_count(response.payload) > self.max_records_per_day:
                raise AssetSchemaError("B1610 settlement day exceeds safety limit")
            batch = parse_b1610_metered_energy(
                response.payload,
                retrieved_at=response.retrieved_at,
                endpoint=f"/{self.endpoint}",
            )
            if batch.warnings:
                raise AssetSchemaError("B1610 settlement day was only partially valid")
            if any(record.settlement_date != target_date for record in batch.records):
                raise AssetSchemaError("B1610 response escaped the requested settlement day")
            records.extend(batch.records)
            warnings.extend(batch.warnings)
            responses.append(
                {
                    "settlementDate": target_date.isoformat(),
                    "data": response.payload,
                }
            )
            request_urls.append(response.request_url)
            retrieved_at = max(retrieved_at, response.retrieved_at)
            content_type = response.content_type or content_type

        raw_payload: dict[str, Any] = {"responses": responses}
        raw_body = json.dumps(
            raw_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=retrieved_at,
            request_url=request_urls[0],
            records=tuple(records),
            raw_payload=raw_payload,
            raw_body=raw_body,
            checksum_sha256=hashlib.sha256(raw_body).hexdigest(),
            content_type=content_type or "application/json",
            metadata={
                "snapshotKind": "delayed_revision_days",
                "targetSettlementDates": [item.isoformat() for item in target_dates],
                "requestUrls": request_urls,
                "minimumSourceLagDays": self.minimum_lag_days,
                "evidenceSemantics": "delayed_settled_metered_energy",
            },
            warnings=tuple(warnings),
        )


def _payload_record_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).casefold() in {"data", "records", "items", "results"}:
                if isinstance(value, list):
                    return len(value)
                break
    # The parser will provide the more useful structural error. Returning zero
    # here only keeps the safety check independent from schema validation.
    return 0
