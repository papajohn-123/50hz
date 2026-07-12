from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.db import PredictionResolutionRevision


def test_prediction_resolution_table_registers_immutable_revision_constraints() -> None:
    table = PredictionResolutionRevision.__table__
    names = {constraint.name for constraint in table.constraints}

    assert table.name == "prediction_resolution_revisions"
    assert {
        "uq_prediction_resolution_revision",
        "uq_prediction_resolution_evidence",
        "ck_prediction_resolution_revisions_terminal_prediction_resolution_state",
        "ck_prediction_resolution_revisions_prediction_resolution_outcome_matches_state",
        "ck_prediction_resolution_revisions_prediction_resolution_sha256_length",
    }.issubset(names)
    assert "updated_at" not in table.c


def test_offline_migration_creates_prediction_resolution_revision_ledger() -> None:
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
    assert "create table prediction_resolution_revisions" in sql
    assert "uq_prediction_resolution_revision" in sql
    assert "uq_prediction_resolution_evidence" in sql
    assert "state in ('resolved', 'void')" in sql
    assert "char_length(evidence_checksum) = 64" in sql
