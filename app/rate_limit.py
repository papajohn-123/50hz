"""Low-cost burst protection for expensive public endpoints.

The production service currently runs one API replica.  This limiter protects
that process and upstream budgets; the external OpenRouter spend cap remains the
hard billing boundary.  A shared durable counter is required before scaling the
API horizontally.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    method: str
    path_prefix: str
    per_client: int
    global_limit: int
    window_seconds: int = 60

    def __post_init__(self) -> None:
        if self.per_client <= 0 or self.global_limit <= 0 or self.window_seconds <= 0:
            raise ValueError("rate-limit values must be positive")


DEFAULT_POLICIES = (
    RateLimitPolicy("POST", "/v1/ask", per_client=6, global_limit=30),
    RateLimitPolicy("GET", "/v1/events/", per_client=12, global_limit=60),
    RateLimitPolicy("GET", "/v1/regions/", per_client=30, global_limit=120),
    RateLimitPolicy("GET", "/v1/grid/timeline", per_client=60, global_limit=300),
)


class RateLimitMiddleware:
    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        policies: Iterable[RateLimitPolicy] = DEFAULT_POLICIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.policies = tuple(policies)
        self.clock = clock
        self._global: dict[RateLimitPolicy, deque[float]] = defaultdict(deque)
        self._clients: dict[tuple[RateLimitPolicy, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        policy = self._policy(scope)
        if policy is None:
            await self.app(scope, receive, send)
            return

        client = _client_key(scope)
        retry_after = await self._claim(policy, client)
        if retry_after is not None:
            body = json.dumps({"detail": "Too many requests"}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"retry-after", str(retry_after).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

    def _policy(self, scope: dict[str, Any]) -> RateLimitPolicy | None:
        if scope.get("type") != "http":
            return None
        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))
        for policy in self.policies:
            if method == policy.method and path.startswith(policy.path_prefix):
                # Event list/detail are cheap database reads. Only explanation
                # requests on this prefix can call OpenRouter.
                if policy.path_prefix == "/v1/events/" and not path.endswith(
                    "/explanation"
                ):
                    continue
                return policy
        return None

    async def _claim(
        self,
        policy: RateLimitPolicy,
        client: str,
    ) -> int | None:
        now = self.clock()
        cutoff = now - policy.window_seconds
        async with self._lock:
            if len(self._clients) > 1_024:
                for key, calls in list(self._clients.items()):
                    _discard_before(calls, cutoff)
                    if not calls:
                        del self._clients[key]
            global_calls = self._global[policy]
            client_calls = self._clients[(policy, client)]
            _discard_before(global_calls, cutoff)
            _discard_before(client_calls, cutoff)
            if (
                len(global_calls) >= policy.global_limit
                or len(client_calls) >= policy.per_client
            ):
                oldest = min(
                    global_calls[0] if global_calls else now,
                    client_calls[0] if client_calls else now,
                )
                return max(1, int(oldest + policy.window_seconds - now) + 1)
            global_calls.append(now)
            client_calls.append(now)
        return None


def _discard_before(values: deque[float], cutoff: float) -> None:
    while values and values[0] <= cutoff:
        values.popleft()


def _client_key(scope: dict[str, Any]) -> str:
    headers = scope.get("headers", [])
    real_ip = next(
        (
            value.decode("latin-1")
            for key, value in headers
            if key.lower() == b"x-real-ip"
        ),
        "",
    )
    # Railway documents X-Real-IP as the originating remote address. Prefer
    # that proxy-owned value so every user does not collapse into the edge
    # proxy's socket address.
    if candidate := real_ip.strip():
        return candidate[:128]

    forwarded_for = next(
        (
            value.decode("latin-1")
            for key, value in headers
            if key.lower() == b"x-forwarded-for"
        ),
        "",
    )
    if forwarded_for:
        # The left-most entry is the originating address in the conventional
        # chain. This is only a fallback for non-Railway/local proxy setups;
        # the global bucket remains the hard cost-control boundary.
        candidate = forwarded_for.split(",", 1)[0].strip()
        if candidate:
            return candidate[:128]
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])[:128]
    return "unknown"
