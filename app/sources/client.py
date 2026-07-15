"""A small resilient async JSON client for public grid-data sources."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Mapping, Sequence

import httpx

from app.sources.exceptions import (
    SourceHTTPStatusError,
    SourcePayloadError,
    SourceUnavailableError,
)


DEFAULT_ELEXON_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1/"
DEFAULT_NESO_CARBON_BASE_URL = "https://api.carbonintensity.org.uk/"
DEFAULT_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    retry_statuses: frozenset[int] = DEFAULT_RETRY_STATUSES

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")

    def backoff(self, attempt: int) -> float:
        return min(self.base_delay_seconds * (2 ** max(attempt - 1, 0)), self.max_delay_seconds)


@dataclass(frozen=True, slots=True)
class JSONResponse:
    payload: Any
    raw_body: bytes
    checksum_sha256: str
    retrieved_at: datetime
    request_url: str
    content_type: str | None


class AsyncJSONClient:
    """HTTPX wrapper with bounded timeouts and conservative retries.

    The client retries connection failures, truncated/invalid JSON responses,
    rate limits, and transient server errors.  Other 4xx responses fail without
    retrying so a bad adapter query does not hammer the source.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_ELEXON_BASE_URL,
        timeout: httpx.Timeout | float | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] | None = None,
        user_agent: str = "50Hz/0.1 (+https://github.com/papajohn-123/50hz)",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        resolved_timeout = timeout or httpx.Timeout(15.0, connect=5.0, pool=5.0)
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(UTC))
        default_headers = {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
        if headers:
            default_headers.update(headers)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=resolved_timeout,
            transport=transport,
            follow_redirects=True,
            headers=default_headers,
        )

    async def __aenter__(self) -> AsyncJSONClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(
        self,
        path: str,
        *,
        params: Mapping[
            str,
            str | int | float | bool | None | Sequence[str | int | float | bool],
        ]
        | None = None,
    ) -> JSONResponse:
        last_transport_error: Exception | None = None

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                response = await self._client.get(path, params=params)
            except httpx.RequestError as exc:
                last_transport_error = exc
                if attempt == self._retry_policy.max_attempts:
                    break
                await self._sleep(self._retry_policy.backoff(attempt))
                continue

            if response.status_code in self._retry_policy.retry_statuses:
                if attempt < self._retry_policy.max_attempts:
                    retry_delay = _retry_after_seconds(response, now=self._clock())
                    await self._sleep(
                        min(
                            retry_delay
                            if retry_delay is not None
                            else self._retry_policy.backoff(attempt),
                            self._retry_policy.max_delay_seconds,
                        )
                    )
                    continue

            if response.is_error:
                preview = response.text[:500].replace("\n", " ")
                raise SourceHTTPStatusError(
                    response.status_code,
                    str(response.request.url),
                    preview,
                )

            try:
                payload = response.json()
            except ValueError as exc:
                if attempt < self._retry_policy.max_attempts:
                    await self._sleep(self._retry_policy.backoff(attempt))
                    continue
                raise SourcePayloadError(
                    f"source returned invalid JSON for {response.request.url}"
                ) from exc

            if not isinstance(payload, (dict, list)):
                raise SourcePayloadError(
                    f"source returned a JSON {type(payload).__name__}; expected an object or array"
                )

            body = response.content
            retrieved_at = self._clock()
            if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
                raise ValueError("client clock must return a timezone-aware datetime")
            return JSONResponse(
                payload=payload,
                raw_body=body,
                checksum_sha256=hashlib.sha256(body).hexdigest(),
                retrieved_at=retrieved_at.astimezone(UTC),
                request_url=str(response.request.url),
                content_type=response.headers.get("content-type"),
            )

        assert last_transport_error is not None
        raise SourceUnavailableError(
            f"source unavailable after {self._retry_policy.max_attempts} attempts"
        ) from last_transport_error


def _retry_after_seconds(response: httpx.Response, *, now: datetime) -> float | None:
    raw_value = response.headers.get("retry-after")
    if not raw_value:
        return None
    try:
        return max(float(raw_value), 0.0)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return max((retry_at - now).total_seconds(), 0.0)
