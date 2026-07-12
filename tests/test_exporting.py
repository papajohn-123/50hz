from __future__ import annotations

import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.exporting import ExportFormat, ExportRequest, build_export, render_csv
from app.exporting.api import get_export_history_repository, router
from app.history import RawMetricObservation, RawMetricSeries
from app.history.repository import HistoryMetric


START = datetime(2026, 7, 1, tzinfo=UTC)


class FakeHistoryRepository:
    def __init__(self, series: RawMetricSeries) -> None:
        self.series = series
        self.requests = []

    async def load(self, request):
        self.requests.append(request)
        return self.series


def carbon_series(*, missing_second: bool = False) -> RawMetricSeries:
    observations = [
        RawMetricObservation(
            timestamp=START,
            value=90,
            revision=0,
            source_record_id="carbon:first",
        )
    ]
    if not missing_second:
        observations.append(
            RawMetricObservation(
                timestamp=START + timedelta(minutes=30),
                value=80,
                revision=0,
                source_record_id="carbon:second",
            )
        )
    return RawMetricSeries(
        identity={
            "metric_id": "carbon.intensity.national",
            "geography": "GB",
            "unit": "gCO2/kWh",
            "fact_class": "estimated",
            "source_id": "neso.carbon-intensity-national",
            "methodology_version": "neso-national-carbon-v1",
        },
        source_cadence_minutes=30,
        observations=tuple(observations),
    )


def request(**overrides) -> ExportRequest:
    values = {
        "metric": HistoryMetric.NATIONAL_CARBON,
        "start": START,
        "end": START + timedelta(hours=1),
    }
    values.update(overrides)
    return ExportRequest(**values)


def test_export_is_bounded_to_exact_half_hours_and_31_days() -> None:
    with pytest.raises(ValidationError, match="exact UTC half-hour"):
        request(start=START + timedelta(minutes=1))
    with pytest.raises(ValidationError, match="cannot exceed 31 days"):
        request(end=START + timedelta(days=31, minutes=30))
    with pytest.raises(ValidationError, match="only 1800-second"):
        request(resolution_seconds=3_600)


def test_export_uses_allowlisted_source_and_preserves_provenance() -> None:
    repository = FakeHistoryRepository(carbon_series())
    result = asyncio.run(
        build_export(
            repository,
            request(),
            generated_at=START + timedelta(hours=2),
        )
    )

    assert repository.requests[0].source_id == "neso.carbon-intensity-national"
    assert result.coverage.is_complete is True
    assert result.coverage.coverage_fraction == 1
    assert [row.value for row in result.rows] == [90, 80]
    assert result.rows[0].source_record_ids == ["carbon:first"]
    assert result.rows[0].classification == "estimated"
    assert result.rows[0].source_methodology_version == "neso-national-carbon-v1"
    assert result.rows[0].materialization_methodology_version == (
        "50hz.history.half-hour-mean.v1"
    )
    payload = result.model_dump(mode="json", by_alias=True)
    assert payload["metricID"] == "carbon.intensity.national"
    assert payload["rows"][0]["sourceRecordIDs"] == ["carbon:first"]


def test_missing_interval_is_an_explicit_gap_not_a_zero_or_omitted_row() -> None:
    result = asyncio.run(
        build_export(FakeHistoryRepository(carbon_series(missing_second=True)), request())
    )

    assert len(result.rows) == 2
    assert result.coverage.available_interval_count == 1
    assert result.coverage.missing_interval_count == 1
    assert result.coverage.coverage_fraction == 0.5
    gap = result.rows[1]
    assert gap.status == "insufficient_data"
    assert gap.value is None
    assert gap.source_record_ids == []
    assert gap.coverage_fraction == 0


