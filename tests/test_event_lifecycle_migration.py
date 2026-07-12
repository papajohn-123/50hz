from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.db import EventLifecycleDelta, EventLifecycleRevision


def test_event_lifecycle_tables_register_immutable_revision_constraints() -> None:
    revisions = EventLifecycleRevision.__table__
    deltas = EventLifecycleDelta.__table__

    assert revisions.name == "event_lifecycle_revisions"
    assert deltas.name == "event_lifecycle_deltas"
    names = {
        constraint.name
        for table in (revisions, deltas)
        for constraint in table.constraints
    }
    assert {
        "uq_event_lifecycle_revision",
        "ck_event_lifecycle_revisions_valid_event_lifecycle_window",
        "ck_event_lifecycle_revisions_event_lifecycle_supersession_matches_status",
        "ck_event_lifecycle_deltas_sequential_event_lifecycle_delta",
        "uq_event_lifecycle_delta",
    }.issubset(names)


def test_offline_migration_creates_event_revision_and_delta_ledger() -> None:
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
    assert "create table event_lifecycle_revisions" in sql
    assert "create table event_lifecycle_deltas" in sql
    assert "uq_event_lifecycle_revision" in sql
    assert "sequential_event_lifecycle_delta" in sql
    assert "superseded_by_event_id is not null" in sql
    assert "foreign key(event_revision_id)" in sql
