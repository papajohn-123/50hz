from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rate_limit import RateLimitMiddleware, RateLimitPolicy, _client_key


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
