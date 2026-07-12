from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db import (
    CarbonObservation,
    DemandObservation,
    ForecastObservation,
    ForecastVerificationPair,
    ForecastVerificationResult,
    ForecastVerificationRun,
    GenerationObservation,
)


def test_forecast_verification_schema_is_linear_append_only_and_indexed() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    migration = scripts.get_revision("20260712_0009")

    assert migration is not None
    assert migration.down_revision == "20260712_0008"
    assert scripts.get_current_head() == "20260712_0009"
    assert ForecastVerificationPair.__table__.name == "forecast_verification_pairs"
    assert ForecastVerificationResult.__table__.name == "forecast_verification_results"
    assert ForecastVerificationRun.__table__.name == "forecast_verification_runs"

    pair_unique = next(
        constraint
        for constraint in ForecastVerificationPair.__table__.constraints
        if constraint.name == "uq_forecast_pair_identity_revision"
    )
    result_unique = next(
        constraint
        for constraint in ForecastVerificationResult.__table__.constraints
        if constraint.name == "uq_forecast_result_identity_revision"
    )
    assert tuple(pair_unique.columns)[-1].name == "revision"
    assert tuple(result_unique.columns)[-1].name == "revision"
    assert ForecastVerificationPair.__table__.c.content_sha256.nullable is False
    assert ForecastVerificationResult.__table__.c.evidence_checksum.nullable is False
    assert ForecastVerificationPair.__table__.c.forecast_vintage_at.nullable is False
    assert ForecastVerificationPair.__table__.c.forecast_source_issued_at.nullable is True
    assert (
        ForecastVerificationPair.__table__.c.effective_vintage_time_basis.nullable
        is False
    )
    assert (
        ForecastVerificationResult.__table__.c.effective_vintage_time_basis.nullable
        is False
    )
    pair_checks = {
        constraint.name
        for constraint in ForecastVerificationPair.__table__.constraints
    }
    result_checks = {
        constraint.name for constraint in ForecastVerificationResult.__table__.constraints
    }
    assert any(
        name.endswith("forecast_vintage_basis_matches_timestamps")
        for name in pair_checks
    )
    assert any(
        name.endswith("valid_result_effective_vintage_basis")
        for name in result_checks
    )

    assert "ix_forecast_verify_source_metric_valid" in {
        index.name for index in ForecastObservation.__table__.indexes
    }
    assert "ix_demand_verify_source_time" in {
        index.name for index in DemandObservation.__table__.indexes
    }
    assert "ix_generation_verify_source_time" in {
        index.name for index in GenerationObservation.__table__.indexes
    }
    assert "ix_carbon_verify_source_time" in {
        index.name for index in CarbonObservation.__table__.indexes
    }


def test_offline_upgrade_and_downgrade_include_forecast_verification() -> None:
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
            "20260712_0009:20260712_0008",
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
    assert "create table forecast_verification_pairs" in upgrade.stdout.lower()
    assert "create table forecast_verification_results" in upgrade.stdout.lower()
    assert "create table forecast_verification_runs" in upgrade.stdout.lower()
    assert "forecast_display_threshold" in upgrade.stdout.lower()
    assert "forecast_source_issued_at timestamp with time zone" in upgrade.stdout.lower()
    assert "effective_vintage_time_basis" in upgrade.stdout.lower()
    assert "source_does_not_publish_issue_time" in upgrade.stdout.lower()
    assert "drop table forecast_verification_pairs" in downgrade.stdout.lower()
