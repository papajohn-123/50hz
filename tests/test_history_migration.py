from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.db import (
    ComparisonBaseline,
    MetricAggregate,
    MetricDefinition,
    ObservationCoverageDaily,
)


def test_history_tables_are_registered_with_required_constraints() -> None:
    definitions = MetricDefinition.__table__
    coverage = ObservationCoverageDaily.__table__
    aggregates = MetricAggregate.__table__
    baselines = ComparisonBaseline.__table__

    assert definitions.name == "metric_definitions"
    assert coverage.name == "observation_coverage_daily"
    assert aggregates.name == "metric_aggregates"
    assert baselines.name == "comparison_baselines"

    constraint_names = {
        constraint.name
        for table in (definitions, coverage, aggregates, baselines)
        for constraint in table.constraints
    }
    assert {
        "uq_metric_definition_identity",
        "ck_observation_coverage_daily_valid_daily_expected_intervals",
        "ck_observation_coverage_daily_bounded_daily_coverage",
        "ck_metric_aggregates_aggregate_value_matches_status",
        "ck_comparison_baselines_baseline_values_match_status",
        "ck_comparison_baselines_ordered_baseline_quartiles",
    }.issubset(constraint_names)


def test_offline_migration_creates_coverage_aware_history_tables() -> None:
    repository = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "DATABASE_URL": "postgresql://postgres:postgres@localhost/50hz",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    sql = result.stdout.lower()
    assert "create table metric_definitions" in sql
    assert "create table observation_coverage_daily" in sql
    assert "create table metric_aggregates" in sql
    assert "create table comparison_baselines" in sql
    assert "expected_interval_count between 46 and 50" in sql
    assert "aggregate_value_matches_status" in sql
    assert "baseline_values_match_status" in sql
    assert "ordered_baseline_quartiles" in sql
