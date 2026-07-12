import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rate_limit import (
    DEFAULT_POLICIES,
    RateLimitMiddleware,
    RateLimitPolicy,
    _client_key,
)


def test_expensive_endpoint_is_burst_limited_but_other_routes_are_not() -> None:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        policies=(
            RateLimitPolicy(
                "POST",
                "/v1/ask",
                per_client=2,
                global_limit=3,
                window_seconds=60,
            ),
        ),
    )

    @app.post("/v1/ask")
    async def ask() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        assert client.post("/v1/ask").status_code == 200
        assert client.post("/v1/ask").status_code == 200
        limited = client.post("/v1/ask")
        assert client.get("/health").status_code == 200

    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_railway_real_ip_is_preferred_over_proxy_chain_and_socket() -> None:
    scope = {
        "headers": [
            (b"x-forwarded-for", b"198.51.100.7, 10.0.0.2"),
            (b"x-real-ip", b"203.0.113.8"),
        ],
        "client": ("127.0.0.1", 1234),
    }

    assert _client_key(scope) == "203.0.113.8"


def test_bounded_export_has_a_separate_conservative_default_limit() -> None:
    policy = next(
        item for item in DEFAULT_POLICIES if item.path_prefix == "/v1/export"
    )
    assert policy.method == "GET"
    assert policy.per_client == 6
    assert policy.global_limit == 30


def test_briefing_source_status_and_resolution_have_default_limits() -> None:
    policies = {(item.method, item.path_prefix): item for item in DEFAULT_POLICIES}

    assert ("GET", "/v1/briefing/today") in policies
    assert ("GET", "/v1/sources/status") in policies
    assert ("GET", "/v1/game/") in policies

    middleware = RateLimitMiddleware(lambda *_: None)
    assert middleware._policy(
        {"type": "http", "method": "GET", "path": "/v1/game/2026-07-11/resolution"}
    ) is policies[("GET", "/v1/game/")]
    assert middleware._policy(
        {"type": "http", "method": "GET", "path": "/v1/game/today"}
    ) is None


def test_retry_after_uses_the_bucket_that_is_actually_blocking() -> None:
    now = [-10.0]
    policy = RateLimitPolicy(
        "GET",
        "/limited",
        per_client=2,
        global_limit=10,
        window_seconds=60,
    )
    middleware = RateLimitMiddleware(
        lambda *_: None,
        policies=(policy,),
        clock=lambda: now[0],
    )

    assert asyncio.run(middleware._claim(policy, "other")) is None
    now[0] = 0
    assert asyncio.run(middleware._claim(policy, "client")) is None
    now[0] = 30
    assert asyncio.run(middleware._claim(policy, "client")) is None
    now[0] = 40

    # The client's oldest call expires at t=60. An older call in the non-full
    # global bucket must not shorten that wait to ten seconds.
    assert asyncio.run(middleware._claim(policy, "client")) == 20


def test_retry_after_waits_for_every_full_bucket() -> None:
    now = [0.0]
    policy = RateLimitPolicy(
        "GET",
        "/limited",
        per_client=2,
        global_limit=3,
        window_seconds=60,
    )
    middleware = RateLimitMiddleware(
        lambda *_: None,
        policies=(policy,),
        clock=lambda: now[0],
    )

    assert asyncio.run(middleware._claim(policy, "other")) is None
    now[0] = 10
    assert asyncio.run(middleware._claim(policy, "client")) is None
    now[0] = 30
    assert asyncio.run(middleware._claim(policy, "client")) is None
    now[0] = 40

    # Global capacity returns at t=60, client capacity at t=70.
    assert asyncio.run(middleware._claim(policy, "client")) == 30
