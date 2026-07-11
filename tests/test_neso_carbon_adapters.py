from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.sources.client import AsyncJSONClient, DEFAULT_NESO_CARBON_BASE_URL
from app.sources.neso_carbon import (
    LondonCarbonIntensityAdapter,
    NationalCarbonCurrentAdapter,
    NationalCarbonForecastAdapter,
    PostcodeCarbonIntensityAdapter,
    normalize_outward_postcode,
)
from app.sources.types import DataClassification, ObservationWindow


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "neso_carbon"
RETRIEVED_AT = datetime(2026, 7, 11, 12, 21, tzinfo=UTC)
WINDOW = ObservationWindow(
    start=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
    end=datetime(2026, 7, 11, 13, 30, tzinfo=UTC),
)


def fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text())


def run_adapter(adapter_factory, payload: Any):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, request=request)

    async def scenario():
        client = AsyncJSONClient(
            base_url=DEFAULT_NESO_CARBON_BASE_URL,
            transport=httpx.MockTransport(handler),
            clock=lambda: RETRIEVED_AT,
        )
        try:
            return await adapter_factory(client).fetch(WINDOW)
        finally:
            await client.aclose()

    return asyncio.run(scenario()), requests


def test_national_current_keeps_actual_and_forecast_separate() -> None:
    result, requests = run_adapter(
        NationalCarbonCurrentAdapter,
        fixture("national_current.json"),
    )

    assert requests[0].url.path == "/intensity"
    assert len(result.records) == 2
    observed = next(
        record
        for record in result.records
        if record.classification is DataClassification.OBSERVED
    )
    forecast = next(
        record
        for record in result.records
        if record.classification is DataClassification.FORECAST
    )
    assert observed.intensity_g_co2_per_kwh == 67
    assert forecast.intensity_g_co2_per_kwh == 63
    assert observed.source_key.endswith(":observed")
    assert forecast.source_key.endswith(":forecast")
    assert observed.period_end == datetime(2026, 7, 11, 12, 30, tzinfo=UTC)


def test_national_forecast_uses_requested_range_and_never_emits_actuals() -> None:
    result, requests = run_adapter(
        NationalCarbonForecastAdapter,
        fixture("national_forecast.json"),
    )

    assert requests[0].url.path == (
        "/intensity/2026-07-11T12:00Z/2026-07-11T13:30Z"
    )
    assert [record.intensity_g_co2_per_kwh for record in result.records] == [63, 61, 59]
    assert all(
        record.classification is DataClassification.FORECAST
        for record in result.records
    )


def test_london_region_preserves_forecast_mix_and_region_provenance() -> None:
    result, requests = run_adapter(
        LondonCarbonIntensityAdapter,
        fixture("london.json"),
    )

    assert requests[0].url.path == "/regional/regionid/13"
    assert len(result.records) == 1
    record = result.records[0]
    assert record.classification is DataClassification.FORECAST
    assert record.region_id == 13
    assert record.region_name == "London"
    assert record.dno_region == "UKPN London"
    assert record.intensity_g_co2_per_kwh == 81
    assert sum(item.percent for item in record.generation_mix) == pytest.approx(100)
    assert next(item for item in record.generation_mix if item.fuel_type == "solar").percent == 26.3


def test_postcode_adapter_accepts_full_postcode_but_sends_outward_code() -> None:
    result, requests = run_adapter(
        lambda client: PostcodeCarbonIntensityAdapter(client, "sw1a 1aa"),
        fixture("postcode.json"),
    )

    assert requests[0].url.path == "/regional/postcode/SW1A"
    assert result.records[0].postcode == "SW1A"
    assert result.records[0].region_id == 13
    assert result.records[0].source_key.startswith("neso-carbon:region-13-SW1A:")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SW1A", "SW1A"),
        ("EC1A 1BB", "EC1A"),
        ("m1 1ae", "M1"),
        ("GIR 0AA", "GIR"),
    ],
)
def test_outward_postcode_normalization(raw: str, expected: str) -> None:
    assert normalize_outward_postcode(raw) == expected


def test_outward_postcode_rejects_non_postcodes() -> None:
    with pytest.raises(ValueError, match="valid UK"):
        normalize_outward_postcode("central london")
