from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db import B1610SettledEnergyRevision, PhysicalNotificationSegmentCurrent


def test_elexon_asset_schema_is_linear_and_semantically_constrained() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    migration = scripts.get_revision("20260715_0011")

    assert migration is not None
    assert migration.down_revision == "20260715_0010"
    assert scripts.get_current_head() == "20260715_0011"
    assert PhysicalNotificationSegmentCurrent.__table__.name == (
        "physical_notification_segments_current"
    )
    assert B1610SettledEnergyRevision.__table__.name == (
        "b1610_settled_energy_revisions"
    )
    assert PhysicalNotificationSegmentCurrent.elexon_bm_unit.nullable is True
    assert B1610SettledEnergyRevision.elexon_bm_unit.nullable is True
    assert PhysicalNotificationSegmentCurrent.national_grid_bm_unit.nullable is False
    assert B1610SettledEnergyRevision.national_grid_bm_unit.nullable is True
    constraint_names = {
        constraint.name
        for table in (
            PhysicalNotificationSegmentCurrent.__table__,
            B1610SettledEnergyRevision.__table__,
        )
        for constraint in table.constraints
    }
    assert {
        "uq_pn_current_segment",
        "uq_b1610_energy_revision",
    }.issubset(constraint_names)
    assert any(name.endswith("b1610_has_official_unit_id") for name in constraint_names)
    assert any(name.endswith("pn_current_reported_plan_only") for name in constraint_names)
    assert any(name.endswith("b1610_settled_metered_only") for name in constraint_names)
    for table in (
        PhysicalNotificationSegmentCurrent.__table__,
        B1610SettledEnergyRevision.__table__,
    ):
        ondelete = {
            tuple(foreign_key.parent.name for foreign_key in constraint.elements): constraint.ondelete
            for constraint in table.foreign_key_constraints
        }
        assert ondelete[("raw_payload_id",)] == "SET NULL"
        assert ondelete[("asset_id",)] == "RESTRICT"


def test_offline_migration_has_set_null_raw_payload_and_restrict_asset_fks() -> None:
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
            "20260715_0011:20260715_0010",
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
    assert "create table physical_notification_segments_current" in sql
    assert "create table b1610_settled_energy_revisions" in sql
    assert "foreign key(raw_payload_id) references raw_payloads (id) on delete set null" in sql
    assert "foreign key(asset_id) references assets (id) on delete restrict" in sql
    assert "reported_plan" in sql
    assert "settled_metered" in sql
    assert "drop table b1610_settled_energy_revisions" in downgrade.stdout.lower()
