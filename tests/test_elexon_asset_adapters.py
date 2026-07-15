from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.assets import AssetSchemaError, EvidenceKind
from app.assets.adapters import (
    B1610DelayedHistoryAdapter,
    BMUnitReferenceAdapter,
    PhysicalNotificationAdapter,
)
from app.domain.settlement import settlement_period_for_instant
from app.sources.client import AsyncJSONClient, RetryPolicy
from app.sources.types import ObservationWindow


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "elexon_assets"
NOW = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
WINDOW = ObservationWindow(start=NOW - timedelta(minutes=30), end=NOW)


def fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def test_reference_adapter_uses_complete_official_endpoint_and_rejects_empty() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=fixture("bm_units.json")[:2],
            request=request,
        )

    async def scenario():
        client = AsyncJSONClient(
            base_url="https://elexon.example.test/",
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
            clock=lambda: NOW,
        )
        try:
            return await BMUnitReferenceAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    result = asyncio.run(scenario())

    assert requests[0].url.path == "/reference/bmunits/all"
    assert requests[0].url.query == b""
    assert len(result.records) == 2
    assert result.metadata["snapshotKind"] == "complete_reference"
    assert all(
        record.provenance.evidence_kind is EvidenceKind.REFERENCE
        for record in result.records
    )

    async def empty_scenario() -> None:
        client = AsyncJSONClient(
            base_url="https://elexon.example.test/",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=[], request=request)
            ),
            retry_policy=RetryPolicy(max_attempts=1),
            clock=lambda: NOW,
        )
        try:
            with pytest.raises(AssetSchemaError, match="cannot be empty"):
                await BMUnitReferenceAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    asyncio.run(empty_scenario())


def test_pn_adapter_queries_exact_current_gb_period_and_declares_replace_scope() -> None:
    requests: list[httpx.Request] = []
    period = settlement_period_for_instant(NOW)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "dataset": "PN",
                        "settlementDate": period.settlement_date.isoformat(),
                        "settlementPeriod": period.period,
                        "timeFrom": period.start_utc.isoformat(),
                        "timeTo": period.end_utc.isoformat(),
                        "levelFrom": -25,
                        "levelTo": 75,
                        "nationalGridBmUnit": "TEST-1",
                        "bmUnit": "T_TEST-1",
                    }
                ]
            },
            request=request,
        )

    async def scenario():
        client = AsyncJSONClient(
            base_url="https://elexon.example.test/",
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
            clock=lambda: NOW,
        )
        try:
            return await PhysicalNotificationAdapter(
                client,
                bm_units=("T_TEST-1",),
            ).fetch(WINDOW)
        finally:
            await client.aclose()

    result = asyncio.run(scenario())
    request = requests[0]

    assert request.url.path == "/datasets/PN"
    assert request.url.params["settlementDate"] == period.settlement_date.isoformat()
    assert request.url.params["settlementPeriod"] == str(period.period)
    assert request.url.params.get_list("bmUnit") == ["T_TEST-1"]
    assert result.metadata == {
        "snapshotKind": "replace_query_scope",
        "settlementDate": period.settlement_date.isoformat(),
        "settlementPeriod": period.period,
        "bmUnits": ["T_TEST-1"],
        "allUnits": False,
    }
    assert result.records[0].level_from_mw == -25
    assert result.records[0].provenance.evidence_kind is EvidenceKind.REPORTED_PLAN


def test_pn_all_unit_snapshot_accepts_official_rows_without_elexon_unit_id() -> None:
    period = settlement_period_for_instant(NOW)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "dataset": "PN",
                        "settlementDate": period.settlement_date.isoformat(),
                        "settlementPeriod": period.period,
                        "timeFrom": period.start_utc.isoformat(),
                        "timeTo": period.end_utc.isoformat(),
                        "levelFrom": 0,
                        "levelTo": 12.5,
                        "nationalGridBmUnit": "AG-GBL01H",
                        "bmUnit": None,
                    }
                ]
            },
            request=request,
        )

    async def scenario():
        client = AsyncJSONClient(
            base_url="https://elexon.example.test/",
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
            clock=lambda: NOW,
        )
        try:
            return await PhysicalNotificationAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    result = asyncio.run(scenario())

    assert result.metadata["allUnits"] is True
    assert result.records[0].asset_id == "AG-GBL01H"
    assert result.records[0].source_asset_id is None


def test_pn_adapter_caps_explicit_unit_fanout() -> None:
    client = AsyncJSONClient(base_url="https://elexon.example.test/")
    try:
        with pytest.raises(ValueError, match="exceeds safety limit"):
            PhysicalNotificationAdapter(
                client,
                bm_units=tuple(f"T_UNIT-{index}" for index in range(251)),
            )
    finally:
        asyncio.run(client.aclose())


def test_b1610_adapter_only_queries_bounded_delayed_days_and_records_requests() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        target = request.url.params["from"]
        assert request.url.params["to"] == target
        target_date = datetime.fromisoformat(target).date()
        # Summer settlement period 1 starts at 23:00 UTC on the prior date.
        end = datetime.combine(target_date, datetime.min.time(), tzinfo=UTC) - timedelta(
            minutes=30
        )
        return httpx.Response(
            200,
            json=[
                {
                    "dataset": "B1610",
                    "psrType": "Generation",
                    "bmUnit": "T_TEST-1",
                    "nationalGridBmUnitId": "TEST-1",
                    "settlementDate": target,
                    "settlementPeriod": 1,
                    "halfHourEndTime": end.isoformat(),
                    "quantity": -12.5,
                }
            ],
            request=request,
        )

    async def scenario():
        client = AsyncJSONClient(
            base_url="https://elexon.example.test/",
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
            clock=lambda: NOW,
        )
        try:
            return await B1610DelayedHistoryAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    result = asyncio.run(scenario())

    assert [request.url.path for request in requests] == [
        "/datasets/B1610/stream",
        "/datasets/B1610/stream",
    ]
    assert [request.url.params["from"] for request in requests] == [
        "2026-07-08",
        "2026-06-10",
    ]
    assert result.metadata["minimumSourceLagDays"] == 5
    assert result.metadata["targetSettlementDates"] == [
        "2026-07-08",
        "2026-06-10",
    ]
    assert len(result.records) == 2
    assert all(record.average_mw == -25 for record in result.records)
    assert all(
        record.provenance.evidence_kind is EvidenceKind.SETTLED_METERED
        for record in result.records
    )
    assert all(record.settlement_date < NOW.date() - timedelta(days=5) for record in result.records)


def test_b1610_adapter_rejects_any_live_target_configuration() -> None:
    client = AsyncJSONClient(base_url="https://elexon.example.test/")
    try:
        with pytest.raises(ValueError, match="at least five days"):
            B1610DelayedHistoryAdapter(client, revision_lags_days=(4,))
    finally:
        asyncio.run(client.aclose())
