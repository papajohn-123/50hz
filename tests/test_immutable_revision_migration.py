from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import ForecastObservation


def test_forecast_revision_migration_is_linear_and_model_identity_is_revisioned() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    migration = scripts.get_revision("20260712_0007")

    assert migration is not None
    assert migration.down_revision == "20260712_0006"
    assert scripts.get_current_head() == "20260715_0011"

    unique = next(
        constraint
        for constraint in ForecastObservation.__table__.constraints
        if constraint.name == "uq_forecast_series_valid_issue_revision"
    )
    assert tuple(column.name for column in unique.columns) == (
        "source_id",
        "metric_type",
        "series_key",
        "variant",
        "valid_from",
        "issued_at",
        "revision",
    )
    assert ForecastObservation.__table__.c.revision.nullable is False
    assert ForecastObservation.__table__.c.revision.server_default is not None


def test_offline_migration_adds_forecast_revision_without_rewriting_old_rows() -> None:
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
    assert "forecast_observations add column revision integer default 0 not null" in sql
    assert "drop constraint uq_forecast_source_metric_series_variant_valid_issue" in sql
    assert "uq_forecast_series_valid_issue_revision unique" in sql
