"""Small conditional-response cache for stable public JSON endpoints."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any


ASGIApp = Callable[
    [dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]],
    Awaitable[None],
]


class ConditionalJSONMiddleware:
    """Attach content ETags and answer matching GETs with HTTP 304.

    The selected endpoints return bounded JSON rather than streams. Buffering
    those responses lets the API use a representation-derived validator without
    coupling cache behaviour to database timestamps or route implementation.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http" or scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return
        max_age = _cache_seconds(str(scope.get("path", "")))
        if max_age is None:
            await self.app(scope, receive, send)
            return

        messages: list[dict[str, Any]] = []

        async def capture(message: dict[str, Any]) -> None:
            messages.append(message)

        await self.app(scope, receive, capture)
        start = next(
            (message for message in messages if message.get("type") == "http.response.start"),
            None,
        )
        if start is None or start.get("status") != 200:
            for message in messages:
                await send(message)
            return

        headers = list(start.get("headers", []))
        content_type = _header(headers, b"content-type") or b""
        if not content_type.lower().startswith(b"application/json"):
            for message in messages:
                await send(message)
            return

        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message.get("type") == "http.response.body"
        )
        etag = f'"{hashlib.sha256(body).hexdigest()}"'.encode("ascii")
        cache_control = (
            f"public, max-age={max_age}, stale-while-revalidate={max_age * 4}"
        ).encode("ascii")
        headers = _replace_header(headers, b"etag", etag)
        headers = _replace_header(headers, b"cache-control", cache_control)

        request_headers = {
            key.lower(): value for key, value in scope.get("headers", [])
        }
        if request_headers.get(b"if-none-match") == etag:
            headers = [
                (key, value)
                for key, value in headers
                if key.lower() not in {b"content-length", b"content-type"}
            ]
            await send({"type": "http.response.start", "status": 304, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return

        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def _cache_seconds(path: str) -> int | None:
    if path == "/v1/grid/current":
        return 30
    if path == "/v1/sources/status":
        return 30
    if path == "/v1/grid/timeline":
        return 60
    if path == "/v1/forecasts/verification":
        return 300
    if path in {"/v1/briefing/today", "/v1/events", "/v1/game/today"}:
        return 60
    if path.startswith("/v1/game/") and path.endswith("/resolution"):
        return 60
    if path.startswith("/v1/events/") and not path.endswith("/explanation"):
        return 60
    if path.startswith("/v1/regions/") and path.endswith("/windows"):
        return 60
    if path.startswith("/v1/regions/"):
        return 300
    if path in {
        "/v1/sources",
        "/v1/meta",
        "/v1/metadata/metrics",
        "/v1/metadata/export-schema",
    }:
        return 3_600
    return None


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    return next((value for key, value in headers if key.lower() == name), None)


def _replace_header(
    headers: list[tuple[bytes, bytes]],
    name: bytes,
    value: bytes,
) -> list[tuple[bytes, bytes]]:
    return [(key, item) for key, item in headers if key.lower() != name] + [(name, value)]
