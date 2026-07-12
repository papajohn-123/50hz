from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.forecast_verification.api import (
    get_forecast_verification_repository,
    router,
)
from app.http_cache import ConditionalJSONMiddleware
from app.main import app as production_app
from app.rate_limit import DEFAULT_POLICIES


class EmptyRepository:
    def __init__(self) -> None:
        self.metrics = []

    async def latest(self, metric=None):
        self.metrics.append(metric)
        return ()


def test_public_endpoint_is_bounded_filterable_cached_and_absence_safe() -> None:
    repository = EmptyRepository()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_forecast_verification_repository] = lambda: repository
    app.add_middleware(ConditionalJSONMiddleware)

    with TestClient(app) as client:
        first = client.get(
            "/v1/forecasts/verification?metric=national_demand"
        )
        unchanged = client.get(
            "/v1/forecasts/verification?metric=national_demand",
            headers={"If-None-Match": first.headers["etag"]},
        )
        invalid = client.get("/v1/forecasts/verification?metric=regional_carbon")

    payload = first.json()
    assert first.status_code == 200
    assert first.headers["cache-control"].startswith("public, max-age=300")
    assert unchanged.status_code == 304 and unchanged.content == b""
    assert invalid.status_code == 422
    assert payload["generatedAt"] is None
    assert len(payload["results"]) == 4
    assert all(item["status"] == "insufficient_data" for item in payload["results"])
    assert all(item["reason"] == "not_computed" for item in payload["results"])
    assert all("observationID" not in str(item) for item in payload["results"])


def test_route_and_conservative_rate_limit_are_registered_in_production() -> None:
    schema = production_app.openapi()
    policy = next(
        item
        for item in DEFAULT_POLICIES
        if item.path_prefix == "/v1/forecasts/verification"
    )

    assert "/v1/forecasts/verification" in schema["paths"]
    assert policy.method == "GET"
    assert policy.per_client == 12
    assert policy.global_limit == 60
