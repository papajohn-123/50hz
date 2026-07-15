from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.geography.adapters import (
    REPD_CONTENT_ENDPOINT,
    REPDReferenceAdapter,
    select_repd_csv_attachment,
)
from app.geography.repd import REPDSchemaError, REPDStatus
from app.sources.client import AsyncJSONClient, RetryPolicy
from app.sources.types import ObservationWindow


NOW = datetime(2026, 7, 15, 11, 0, tzinfo=UTC)
WINDOW = ObservationWindow(start=NOW - timedelta(days=1), end=NOW)
CSV_URL = (
    "https://assets.publishing.service.gov.uk/media/official-hash/"
    "REPD_publication_Q1_2026.csv"
)


def _publication(*, url: str = CSV_URL, file_size: int = 1_000) -> dict[str, object]:
    return {
        "public_updated_at": "2026-05-06T10:00:07+01:00",
        "details": {
            "featured_attachments": ["csv-id", "xlsx-id"],
            "attachments": [
                {
                    "id": "xlsx-id",
                    "title": "REPD Excel",
                    "filename": "repd.xlsx",
                    "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "file_size": 2_000,
                    "accessible": True,
                    "url": "https://assets.publishing.service.gov.uk/media/x/repd.xlsx",
                },
                {
                    "id": "csv-id",
                    "title": "Renewable Energy Planning Database: April 2026 (CSV)",
                    "filename": "REPD_publication_Q1_2026.csv",
                    "content_type": "text/csv",
                    "file_size": file_size,
                    "accessible": True,
                    "url": url,
                },
            ],
        },
    }


def _csv() -> bytes:
    return (
        "Ref ID,Record Last Updated (dd/mm/yyyy),Operator (or Applicant),Site Name,"
        "Technology Type,Storage Type,Installed Capacity (MWelec),Development Status,"
        "Development Status (short),Region,Country,X-coordinate,Y-coordinate,Planning Authority\n"
        "1,01/04/2026,Fen Power Ltd,Fen Solar,Solar Photovoltaics,,42.5,Operational,"
        "Operational,East of England,England,651409.903,313177.270,Fen Council\n"
        "2,01/04/2026,Old Power Ltd,Old Solar,Solar Photovoltaics,,30,Decommissioned,"
        "Decommissioned,East of England,England,400000,300000,Fen Council\n"
    ).encode()


def test_adapter_discovers_official_csv_and_reuses_it_until_url_changes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/" + REPD_CONTENT_ENDPOINT:
            return httpx.Response(200, json=_publication(), request=request)
        if str(request.url) == CSV_URL:
            return httpx.Response(
                200,
                content=_csv(),
                headers={"Content-Type": "text/csv", "ETag": '"q1-2026"'},
                request=request,
            )
        return httpx.Response(404, request=request)

    async def scenario():
        client = AsyncJSONClient(
            base_url="https://www.gov.uk/",
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=1),
            clock=lambda: NOW,
        )
        try:
            adapter = REPDReferenceAdapter(client)
            first = await adapter.fetch(WINDOW)
            second = await adapter.fetch(
                ObservationWindow(
                    start=WINDOW.start + timedelta(days=1),
                    end=WINDOW.end + timedelta(days=1),
                )
            )
            return first, second
        finally:
            await client.aclose()

    first, second = asyncio.run(scenario())

    assert [request.url.path for request in requests].count(
        "/" + REPD_CONTENT_ENDPOINT
    ) == 2
    assert sum(str(request.url) == CSV_URL for request in requests) == 1
    assert len(first.records) == 1
    assert first.records[0].status is REPDStatus.OPERATIONAL
    assert first.records[0].operator_name == "Fen Power Ltd"
    assert first.records[0].provenance.source_url == CSV_URL
    assert first.metadata["cacheHit"] is False
    assert second.metadata["cacheHit"] is True
    assert second.records == first.records
    assert first.raw_payload["parse"]["inputRows"] == 2
    assert "records" not in first.raw_payload
    assert first.checksum_sha256 == second.checksum_sha256


def test_selector_rejects_non_official_or_oversized_attachment() -> None:
    with pytest.raises(REPDSchemaError, match="official asset host"):
        select_repd_csv_attachment(
            _publication(url="https://example.com/repd.csv"),
            max_bytes=10_000,
        )

    with pytest.raises(REPDSchemaError, match="exceeds safety limit"):
        select_repd_csv_attachment(
            _publication(file_size=10_001),
            max_bytes=10_000,
        )


def test_selector_fails_closed_when_csv_is_missing() -> None:
    payload = _publication()
    payload["details"]["attachments"] = []  # type: ignore[index]

    with pytest.raises(REPDSchemaError, match="no accessible CSV"):
        select_repd_csv_attachment(payload, max_bytes=10_000)
