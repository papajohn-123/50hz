from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.api.dependencies import get_grid_read_repository, get_history_repository
from app.api.history_context import present_current_history_context
from app.history.materialize import RawMetricObservation, RawMetricSeries
from app.history.models import MetricSeriesIdentity
from app.history.repository import HistoryMetric, HistorySeriesRequest
from app.main import app
from app.persistence.reads import (
    CarbonRead,
    CurrentGridRead,
    DemandRead,
    ReadProvenance,
)


REFERENCE = datetime(2026, 7, 11, 12, tzinfo=UTC)


class GridRepository:
    async def get_current(
        self,
        *,
        as_of: datetime | None = None,
        carbon_region: str = "GB",
    ) -> CurrentGridRead:
        requested_at = as_of or REFERENCE
        return CurrentGridRead(
            requested_at=requested_at,
            generation=(),
            demand=DemandRead(
                "gb",
                "indo",
                30_000,
                provenance("elexon.indo", "demand"),
            ),
            frequency=None,
            interconnectors=(),
            carbon=CarbonRead(
                "GB",
                180,
                "moderate",
                (),
                provenance("neso.carbon-intensity-national", "carbon"),
            ),
            sources=(),
        )


class HistoryRepository:
    def __init__(self) -> None:
        self.requests: list[HistorySeriesRequest] = []

    async def load(self, request: HistorySeriesRequest) -> RawMetricSeries:
        self.requests.append(request)
        is_demand = request.metric_id is HistoryMetric.NATIONAL_DEMAND
        reference_value = 100.0 if is_demand else 200.0
        prior_base = 101.0 if is_demand else 100.0
        values = [(REFERENCE, reference_value), (REFERENCE - timedelta(minutes=30), 95.0)]
        values.extend(
            (REFERENCE - timedelta(days=days), prior_base + days - 1)
            for days in range(1, 29)
        )
        return RawMetricSeries(
            identity=MetricSeriesIdentity(
                metric_id=request.metric_id.value,
                geography="GB",
                unit="MW" if is_demand else "gCO2/kWh",
                fact_class="observed" if is_demand else "estimated",
                source_id=request.source_id,
                methodology_version=(
                    "indo-national-demand-v1"
                    if is_demand
                    else "neso-national-carbon-v1"
                ),
            ),
            source_cadence_minutes=30,
            observations=tuple(
                RawMetricObservation(
                    timestamp=timestamp,
                    value=value,
                    revision=0,
                    source_record_id=(
                        f"{request.metric_id.value}:{timestamp.isoformat()}"
                    ),
                    retrieved_at=REFERENCE,
                )
                for timestamp, value in values
            ),
        )


def provenance(source_id: str, record_id: str) -> ReadProvenance:
    return ReadProvenance(
        source_id=source_id,
        source_record_id=record_id,
        observed_at=REFERENCE,
        published_at=REFERENCE,
        retrieved_at=REFERENCE,
    )


async def test_history_context_exposes_compatible_28_day_baselines() -> None:
    history = HistoryRepository()

    result = await present_current_history_context(
        GridRepository(),
        history,
        as_of=REFERENCE + timedelta(minutes=5),
    )

    assert len(history.requests) == 2
    assert {request.metric_id for request in history.requests} == {
        HistoryMetric.NATIONAL_DEMAND,
        HistoryMetric.NATIONAL_CARBON,
    }
    demand, carbon = result.metrics
    assert demand.available is True
    assert demand.reference_value == 100
    assert demand.previous_period is not None
    assert demand.previous_period.reference_minus_comparison == 5
    assert demand.rolling_28_days is not None
    assert demand.rolling_28_days.valid_day_count == 28
    assert demand.rolling_28_days.coverage_fraction == 1
    assert "well below" in demand.summary
    assert carbon.rolling_28_days is not None
    assert carbon.rolling_28_days.reference_percentile == 100
    assert "well above" in carbon.summary


def test_history_context_route_uses_mobile_camel_case_contract() -> None:
    history = HistoryRepository()
    app.dependency_overrides[get_grid_read_repository] = lambda: GridRepository()
    app.dependency_overrides[get_history_repository] = lambda: history
    try:
        with TestClient(app) as client:
            response = client.get(
                "/v1/history/context",
                params={"at": "2026-07-11T12:05:00Z"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert response.headers["cache-control"].startswith("public, max-age=60")
    assert payload["schemaVersion"] == "1.0"
    assert payload["metrics"][0]["metricID"] == "demand.national_outturn"
    assert payload["metrics"][0]["sourceID"] == "elexon.indo"
    assert payload["metrics"][0]["rolling28Days"]["validDayCount"] == 28
    assert "rolling_28_days" not in payload["metrics"][0]
