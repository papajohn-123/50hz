from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.http_cache import ConditionalJSONMiddleware


def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(ConditionalJSONMiddleware)

    @app.get("/v1/grid/current")
    async def current() -> dict[str, object]:
        return {"timestamp": "2026-07-11T12:00:00Z", "demandMW": 28_400}

    @app.get("/v1/events/evt_missing")
    async def missing() -> None:
        raise HTTPException(status_code=404, detail="missing")

    @app.post("/v1/ask")
    async def ask() -> dict[str, str]:
        return {"answer": "bounded"}

    return TestClient(app)


def test_matching_etag_returns_bodyless_not_modified_response() -> None:
    with client() as test_client:
        first = test_client.get("/v1/grid/current")
        second = test_client.get(
            "/v1/grid/current",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert first.status_code == 200
    assert first.headers["cache-control"].startswith("public, max-age=30")
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["etag"] == first.headers["etag"]


def test_errors_and_non_get_requests_are_not_cached() -> None:
    with client() as test_client:
        missing = test_client.get("/v1/events/evt_missing")
        post = test_client.post("/v1/ask")

    assert "etag" not in missing.headers
    assert "etag" not in post.headers
