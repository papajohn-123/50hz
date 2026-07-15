from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.api.dependencies import get_grid_read_repository
from app.api.models import (
    DataFamilyStatus,
    DeliveryState,
    FactState,
    MetricFamily,
)
from app.db.models import IngestionRun, SourceMetadata
from app.domain.enums import IngestionRunStatus
from app.source_health.api import get_source_health_repository, router
from app.source_health.repository import (
    SourceHealthRepository,
    SourceRunSummary,
    _latest_run_statement,
    _latest_success_statement,
    _public_sources_statement,
)
from app.source_health.service import build_source_health


NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


def source(
    source_id: str,
    *,
    dataset: str,
    cadence: int = 300,
) -> SourceMetadata:
    return SourceMetadata(
        id=source_id,
        provider=source_id.split(".", 1)[0],
        dataset=dataset,
        display_name=f"Publisher — {dataset}",
        base_url="https://example.test",
        documentation_url="https://example.test/docs",
        licence_name=None,
        licence_url="https://example.test/licence",
        attribution="Authoritative public data.",
        expected_cadence_seconds=cadence,
        active=True,
    )


def family_status(
    family: MetricFamily,
    fact_state: FactState,
    *,
    observed_at: datetime | None = None,
) -> DataFamilyStatus:
    available = observed_at is not None
    return DataFamilyStatus(
        family=family,
        metric_ids=[f"metric.{family.value}"],
        source_ids=[],
        required_for_snapshot=True,
        evaluated_at=NOW,
        delivery_state=(DeliveryState.HEALTHY if available else DeliveryState.UNAVAILABLE),
        fact_state=fact_state,
        observed_at=observed_at,
        retrieved_at=(observed_at + timedelta(minutes=1) if available else None),
        valid_to=(observed_at + timedelta(minutes=30) if available else None),
        observation_age_seconds=(
            int((NOW - observed_at).total_seconds()) if available else None
        ),
        retrieval_age_seconds=(
            int((NOW - observed_at - timedelta(minutes=1)).total_seconds())
            if available
            else None
        ),
        expected_cadence_seconds=300,
        delivery_healthy_seconds=600,
        delivery_stale_seconds=1_200,
        fact_live_seconds=600,
        fact_stale_seconds=1_200,
        series_count=1 if available else 0,
    )


def test_delivery_and_fact_states_are_independent_and_camel_case() -> None:
    carbon = source(
        "neso.carbon-intensity-national",
        dataset="CARBON_INTENSITY_NATIONAL",
        cadence=1_800,
    )
    response = build_source_health(
        (carbon,),
        {
            carbon.id: SourceRunSummary(
                source_id=carbon.id,
                last_attempted_at=NOW - timedelta(seconds=30),
                last_attempt_state="succeeded",
                last_succeeded_at=NOW - timedelta(seconds=30),
            )
        },
        evaluated_at=NOW,
        current_fact_statuses=[
            family_status(
                MetricFamily.CARBON,
                FactState.STALE,
                observed_at=NOW - timedelta(hours=2),
            )
        ],
    )

    item = response.sources[0]
    assert item.delivery_state == "healthy"
    assert item.fact_state == "stale"
    assert item.delivery_lag_seconds == 30
    assert item.fact_age_seconds == 7_200
    payload = response.model_dump(mode="json", by_alias=True)
    assert payload["evaluatedAt"] == NOW.isoformat().replace("+00:00", "Z")
    assert payload["sources"][0]["sourceID"] == carbon.id
    assert payload["sources"][0]["deliveryState"] == "healthy"
    assert "error" not in payload["sources"][0]


def test_current_family_gap_can_be_unavailable_while_delivery_is_healthy() -> None:
    fuelinst = source("elexon.fuelinst", dataset="FUELINST")
    response = build_source_health(
        (fuelinst,),
        {
            fuelinst.id: SourceRunSummary(
                source_id=fuelinst.id,
                last_attempted_at=NOW,
                last_attempt_state="succeeded",
                last_succeeded_at=NOW,
            )
        },
        evaluated_at=NOW,
        current_fact_statuses=[
            family_status(
                MetricFamily.GENERATION,
                FactState.LIVE,
                observed_at=NOW - timedelta(minutes=5),
            ),
            family_status(MetricFamily.INTERCONNECTORS, FactState.UNAVAILABLE),
        ],
    )

    item = response.sources[0]
    assert item.delivery_state == "healthy"
    assert item.fact_state == "unavailable"
    assert item.fact_families == [
        MetricFamily.GENERATION,
        MetricFamily.INTERCONNECTORS,
    ]


def test_non_current_source_is_not_applicable_and_missing_runs_are_unavailable() -> None:
    remit = source("elexon.remit", dataset="REMIT")
    response = build_source_health((remit,), {}, evaluated_at=NOW)

    item = response.sources[0]
    assert item.delivery_state == "unavailable"
    assert item.delivery_lag_seconds is None
    assert item.fact_state == "not_applicable"
    assert item.fact_families == []


