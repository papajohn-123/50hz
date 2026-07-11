from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.sources.client import AsyncJSONClient, RetryPolicy
from app.sources.elexon import (
    FuelInstGenerationAdapter,
    InitialDemandAdapter,
    InterconnectorFlowAdapter,
    SystemFrequencyAdapter,
)
from app.sources.exceptions import SourceSchemaError
from app.sources.types import FlowDirection, ObservationWindow


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "elexon"
RETRIEVED_AT = datetime(2026, 7, 11, 12, 21, tzinfo=UTC)
WINDOW = ObservationWindow(
    start=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
    end=datetime(2026, 7, 11, 12, 30, tzinfo=UTC),
)


def fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text())


def fetch_with_fixture(adapter_type: type, payload: Any):
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, json=payload, request=request)

    async def scenario():
        client = AsyncJSONClient(
            transport=httpx.MockTransport(handler),
            clock=lambda: RETRIEVED_AT,
            retry_policy=RetryPolicy(max_attempts=1),
        )
        try:
            result = await adapter_type(client).fetch(WINDOW)
        finally:
            await client.aclose()
        return result

    return asyncio.run(scenario()), seen_requests


def test_fuelinst_normalizes_generation_and_excludes_interconnectors() -> None:
    result, requests = fetch_with_fixture(
        FuelInstGenerationAdapter,
        fixture("fuelinst.json"),
    )

    assert len(result.records) == 7
    assert {record.fuel_code for record in result.records}.isdisjoint({"INTELEC", "INTEW"})
    assert next(record for record in result.records if record.fuel_code == "CCGT").fuel_type == "gas"
    pumped_storage = next(record for record in result.records if record.fuel_code == "PS")
    assert pumped_storage.generation_mw == -291
    assert pumped_storage.source_key == "elexon:FUELINST:2026-07-11T12:15:00Z:PS"
    assert "ignored 2 out-of-scope row(s)" in result.warnings
    assert result.metadata == {"datasets": ["FUELINST"]}
    assert len(result.checksum_sha256) == 64

    request = requests[0]
    assert request.url.path.endswith("/datasets/FUELINST")
    assert request.url.params["publishDateTimeFrom"] == "2026-07-11T12:00:00Z"
    assert request.url.params["publishDateTimeTo"] == "2026-07-11T12:30:00Z"
    assert request.url.params["format"] == "json"


def test_interconnector_adapter_uses_signed_import_convention() -> None:
    result, _ = fetch_with_fixture(
        InterconnectorFlowAdapter,
        fixture("fuelinst.json"),
    )

    assert len(result.records) == 2
    eleclink = next(record for record in result.records if record.interconnector_id == "INTELEC")
    east_west = next(record for record in result.records if record.interconnector_id == "INTEW")
    assert eleclink.flow_mw == 973
    assert eleclink.direction is FlowDirection.IMPORT
    assert eleclink.import_mw == 973
    assert east_west.flow_mw == -356
    assert east_west.direction is FlowDirection.EXPORT
    assert east_west.export_mw == 356
    assert east_west.source_key.endswith(":INTEW")


def test_interconnector_parser_accepts_official_historic_shape() -> None:
    payload = fixture("interconnectors_historic.json")

    async def scenario():
        client = AsyncJSONClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        try:
            return InterconnectorFlowAdapter(client).parse(payload, retrieved_at=RETRIEVED_AT)
        finally:
            await client.aclose()

    records, warnings = asyncio.run(scenario())
    assert warnings == ()
    assert [record.interconnector_id for record in records] == [
        "INTELEC",
        "INTEW",
        "INTFR",
        "INTVKL",
    ]
    assert all(record.dataset == "INTOUTHH" for record in records)


def test_indo_accepts_wrapped_and_unwrapped_responses() -> None:
    payload = fixture("indo.json")

    async def scenario():
        client = AsyncJSONClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        adapter = InitialDemandAdapter(client)
        try:
            wrapped, _ = adapter.parse(payload, retrieved_at=RETRIEVED_AT)
            unwrapped, _ = adapter.parse(payload["data"], retrieved_at=RETRIEVED_AT)
        finally:
            await client.aclose()
        return wrapped, unwrapped

    wrapped, unwrapped = asyncio.run(scenario())
    assert wrapped == unwrapped
    assert [record.demand_mw for record in wrapped] == [16042, 16177]
    assert wrapped[-1].settlement_period == 26


def test_frequency_stream_contract_and_query_parameters() -> None:
    result, requests = fetch_with_fixture(
        SystemFrequencyAdapter,
        fixture("frequency_stream.json"),
    )

    assert [record.frequency_hz for record in result.records] == [
        50.012,
        49.998,
        49.987,
        50.004,
    ]
    assert all(record.published_at is None for record in result.records)
    assert requests[0].url.path.endswith("/datasets/FREQ/stream")
    assert requests[0].url.params["measurementDateTimeFrom"] == "2026-07-11T12:00:00Z"
    assert "format" not in requests[0].url.params


def test_parser_keeps_valid_rows_and_surfaces_partial_schema_drift() -> None:
    payload = fixture("indo.json")
    payload["data"].append({"dataset": "INDO", "demand": "not-a-number"})

    async def scenario():
        client = AsyncJSONClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        try:
            return InitialDemandAdapter(client).parse(payload, retrieved_at=RETRIEVED_AT)
        finally:
            await client.aclose()

    records, warnings = asyncio.run(scenario())
    assert len(records) == 2
    assert any("ignored 1 invalid row" in warning for warning in warnings)


def test_parser_fails_when_every_in_scope_row_is_invalid() -> None:
    payload = {"data": [{"dataset": "INDO", "demand": 100}]}

    async def scenario() -> None:
        client = AsyncJSONClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
        try:
            with pytest.raises(SourceSchemaError, match="no valid INDO records"):
                InitialDemandAdapter(client).parse(payload, retrieved_at=RETRIEVED_AT)
        finally:
            await client.aclose()

    asyncio.run(scenario())

