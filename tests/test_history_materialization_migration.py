from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import (
    ComparisonBaseline,
    HistoryMaterializationRun,
    IngestionRun,
    MetricAggregate,
    MetricDefinition,
    ObservationCoverageDaily,
    ReportedNotice,
)


def test_history_materialization_schema_is_linear_revision_safe_and_indexed() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    migration = scripts.get_revision("20260712_0008")

    assert migration is not None
    assert migration.down_revision == "20260712_0007"
    assert scripts.get_current_head() == "20260715_0010"
    assert HistoryMaterializationRun.__table__.name == "history_materialization_runs"
    assert MetricDefinition.__table__.c.stable_metric_id.nullable is False

    definition_unique = next(
        constraint
        for constraint in MetricDefinition.__table__.constraints
        if constraint.name == "uq_metric_definition_identity"
    )
    assert tuple(column.name for column in definition_unique.columns) == (
        "stable_metric_id",
        "identity_version",
        "methodology_version",
    )
    for model, unique_name in (
        (ObservationCoverageDaily, "uq_daily_coverage_series_date_method"),
        (MetricAggregate, "uq_metric_aggregate_series_period_method"),
        (ComparisonBaseline, "uq_comparison_baseline_reference_method"),
    ):
        unique = next(
            constraint
            for constraint in model.__table__.constraints
            if constraint.name == unique_name
        )
        assert tuple(unique.columns)[-1].name == "revision"
        assert model.__table__.c.content_sha256.nullable is False
        assert model.__table__.c.source_watermark_at.nullable is True
    assert ComparisonBaseline.__table__.c.attributes.nullable is False

    ingestion_indexes = {index.name for index in IngestionRun.__table__.indexes}
    notice_indexes = {index.name for index in ReportedNotice.__table__.indexes}
    assert {
        "ix_ingestion_runs_adapter_started",
        "ix_ingestion_runs_source_started",
        "ix_ingestion_runs_source_status_completed",
    }.issubset(ingestion_indexes)
    assert {
        "ix_reported_notices_kind_published",
        "ix_reported_notices_identity_revision",
        "ix_reported_notices_external_history",
    }.issubset(notice_indexes)


def test_offline_upgrade_and_downgrade_include_materialization_foundation() -> None:
    repository = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "DATABASE_URL": "postgresql://postgres:postgres@localhost/50hz",
    }
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    downgrade = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "downgrade",
            "20260712_0008:20260712_0007",
            "--sql",
        ],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert upgrade.returncode == 0, upgrade.stderr
    assert downgrade.returncode == 0, downgrade.stderr
    upgrade_sql = upgrade.stdout.lower()
    downgrade_sql = downgrade.stdout.lower()
    assert "create table history_materialization_runs" in upgrade_sql
    assert "add column stable_metric_id" in upgrade_sql
    assert "add column revision integer default 0 not null" in upgrade_sql
    assert "comparison_baselines add column attributes jsonb" in upgrade_sql
    assert "ix_ingestion_runs_adapter_started" in upgrade_sql
    assert "ix_reported_notices_identity_revision" in upgrade_sql
    assert "ix_reported_notices_external_history" in upgrade_sql
    assert "drop table history_materialization_runs" in downgrade_sql
    assert "delete from comparison_baselines where revision > 0" in downgrade_sql
