from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.sources.client import AsyncJSONClient
from app.sources.elexon import (
    NationalDemandForecastAdapter,
    RemitUnavailabilityAdapter,
    SystemWarningsAdapter,
    WindGenerationForecastAdapter,
)
from app.sources.types import DataClassification, ObservationWindow


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "elexon"
RETRIEVED_AT = datetime(2026, 7, 11, 12, 31, tzinfo=UTC)
WINDOW = ObservationWindow(
    start=datetime(2026, 7, 11, 11, 0, tzinfo=UTC),
    end=datetime(2026, 7, 11, 13, 0, tzinfo=UTC),
)


def fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text())


def run_single(adapter_type: type, payload: Any):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, request=request)

    async def scenario():
        client = AsyncJSONClient(
            transport=httpx.MockTransport(handler),
            clock=lambda: RETRIEVED_AT,
        )
        try:
            return await adapter_type(client).fetch(WINDOW)
        finally:
            await client.aclose()

    return asyncio.run(scenario()), requests


def test_ndf_preserves_forecast_revisions_and_settlement_contract() -> None:
    result, requests = run_single(
        NationalDemandForecastAdapter,
        fixture("ndf_stream.json"),
    )

    assert requests[0].url.path.endswith("/datasets/NDF/stream")
    assert requests[0].url.params["publishDateTimeFrom"] == "2026-07-11T11:00:00Z"
    assert "format" not in requests[0].url.params
    assert len(result.records) == 3
    same_period = [
        record
        for record in result.records
        if record.forecast_for == datetime(2026, 7, 11, 12, 30, tzinfo=UTC)
    ]
    assert len(same_period) == 2
    assert len({record.source_key for record in same_period}) == 2
    assert [record.demand_mw for record in same_period] == [16130, 16100]
    assert all(record.classification is DataClassification.FORECAST for record in result.records)
    assert result.records[0].boundary == "N"
    assert result.records[0].settlement_period == 28


def test_windfor_preserves_each_published_forecast_version() -> None:
    result, requests = run_single(
        WindGenerationForecastAdapter,
        fixture("windfor_stream.json"),
    )

    assert requests[0].url.path.endswith("/datasets/WINDFOR/stream")
    same_hour = [
        record
        for record in result.records
        if record.forecast_for == datetime(2026, 7, 11, 13, 0, tzinfo=UTC)
    ]
    assert [record.generation_mw for record in same_hour] == [5031, 4958]
    assert len({record.source_key for record in same_hour}) == 2
    assert all(record.classification is DataClassification.FORECAST for record in result.records)


def test_syswarn_is_reported_text_with_revision_safe_identity() -> None:
    result, requests = run_single(SystemWarningsAdapter, fixture("syswarn.json"))

    assert requests[0].url.path.endswith("/system/warnings")
    assert requests[0].url.params["format"] == "json"
    assert len(result.records) == 2
    assert all(record.classification is DataClassification.REPORTED for record in result.records)
    assert all(record.revision_number is None for record in result.records)
    assert all(len(record.content_sha256) == 64 for record in result.records)
    assert len({record.source_key for record in result.records}) == 2
    assert result.records[0].warning_text.startswith("NESO reports")


def test_remit_fetches_bulk_details_and_preserves_revisions() -> None:
    listing = fixture("remit_listing_stream.json")
    details = fixture("remit_details.json")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/remit/list/by-publish/stream"):
            return httpx.Response(200, json=listing, request=request)
        if request.url.path.endswith("/remit"):
            return httpx.Response(200, json=details, request=request)
        return httpx.Response(404, request=request)

    async def scenario():
        client = AsyncJSONClient(
            transport=httpx.MockTransport(handler),
            clock=lambda: RETRIEVED_AT,
        )
        try:
            return await RemitUnavailabilityAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    result = asyncio.run(scenario())
    assert len(requests) == 2
    listing_request, detail_request = requests
    assert listing_request.url.params["messageType"] == (
        "UnavailabilitiesOfElectricityFacilities"
    )
    assert listing_request.url.params["latestRevisionOnly"] == "false"
    assert detail_request.url.params.get_list("messageId") == ["903180", "903186"]
    assert len(result.records) == 2
    assert [record.revision_number for record in result.records] == [3, 4]
    assert len({record.source_key for record in result.records}) == 2
    assert result.records[0].mrid == result.records[1].mrid
    assert result.records[1].event_end < result.records[0].event_end
    assert result.records[1].classification is DataClassification.REPORTED
    assert result.records[1].reported_cause == (
        "The market participant reports equipment repair work."
    )
    assert result.records[1].unavailable_capacity_mw == 504
    assert result.records[0].outage_profile[0].start == (
        result.records[0].outage_profile[0].end
    )
    assert result.records[1].outage_profile[0].available_capacity_mw == 0
    assert result.warnings == ()
    assert set(result.raw_payload) == {"listing", "details"}
    assert len(result.metadata["detailChecksums"]) == 1


def test_remit_empty_publish_window_does_not_make_detail_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[], request=request)

    async def scenario():
        client = AsyncJSONClient(
            transport=httpx.MockTransport(handler),
            clock=lambda: RETRIEVED_AT,
        )
        try:
            return await RemitUnavailabilityAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    result = asyncio.run(scenario())
    assert len(requests) == 1
    assert result.records == ()
    assert result.raw_payload == {"listing": [], "details": []}
