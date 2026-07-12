from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_grid_read_repository
from app.api.local_windows import (
    LocalWindowsUnavailableError,
    LocalWindowsValidationError,
    present_local_windows,
)
from app.main import app
from app.persistence import ForecastRead


NOW = datetime(2026, 7, 11, 19, 7, tzinfo=UTC)
FLOOR = datetime(2026, 7, 11, 19, 0, tzinfo=UTC)


def forecast_rows(
    values: list[float],
    *,
    start: datetime = FLOOR,
    captured_at: datetime = NOW - timedelta(minutes=10),
    missing: set[int] | None = None,
    source_id: str = "neso.carbon-intensity-national",
) -> tuple[ForecastRead, ...]:
    missing = missing or set()
    return tuple(
        ForecastRead(
            metric_type="carbon_intensity",
            series_key="GB",
            value=value,
            unit="gCO2/kWh",
            valid_from=start + timedelta(minutes=30 * index),
            valid_to=start + timedelta(minutes=30 * (index + 1)),
            issued_at=captured_at,
            published_at=None,
            retrieved_at=captured_at,
            source_id=source_id,
            source_record_id=f"{source_id}:{captured_at.isoformat()}:{index}",
            model_name="neso_carbon_intensity",
            attributes={
                "classification": "forecast",
                "issueTimeBasis": "retrieved_at",
            },
        )
        for index, value in enumerate(values)
        if index not in missing
    )


