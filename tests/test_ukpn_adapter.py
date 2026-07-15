from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.sources.client import AsyncJSONClient, RetryPolicy
from app.sources.exceptions import SourceHTTPStatusError, SourceSchemaError
from app.sources.types import ObservationWindow
from app.sources.ukpn import (
    UKPNLiveFaultsAdapter,
    contains_full_postcode,
    normalize_outward_code,
    ukpn_authorization_headers,
)


NOW = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
WINDOW = ObservationWindow(NOW - timedelta(minutes=30), NOW)


def incident(
    reference: str = "INCD-617934-Z",
    *,
    status: str = "Unplanned",
) -> dict[str, object]:
    return {
        "incidentreference": reference,
        "powercuttype": status,
        "creationdatetime": "2026-07-15T10:49:13",
        "receiveddate": "2026-07-15T10:49:13",
        "restoreddatetime": (
            "2026-07-15T11:21:00" if status == "Restored" else None
        ),
        "planneddate": "2026-07-15T09:00:00" if status == "Planned" else None,
        "estimatedrestorationdate": "2026-07-15T16:00:00",
        "nocallsreported": 3,
        "nocustomeraffected": 46 if status != "Restored" else 0,
        "noplannedcustomers": 50 if status == "Planned" else 0,
        "postcodesaffected": "SW1A 1;SW1A 2",
        "fullpostcodedata": "SW1A1AA;SW1A2BB",
        "mainmessage": "Engineers are working near SW1A 1AA.",
        "incidentcategorycustomerfriendlydescription": "Official detail",
        "incidenttypetbcestimatedfriendlydescription": "15 Jul 16:00 - 17:00",
        "incidentcategory": "24",
        "statusid": 1,
        "geopoint": {"lat": 51.50123, "lon": -0.14123},
        "operatingzone": "LONDON",
    }


def client_for(payload: object, *, status_code: int = 200) -> AsyncJSONClient:
    return AsyncJSONClient(
        base_url="https://ukpn.example.test/",
        retry_policy=RetryPolicy(max_attempts=1),
        clock=lambda: NOW,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status_code,
                json=payload,
                request=request,
            )
        ),
    )


def fetch(payload: object):
    async def scenario():
        client = client_for(payload)
        try:
            return await UKPNLiveFaultsAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "expected", "customers"),
    [
        ("Unplanned", "unplanned", 46),
        ("Planned", "planned", 50),
        ("Restored", "restored", 0),
    ],
)
def test_normalizes_status_counts_local_times_and_aggregated_geography(
    status: str,
    expected: str,
    customers: int,
) -> None:
    result = fetch({"total_count": 1, "results": [incident(status=status)]})

    record = result.records[0]
    assert record.status == expected
    assert record.customers_affected == customers
    # Source text datetimes are UK civil time; July values are BST (UTC+1).
    assert record.source_created_at == datetime(2026, 7, 15, 9, 49, 13, tzinfo=UTC)
    assert record.estimated_restoration_at == datetime(
        2026, 7, 15, 15, 0, tzinfo=UTC
    )
    assert record.postcode_sectors == ("SW1A 1", "SW1A 2")
    assert record.outward_codes == ("SW1A",)
    assert record.geography_precision == "aggregated_incident_point"
    assert record.latitude == 51.50123
    assert record.longitude == -0.14123


def test_removes_full_postcode_fields_and_reduces_stray_values_before_retention() -> None:
    result = fetch({"total_count": 1, "results": [incident()]})
    serialized = json.dumps(result.raw_payload, sort_keys=True)

    assert "fullpostcodedata" not in serialized.lower()
    assert "SW1A1AA" not in serialized
    assert "SW1A 1AA" not in serialized
    assert "near SW1A 1." in serialized
    assert contains_full_postcode(result.raw_payload) is False
    assert b"fullpostcodedata" not in result.raw_body.lower()
    assert result.checksum_sha256 == hashlib.sha256(result.raw_body).hexdigest()
    assert result.records[0].official_summary == "Engineers are working near SW1A 1."


def test_empty_snapshot_is_valid_and_distinct_from_malformed_schema() -> None:
    empty = fetch({"total_count": 0, "results": []})
    assert empty.records == ()
    assert empty.raw_payload == {"total_count": 0, "results": []}

    with pytest.raises(SourceSchemaError, match="total_count"):
        fetch({"results": []})
    with pytest.raises(SourceSchemaError, match="results"):
        fetch({"total_count": 0, "results": {}})


def test_all_malformed_rows_fail_instead_of_publishing_a_false_empty_snapshot() -> None:
    bad = incident()
    bad["powercuttype"] = "Mystery"

    with pytest.raises(SourceSchemaError, match="no valid UKPN"):
        fetch({"total_count": 1, "results": [bad]})


def test_multiple_map_clusters_are_explicitly_ignored_without_double_counting() -> None:
    aggregate = incident("MULT-123")
    aggregate["powercuttype"] = "Multiple"
    result = fetch(
        {"total_count": 2, "results": [incident(), aggregate]}
    )

    assert len(result.records) == 1
    assert result.warnings == ("ignored 1 UKPN aggregate map row(s)",)


def test_bounded_pagination_declares_truncation() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "total_count": 3,
                "results": [incident("INCD-A"), incident("INCD-B")],
            },
            request=request,
        )

    async def scenario():
        client = AsyncJSONClient(
            base_url="https://ukpn.example.test/",
            clock=lambda: NOW,
            transport=httpx.MockTransport(handler),
        )
        try:
            return await UKPNLiveFaultsAdapter(client, max_records=2).fetch(WINDOW)
        finally:
            await client.aclose()

    result = asyncio.run(scenario())
    assert len(calls) == 1
    assert calls[0].url.params["limit"] == "2"
    assert "truncated at 2 of 3" in result.warnings[0]


def test_optional_key_uses_authorization_header_and_never_the_query_string() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"total_count": 0, "results": []},
            request=request,
        )

    async def scenario() -> None:
        client = AsyncJSONClient(
            base_url="https://ukpn.example.test/",
            headers=ukpn_authorization_headers("temporary-test-key"),
            clock=lambda: NOW,
            transport=httpx.MockTransport(handler),
        )
        try:
            await UKPNLiveFaultsAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert requests[0].headers["authorization"] == "Apikey temporary-test-key"
    assert "temporary-test-key" not in str(requests[0].url)
    assert ukpn_authorization_headers(None) == {}


def test_upstream_http_failure_propagates_for_worker_failure_accounting() -> None:
    async def scenario() -> None:
        client = client_for({"message": "offline"}, status_code=503)
        try:
            with pytest.raises(SourceHTTPStatusError):
                await UKPNLiveFaultsAdapter(client).fetch(WINDOW)
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["SW1A 1AA", "not-a-postcode", "SW1A1"])
def test_outward_code_contract_rejects_property_or_sector_precision(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_outward_code(value)


def test_outward_code_contract_normalizes_safe_input() -> None:
    assert normalize_outward_code(" sw1a ") == "SW1A"
