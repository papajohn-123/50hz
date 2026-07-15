from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db import DistributionIncidentCurrent, DistributionIncidentRevision


def test_distribution_incident_schema_is_linear_revisioned_and_privacy_reduced() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    migration = scripts.get_revision("20260715_0010")

    assert migration is not None
    assert migration.down_revision == "20260712_0009"
    assert scripts.get_current_head() == "20260715_0010"
    assert DistributionIncidentRevision.__table__.name == (
        "distribution_incident_revisions"
    )
    assert DistributionIncidentCurrent.__table__.name == (
        "distribution_incident_current"
    )
    columns = DistributionIncidentRevision.__table__.c
    assert "postcode_sectors" in columns
    assert "outward_codes" in columns
    assert "full_postcode" not in columns
    assert "address" not in columns
    unique_names = {
        constraint.name
        for constraint in DistributionIncidentRevision.__table__.constraints
    }
    assert "uq_distribution_incident_revision" in unique_names


def test_offline_upgrade_and_downgrade_include_distribution_incidents() -> None:
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
            "20260715_0010:20260712_0009",
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
    sql = upgrade.stdout.lower()
    assert "create table distribution_incident_revisions" in sql
    assert "create table distribution_incident_current" in sql
    assert "full_postcode" not in sql
    assert "postcode_sectors jsonb" in sql
    assert "drop table distribution_incident_revisions" in downgrade.stdout.lower()
