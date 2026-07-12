"""Privacy-safe request correlation and bounded structured access logs."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.routing import Match

from app.config import get_settings


ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[[], Awaitable[dict[str, Any]]],
        Callable[[dict[str, Any]], Awaitable[None]],
    ],
    Awaitable[None],
]

logger = logging.getLogger("50hz.request")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    # Railway classifies container stderr as error output regardless of the
    # Python log record level. Successful access records belong on stdout so a
    # healthy 2xx request is not presented as an operational error.
    _structured_handler = logging.StreamHandler(sys.stdout)
    _structured_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_structured_handler)
_REQUEST_ID = re.compile(
    r"(?:[0-9a-f]{16,32}|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})"
)
_SAFE_SERVICE_VALUE = re.compile(r"[A-Za-z0-9._-]{1,32}")
_UNMATCHED_ROUTE = "__unmatched__"


class RequestObservabilityMiddleware:
    """Attach a safe request ID and log one privacy-bounded JSON record.

    Route matching may inspect the ASGI path internally, but only a registered
    route template is emitted. Query strings, bodies, headers, client addresses,
    exception messages and unmatched raw paths are never read into the record.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        clock: Callable[[], float] = time.perf_counter,
        request_logger: logging.Logger = logger,
        service_role: str | None = None,
        service_version: str | None = None,
    ) -> None:
        self.app = app
        self.clock = clock
        self.request_logger = request_logger
        self.service_role = _safe_service_value(service_role)
        self.service_version = _safe_service_value(service_version)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope.get("headers", ()))
        started_at = self.clock()
        status_code = 500
        response_bytes = 0
        response_started = False
        response_completed = False

        async def observe_send(message: dict[str, Any]) -> None:
            nonlocal status_code, response_bytes, response_started, response_completed
            message_type = message.get("type")
            if message_type == "http.response.start":
                response_started = True
                status_code = int(message.get("status", 500))
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            elif message_type == "http.response.body":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    response_bytes += len(body)
                if not message.get("more_body", False):
                    response_completed = True
            await send(message)

        try:
            await self.app(scope, receive, observe_send)
        finally:
            route_template, route_name = _safe_route(scope)
            elapsed_ms = max(0.0, (self.clock() - started_at) * 1_000)
            record: dict[str, Any] = {
                "durationMs": round(elapsed_ms, 3),
                "method": _safe_method(scope.get("method")),
                "requestId": request_id,
                "routeName": route_name,
                "routeTemplate": route_template,
                "status": status_code,
            }
            if response_started and response_completed:
                record["responseBytes"] = response_bytes
            role = self.service_role or _configured_service_role()
            version = self.service_version or _scope_version(scope)
            if role is not None:
                record["serviceRole"] = role
            if version is not None:
                record["serviceVersion"] = version
            self.request_logger.info(
                json.dumps(record, separators=(",", ":"), sort_keys=True)
            )


def _request_id(headers: Any) -> str:
    incoming = next(
        (
            value.decode("ascii", errors="ignore").strip().casefold()
            for key, value in headers
            if key.lower() == b"x-request-id"
        ),
        "",
    )
    if _REQUEST_ID.fullmatch(incoming) is not None:
        return incoming
    return uuid.uuid4().hex


def _safe_route(scope: dict[str, Any]) -> tuple[str, str]:
    route = scope.get("route")
    if route is None:
        application = scope.get("app")
        routes = getattr(application, "routes", ())
        for candidate in routes:
            try:
                match, _ = candidate.matches(scope)
            except Exception:
                continue
            if match is Match.FULL:
                route = candidate
                break
    template = getattr(route, "path", None)
    name = getattr(route, "name", None)
    if not isinstance(template, str) or not template.startswith("/"):
        return _UNMATCHED_ROUTE, _UNMATCHED_ROUTE
    safe_name = (
        name
        if isinstance(name, str) and _SAFE_SERVICE_VALUE.fullmatch(name)
        else "unnamed"
    )
    return template[:200], safe_name


def _safe_method(value: Any) -> str:
    method = str(value or "UNKNOWN").upper()
    return method if re.fullmatch(r"[A-Z]{1,16}", method) else "UNKNOWN"


def _safe_service_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _SAFE_SERVICE_VALUE.fullmatch(value) else None


def _configured_service_role() -> str | None:
    try:
        return _safe_service_value(get_settings().service_role)
    except Exception:
        return None


def _scope_version(scope: dict[str, Any]) -> str | None:
    return _safe_service_value(getattr(scope.get("app"), "version", None))