def test_csv_has_stable_columns_utf8_and_explicit_gap_rows() -> None:
    result = asyncio.run(
        build_export(FakeHistoryRepository(carbon_series(missing_second=True)), request())
    )
    text = render_csv(result)
    rows = list(csv.DictReader(io.StringIO(text)))

    assert len(rows) == 2
    assert rows[0]["value"] == "90.0"
    assert rows[0]["source_record_ids"] == "carbon:first"
    assert rows[1]["status"] == "insufficient_data"
    assert rows[1]["value"] == ""
    assert rows[1]["coverage_fraction"] == "0.0"
    assert text.encode("utf-8").decode("utf-8") == text


def test_selector_rules_are_enforced_by_the_history_contract() -> None:
    with pytest.raises(ValidationError, match="requires a selector"):
        asyncio.run(
            build_export(
                FakeHistoryRepository(carbon_series()),
                request(metric=HistoryMetric.GENERATION_FUEL),
            )
        )


def test_export_format_is_closed_and_serializable() -> None:
    assert request(format=ExportFormat.CSV).format is ExportFormat.CSV
    with pytest.raises(ValidationError):
        request(format="xlsx")


def export_client(series: RawMetricSeries) -> tuple[TestClient, FakeHistoryRepository]:
    repository = FakeHistoryRepository(series)
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_export_history_repository] = lambda: repository
    return TestClient(application), repository


def test_export_schema_is_machine_readable_and_closed() -> None:
    client, _ = export_client(carbon_series())
    response = client.get("/v1/metadata/export-schema")

    assert response.status_code == 200
    payload = response.json()
    assert payload["maxWindowDays"] == 31
    assert payload["resolutionsSeconds"] == [1_800]
    assert payload["formats"] == ["json", "csv"]
    generation = next(
        item
        for item in payload["metrics"]
        if item["metric"] == "generation.transmission_visible_by_fuel"
    )
    assert generation["selectorRequired"] is True
    assert "WIND" in generation["allowedSelectors"]


def test_export_routes_are_registered_in_the_production_openapi() -> None:
    from app.main import app

    schema = app.openapi()
    assert "/v1/export" in schema["paths"]
    assert "/v1/metadata/export-schema" in schema["paths"]


def test_json_export_route_returns_camel_case_rows_and_gap_coverage() -> None:
    client, repository = export_client(carbon_series(missing_second=True))
    response = client.get(
        "/v1/export",
        params={
            "metric": "carbon.intensity.national",
            "from": START.isoformat(),
            "to": (START + timedelta(hours=1)).isoformat(),
            "format": "json",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metricID"] == "carbon.intensity.national"
    assert payload["coverage"]["missingIntervalCount"] == 1
    assert payload["rows"][1]["status"] == "insufficient_data"
    assert payload["rows"][1]["value"] is None
    assert repository.requests[0].start == START


def test_csv_export_route_sets_download_and_coverage_headers() -> None:
    client, _ = export_client(carbon_series())
    response = client.get(
        "/v1/export",
        params={
            "metric": "carbon.intensity.national",
            "from": START.isoformat(),
            "to": (START + timedelta(hours=1)).isoformat(),
            "format": "csv",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["x-50hz-expected-rows"] == "2"
    assert response.headers["x-50hz-missing-rows"] == "0"
    assert "50hz-carbon-intensity-national.csv" in response.headers[
        "content-disposition"
    ]
    assert "source_record_ids" in response.text.splitlines()[0]


def test_export_route_rejects_unbounded_or_incompatible_requests() -> None:
    client, repository = export_client(carbon_series())
    too_long = client.get(
        "/v1/export",
        params={
            "metric": "carbon.intensity.national",
            "from": START.isoformat(),
            "to": (START + timedelta(days=32)).isoformat(),
        },
    )
    missing_selector = client.get(
        "/v1/export",
        params={
            "metric": "generation.transmission_visible_by_fuel",
            "from": START.isoformat(),
            "to": (START + timedelta(hours=1)).isoformat(),
        },
    )

    assert too_long.status_code == 422
    assert "31 days" in too_long.text
    assert missing_selector.status_code == 422
    assert "selector" in missing_selector.text
    assert repository.requests == []