class HistoryRepository:
    def __init__(self, rows: tuple[ForecastRead, ...]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def get_carbon_forecast_history(
        self,
        **kwargs: Any,
    ) -> tuple[ForecastRead, ...]:
        self.calls.append(kwargs)
        return self.rows


def run_plan(
    repository: HistoryRepository,
    **kwargs: Any,
):
    return asyncio.run(
        present_local_windows(
            repository,
            postcode="SW1A 1AA",
            now=NOW,
            duration_minutes=60,
            **kwargs,
        )
    )


def test_default_bounds_are_half_hour_deterministic_and_capped_to_horizon() -> None:
    repository = HistoryRepository(
        forecast_rows([100, 90, 80, 40, 30, 60, 70, 80])
    )

    response = run_plan(repository)

    assert response.postcode == "SW1A"
    assert response.bounds.earliest_start == datetime(
        2026, 7, 11, 19, 30, tzinfo=UTC
    )
    assert response.bounds.latest_finish == datetime(
        2026, 7, 11, 23, 0, tzinfo=UTC
    )
    assert response.bounds.earliest_was_defaulted is True
    assert response.bounds.latest_was_defaulted is True
    assert "next UTC half-hour" in response.bounds.default_rule
    assert repository.calls[0]["window_start"] == FLOOR
    assert repository.calls[0]["region_code"] == "GB"
    assert repository.calls[0]["captured_before"] == NOW


def test_contract_labels_national_scope_and_capture_based_vintage_truthfully() -> None:
    response = run_plan(
        HistoryRepository(
            forecast_rows([100, 90, 80, 40, 30, 60, 70, 80])
        )
    )

    assert response.forecast.geography_code == "GB"
    assert response.forecast.geography_scope == "national"
    assert response.forecast.fact_class == "forecast"
    assert response.forecast.source_issued_at is None
    assert response.forecast.issue_time_basis == (
        "source_does_not_publish_issue_time"
    )
    assert response.forecast.capture_time_basis == "retrieved_at"
    assert response.forecast.vintage_basis == "captured_at"
    assert response.forecast.capture_age_seconds == 600
    assert all("regional forecast" not in text.lower() for text in response.limitations)
    assert any("national, not regional" in text for text in response.limitations)


def test_latest_partial_capture_does_not_displace_latest_usable_full_vintage() -> None:
    newest_capture = NOW - timedelta(minutes=5)
    full_capture = NOW - timedelta(minutes=35)
    repository = HistoryRepository(
        (
            *forecast_rows([110], captured_at=newest_capture),
            *forecast_rows(
                [100, 90, 80, 40, 30, 60, 70, 80],
                captured_at=full_capture,
            ),
        )
    )

    response = run_plan(repository)

    assert response.forecast.captured_at == full_capture
    assert response.plan.recommended_window is not None
    assert response.plan.recommended_window.average_intensity_gco2_kwh == 35


def test_exact_aware_user_bounds_are_preserved_without_rounding() -> None:
    london_offset = timezone(timedelta(hours=1))
    earliest = datetime(2026, 7, 11, 21, 0, tzinfo=london_offset)
    latest = datetime(2026, 7, 11, 23, 0, tzinfo=london_offset)
    response = run_plan(
        HistoryRepository(
            forecast_rows([100, 90, 80, 40, 30, 60, 70, 80])
        ),
        earliest=earliest,
        latest=latest,
    )

    assert response.bounds.earliest_start == earliest
    assert response.bounds.latest_finish == latest
    assert response.plan.earliest_start == earliest
    assert response.plan.latest_finish == latest
    assert response.bounds.earliest_was_defaulted is False
    assert response.bounds.latest_was_defaulted is False


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"duration_minutes": 45}, "half-hour"),
        ({"continuous": False}, "continuous must be true"),
        (
            {"earliest": datetime(2026, 7, 11, 20, 15, tzinfo=UTC)},
            "half-hour boundary",
        ),
        (
            {"latest": datetime(2026, 7, 11, 20, 0)},
            "include a timezone",
        ),
        (
            {
                "earliest": datetime(2026, 7, 11, 20, 0, tzinfo=UTC),
                "latest": datetime(2026, 7, 11, 20, 30, tzinfo=UTC),
            },
            "cannot fit",
        ),
    ],
)
def test_invalid_requests_fail_before_data_access(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    repository = HistoryRepository(forecast_rows([100, 90, 80, 40]))
    parameters = {
        "postcode": "SW1A",
        "now": NOW,
        "duration_minutes": 60,
        **kwargs,
    }

    with pytest.raises(LocalWindowsValidationError, match=message):
        asyncio.run(present_local_windows(repository, **parameters))

    assert repository.calls == []


def test_missing_and_stale_forecasts_are_distinct_unavailable_failures() -> None:
    with pytest.raises(LocalWindowsUnavailableError, match="unavailable"):
        run_plan(HistoryRepository(()))

    stale = forecast_rows(
        [100, 90, 80, 40, 30, 60],
        captured_at=NOW - timedelta(hours=2),
    )
    with pytest.raises(LocalWindowsUnavailableError, match="stale"):
        run_plan(HistoryRepository(stale))


def test_default_horizon_too_short_for_duration_is_a_validation_error() -> None:
    repository = HistoryRepository(forecast_rows([100]))

    with pytest.raises(LocalWindowsValidationError, match="default forecast horizon"):
        run_plan(repository)


def test_gaps_return_explicit_insufficient_coverage_without_false_deltas() -> None:
    repository = HistoryRepository(
        forecast_rows(
            [100, 90, 80, 70, 60],
            missing={1, 3},
        )
    )
    response = run_plan(
        repository,
        earliest=datetime(2026, 7, 11, 19, 30, tzinfo=UTC),
        latest=datetime(2026, 7, 11, 21, 30, tzinfo=UTC),
    )

    assert response.plan.status.value == "insufficient_coverage"
    assert response.plan.recommended_window is None
    assert response.plan.coverage.gap_starts == [
        datetime(2026, 7, 11, 19, 30, tzinfo=UTC),
        datetime(2026, 7, 11, 20, 30, tzinfo=UTC),
    ]
    assert response.plan.comparison.start_now_minus_recommended_gco2_kwh is None
    assert response.plan.comparison.percent_lower_than_start_now is None
    assert response.plan.comparison.is_meaningful is None


class DynamicRouteRepository:
    def __init__(self, mode: str = "full") -> None:
        self.mode = mode
        self.calls: list[dict[str, Any]] = []

    async def get_carbon_forecast_history(
        self,
        **kwargs: Any,
    ) -> tuple[ForecastRead, ...]:
        self.calls.append(kwargs)
        if self.mode == "missing":
            return ()
        captured_before = kwargs["captured_before"]
        captured = captured_before - (
            timedelta(hours=2)
            if self.mode == "stale"
            else timedelta(minutes=5)
        )
        start = kwargs["window_start"]
        missing = {1, 3, 5, 7} if self.mode == "gaps" else set()
        return forecast_rows(
            [100, 90, 80, 40, 30, 60, 70, 80],
            start=start,
            captured_at=captured,
            missing=missing,
        )


def route_request(
    repository: DynamicRouteRepository,
    path: str,
    *,
    params: dict[str, Any] | None = None,
):
    app.dependency_overrides[get_grid_read_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            return client.get(path, params=params)
    finally:
        app.dependency_overrides.clear()


def test_route_normalizes_full_postcode_and_returns_camel_case_mobile_contract() -> None:
    repository = DynamicRouteRepository()
    response = route_request(
        repository,
        "/v1/regions/SW1A%201AA/windows",
        params={"durationMinutes": 60},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("public, max-age=60")
    payload = response.json()
    assert payload["postcode"] == "SW1A"
    assert payload["forecast"]["geographyCode"] == "GB"
    assert payload["forecast"]["geographyScope"] == "national"
    assert payload["forecast"]["issueTimeBasis"] == (
        "source_does_not_publish_issue_time"
    )
    assert payload["plan"]["requestedDurationMinutes"] == 60
    assert "averageIntensityGCO2KWh" in payload["plan"]["recommendedWindow"]
    encoded = json.dumps(payload).casefold()
    assert "sw1a 1aa" not in encoded
    response_keys = _nested_keys(payload)
    assert all("saving" not in key.casefold() for key in response_keys)
    assert all("emissionskg" not in key.casefold() for key in response_keys)
    assert len(repository.calls) == 1


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/v1/regions/not-a-postcode/windows", {"durationMinutes": 60}),
        ("/v1/regions/SW1A/windows", {"durationMinutes": 45}),
        ("/v1/regions/SW1A/windows", {"durationMinutes": 60, "continuous": False}),
        ("/v1/regions/SW1A/windows", {}),
    ],
)
def test_route_validation_never_reads_forecasts(
    path: str,
    params: dict[str, Any],
) -> None:
    repository = DynamicRouteRepository()
    response = route_request(repository, path, params=params)

    assert response.status_code == 422
    assert repository.calls == []


@pytest.mark.parametrize("mode", ["missing", "stale"])
def test_route_maps_missing_and_stale_forecasts_to_retryable_503(mode: str) -> None:
    response = route_request(
        DynamicRouteRepository(mode),
        "/v1/regions/SW1A/windows",
        params={"durationMinutes": 60},
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "300"
    assert mode in response.json()["detail"]


def test_route_exposes_planner_gap_status_instead_of_bridging_missing_points() -> None:
    response = route_request(
        DynamicRouteRepository("gaps"),
        "/v1/regions/SW1A/windows",
        params={"durationMinutes": 60},
    )

    assert response.status_code == 200
    assert response.json()["plan"]["status"] == "insufficient_coverage"
    assert response.json()["plan"]["recommendedWindow"] is None
    assert response.json()["plan"]["coverage"]["gapStarts"]


def test_openapi_documents_required_duration_bounds_and_privacy_safe_response() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/v1/regions/{postcode}/windows"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}

    assert parameters["durationMinutes"]["required"] is True
    assert parameters["continuous"]["schema"]["default"] is True
    assert parameters["earliest"]["schema"]["anyOf"][0]["format"] == "date-time"
    assert parameters["latest"]["schema"]["anyOf"][0]["format"] == "date-time"
    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema == {"$ref": "#/components/schemas/LocalWindowsResponse"}

    components = schema["components"]["schemas"]
    response = components["LocalWindowsResponse"]
    assert set(response["properties"]) >= {
        "postcode",
        "evaluatedAt",
        "bounds",
        "forecast",
        "plan",
        "limitations",
    }
    assert "outward postcode only" in response["properties"]["postcode"][
        "description"
    ]
    comparison = components["LocalFlexibleUseComparison"]["properties"]
    assert "startNowMinusRecommendedGCO2KWh" in comparison
    assert all("saving" not in field.casefold() for field in comparison)


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *( _nested_keys(item) for item in value.values() )
        )
    if isinstance(value, list):
        return set().union(*(_nested_keys(item) for item in value)) if value else set()
    return set()
