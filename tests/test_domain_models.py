from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.db.base import Base
from app.db import models as database_models  # noqa: F401
from app.db.session import normalize_async_database_url
from app.domain.enums import (
    DataClassification,
    FlowDirection,
    FreshnessState,
)
from app.domain.models import (
    Freshness,
    GenerationFact,
    GridSnapshot,
    GridTimeline,
    InterconnectorFact,
    NumericFact,
    Provenance,
    SourceReference,
    TimelinePoint,
)


NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
SNAPSHOT_ID = UUID("c536b349-ef0a-44a5-99a3-ac085b83c94f")


def source() -> SourceReference:
    return SourceReference(
        id="elexon-fuelinst",
        display_name="Elexon Insights",
        dataset="FUELINST",
        url="https://bmrs.elexon.co.uk/",
    )


def observed_provenance() -> Provenance:
    return Provenance(
        source=source(),
        classification=DataClassification.OBSERVED,
        effective_at=NOW,
        observed_at=NOW,
        published_at=NOW + timedelta(seconds=20),
        retrieved_at=NOW + timedelta(seconds=40),
    )


def fact(value: float, unit: str = "MW") -> NumericFact:
    return NumericFact(
        value=value,
        unit=unit,
        provenance=observed_provenance(),
        freshness=Freshness.assess(
            NOW,
            evaluated_at=NOW + timedelta(seconds=60),
            expected_cadence_seconds=60,
        ),
    )


def test_freshness_is_deterministic_at_thresholds() -> None:
    fresh = Freshness.assess(
        NOW,
        evaluated_at=NOW + timedelta(seconds=120),
        expected_cadence_seconds=60,
    )
    delayed = Freshness.assess(
        NOW,
        evaluated_at=NOW + timedelta(seconds=121),
        expected_cadence_seconds=60,
    )
    stale = Freshness.assess(
        NOW,
        evaluated_at=NOW + timedelta(seconds=600),
        expected_cadence_seconds=60,
    )

    assert fresh.state is FreshnessState.FRESH
    assert delayed.state is FreshnessState.DELAYED
    assert stale.state is FreshnessState.STALE


def test_forecast_provenance_requires_a_distinct_issue_time() -> None:
    with pytest.raises(ValidationError, match="forecast_issued_at"):
        Provenance(
            source=source(),
            classification=DataClassification.FORECAST,
            effective_at=NOW + timedelta(hours=1),
            retrieved_at=NOW,
        )

    forecast = Provenance(
        source=source(),
        classification=DataClassification.FORECAST,
        effective_at=NOW + timedelta(hours=1),
        forecast_issued_at=NOW,
        retrieved_at=NOW + timedelta(seconds=30),
    )
    assert forecast.observed_at is None
    assert forecast.forecast_issued_at == NOW


def test_estimated_provenance_requires_an_observation_time() -> None:
    with pytest.raises(ValidationError, match="estimated"):
        Provenance(
            source=source(),
            classification=DataClassification.ESTIMATED,
            effective_at=NOW,
            retrieved_at=NOW,
        )

    estimate = Provenance(
        source=source(),
        classification=DataClassification.ESTIMATED,
        effective_at=NOW,
        observed_at=NOW,
        retrieved_at=NOW + timedelta(seconds=30),
    )
    assert estimate.classification is DataClassification.ESTIMATED


@pytest.mark.parametrize(
    ("flow_mw", "expected"),
    [
        (750.0, FlowDirection.IMPORT),
        (-125.0, FlowDirection.EXPORT),
        (0.0, FlowDirection.NEUTRAL),
    ],
)
def test_interconnector_sign_convention_is_part_of_the_contract(
    flow_mw: float, expected: FlowDirection
) -> None:
    connector = InterconnectorFact(
        connector_id="ifa",
        display_name="IFA",
        counterparty="France",
        flow=fact(flow_mw),
    )

    assert connector.direction is expected
    assert connector.model_dump(mode="json", by_alias=True)["direction"] == expected.value


def test_snapshot_serializes_camel_case_with_fact_level_provenance() -> None:
    snapshot = GridSnapshot(
        snapshot_id=SNAPSHOT_ID,
        effective_at=NOW,
        generated_at=NOW + timedelta(minutes=1),
        freshness=FreshnessState.FRESH,
        generation=[
            GenerationFact(
                series_key="wind",
                fuel="wind",
                display_name="Wind",
                generation=fact(13_500),
            )
        ],
        demand=fact(28_000),
        frequency=fact(49.98, "Hz"),
    )

    payload = snapshot.model_dump(mode="json", by_alias=True)

    assert payload["snapshotId"] == str(SNAPSHOT_ID)
    assert payload["effectiveAt"] == "2026-07-11T12:00:00Z"
    assert payload["generation"][0]["generation"]["provenance"]["source"]["id"] == (
        "elexon-fuelinst"
    )
    assert payload["frequency"]["unit"] == "Hz"


def test_timeline_rejects_unsorted_or_duplicate_points() -> None:
    with pytest.raises(ValidationError, match="sorted"):
        GridTimeline(
            window_start=NOW,
            window_end=NOW + timedelta(hours=1),
            resolution_seconds=300,
            generated_at=NOW,
            points=[
                TimelinePoint(effective_at=NOW + timedelta(minutes=10)),
                TimelinePoint(effective_at=NOW + timedelta(minutes=5)),
            ],
        )


def test_initial_metadata_contains_all_foundation_tables() -> None:
    expected = {
        "source_metadata",
        "ingestion_runs",
        "raw_payloads",
        "assets",
        "generation_observations",
        "demand_observations",
        "frequency_observations",
        "interconnector_observations",
        "carbon_observations",
        "forecast_observations",
        "grid_snapshots",
        "reported_notices",
        "metric_definitions",
        "observation_coverage_daily",
        "metric_aggregates",
        "comparison_baselines",
        "history_materialization_runs",
        "forecast_verification_pairs",
        "forecast_verification_results",
        "forecast_verification_runs",
        "event_lifecycle_revisions",
        "event_lifecycle_deltas",
        "prediction_resolution_revisions",
        "detected_events",
        "event_explanations",
        "reported_notice_explanations",
    }

    assert expected == set(Base.metadata.tables)
    flow_column = Base.metadata.tables["interconnector_observations"].c.flow_mw
    assert "positive imports" in (flow_column.comment or "")


@pytest.mark.parametrize(
    ("input_url", "expected"),
    [
        (
            "postgres://user:pass@host/db",
            "postgresql+asyncpg://user:pass@host/db",
        ),
        (
            "postgresql://user:pass@host/db",
            "postgresql+asyncpg://user:pass@host/db",
        ),
        (
            "postgresql+asyncpg://user:pass@host/db",
            "postgresql+asyncpg://user:pass@host/db",
        ),
    ],
)
def test_railway_database_urls_use_the_async_driver(
    input_url: str, expected: str
) -> None:
    assert normalize_async_database_url(input_url) == expected