def test_delivery_thresholds_are_cadence_derived_and_boundary_safe() -> None:
    demand = source("elexon.indo", dataset="INDO", cadence=1_800)

    def state(age: int) -> str:
        return build_source_health(
            (demand,),
            {
                demand.id: SourceRunSummary(
                    source_id=demand.id,
                    last_attempted_at=NOW - timedelta(seconds=age),
                    last_attempt_state="succeeded",
                    last_succeeded_at=NOW - timedelta(seconds=age),
                )
            },
            evaluated_at=NOW,
        ).sources[0].delivery_state.value

    assert state(3_600) == "healthy"
    assert state(3_601) == "delayed"
    assert state(7_200) == "stale"


class FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def scalars(self) -> FakeScalars:
        return FakeScalars(self.rows)


class FakeSession:
    def __init__(self, results: list[list[Any]]) -> None:
        self.results = results
        self.statements: list[Any] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.results.pop(0))


def run(
    *,
    source_id: str,
    status: IngestionRunStatus,
    started_at: datetime,
    completed_at: datetime | None,
) -> IngestionRun:
    return IngestionRun(
        id=uuid4(),
        source_id=source_id,
        adapter="adapter",
        endpoint="endpoint",
        idempotency_key=str(uuid4()),
        requested_from=None,
        requested_to=None,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        records_received=1,
        records_written=1,
        cursor={},
        error=None,
    )


def test_repository_returns_latest_attempt_and_latest_success_without_errors() -> None:
    import asyncio

    metadata = source("elexon.indo", dataset="INDO")
    latest = run(
        source_id=metadata.id,
        status=IngestionRunStatus.FAILED,
        started_at=NOW,
        completed_at=NOW,
    )
    success = run(
        source_id=metadata.id,
        status=IngestionRunStatus.SUCCEEDED,
        started_at=NOW - timedelta(minutes=5),
        completed_at=NOW - timedelta(minutes=4),
    )
    session = FakeSession([[metadata], [latest], [success]])
    sources, summaries = asyncio.run(SourceHealthRepository(lambda: session).load())

    assert sources == (metadata,)
    assert summaries[metadata.id].last_attempt_state == "failed"
    assert summaries[metadata.id].last_attempted_at == NOW
    assert summaries[metadata.id].last_succeeded_at == NOW - timedelta(minutes=4)
    assert len(session.statements) == 3


def test_latest_run_queries_use_postgres_distinct_on_and_never_select_errors() -> None:
    dialect = postgresql.dialect()
    latest = str(
        _latest_run_statement(("elexon.indo",)).compile(dialect=dialect)
    ).lower()
    success = str(
        _latest_success_statement(("elexon.indo",)).compile(dialect=dialect)
    ).lower()

    assert "distinct on (ingestion_runs.source_id)" in latest
    assert "order by ingestion_runs.source_id" in latest
    assert "distinct on (ingestion_runs.source_id)" in success
    assert "ingestion_runs.status" in success


def test_public_source_queries_exclude_internal_operational_providers() -> None:
    from app.persistence.reads import _public_sources_statement as grid_sources

    dialect = postgresql.dialect()
    health_sql = str(
        _public_sources_statement().compile(
            dialect=dialect,
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    grid_sql = str(
        grid_sources().compile(
            dialect=dialect,
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    for statement in (health_sql, grid_sql):
        assert "source_metadata.active is true" in statement
        assert "source_metadata.provider in" in statement
        assert "source_metadata.id in" in statement
        assert "elexon" in statement
        assert "neso" in statement
        assert "history-backfill-v1" not in statement
        assert "elexon.interconnectors" not in statement
        assert "neso.carbon-national-forecast" not in statement
        assert "elexon.bm-unit-reference" in statement
        assert "elexon.pn" in statement
        assert "elexon.b1610" in statement


class FakeHealthRepository:
    async def load(self):
        item = source("elexon.indo", dataset="INDO")
        now = datetime.now(UTC)
        return (item,), {
            item.id: SourceRunSummary(
                source_id=item.id,
                last_attempted_at=now,
                last_attempt_state="succeeded",
                last_succeeded_at=now,
            )
        }


class OfflineGridRepository:
    async def get_current(self, *, as_of=None):
        raise RuntimeError("current composition unavailable")


def test_route_preserves_delivery_inspection_when_current_facts_fail() -> None:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_source_health_repository] = (
        lambda: FakeHealthRepository()
    )
    application.dependency_overrides[get_grid_read_repository] = (
        lambda: OfflineGridRepository()
    )
    response = TestClient(application).get("/v1/sources/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sourceCount"] == 1
    assert payload["sources"][0]["deliveryState"] == "healthy"
    assert payload["sources"][0]["factState"] == "unavailable"


def test_source_status_route_is_registered_in_production_openapi() -> None:
    from app.main import app

    assert "/v1/sources/status" in app.openapi()["paths"]
