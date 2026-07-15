from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from app.sources.client import AsyncJSONClient, RetryPolicy
from app.sources.exceptions import SourceHTTPStatusError, SourcePayloadError


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_client_retries_transient_status_and_invalid_json() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, request=request)
        if attempts == 2:
            return httpx.Response(200, text="not-json", request=request)
        return httpx.Response(200, json={"data": []}, request=request)

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async def scenario():
        client = AsyncJSONClient(
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.1),
            sleep=fake_sleep,
            clock=lambda: NOW,
        )
        try:
            return await client.get_json("datasets/FREQ")
        finally:
            await client.aclose()

    result = asyncio.run(scenario())
    assert attempts == 3
    assert delays == [0.0, 0.2]
    assert result.payload == {"data": []}
    assert result.retrieved_at == NOW
    assert len(result.checksum_sha256) == 64


def test_client_does_not_retry_bad_request() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"message": "bad window"}, request=request)

    async def scenario() -> None:
        client = AsyncJSONClient(
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=3),
        )
        try:
            with pytest.raises(SourceHTTPStatusError) as error:
                await client.get_json("datasets/INDO")
            assert error.value.status_code == 400
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert attempts == 1


def test_client_rejects_scalar_json() -> None:
    async def scenario() -> None:
        client = AsyncJSONClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json="unexpected", request=request)
            ),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        try:
            with pytest.raises(SourcePayloadError, match="expected an object or array"):
                await client.get_json("datasets/FREQ")
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_client_fetches_bounded_reference_bytes_with_retry_and_provenance() -> None:
    attempts = 0
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        requests.append(request)
        if attempts == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            content=b"Ref ID,Site Name\n1,Test\n",
            headers={"Content-Type": "text/csv", "ETag": '"repd-v1"'},
            request=request,
        )

    async def scenario():
        client = AsyncJSONClient(
            base_url="https://public.example.test/",
            transport=httpx.MockTransport(handler),
            retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
            clock=lambda: NOW,
        )
        try:
            return await client.get_bytes(
                "reference.csv",
                headers={"Accept": "text/csv"},
                max_bytes=1_000,
            )
        finally:
            await client.aclose()

    result = asyncio.run(scenario())

    assert attempts == 2
    assert requests[-1].headers["accept"] == "text/csv"
    assert result.raw_body.startswith(b"Ref ID")
    assert result.content_type == "text/csv"
    assert result.etag == '"repd-v1"'
    assert result.retrieved_at == NOW
    assert len(result.checksum_sha256) == 64


def test_client_rejects_oversized_reference_body() -> None:
    async def scenario() -> None:
        client = AsyncJSONClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"too large", request=request)
            ),
            retry_policy=RetryPolicy(max_attempts=1),
        )
        try:
            with pytest.raises(SourcePayloadError, match="exceeds 4 bytes"):
                await client.get_bytes("reference.csv", max_bytes=4)
        finally:
            await client.aclose()

    asyncio.run(scenario())
