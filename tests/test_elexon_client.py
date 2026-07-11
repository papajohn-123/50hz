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

