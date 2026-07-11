from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_offline_migration_creates_reported_notice_explanation_cache() -> None:
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
    assert "create table reported_notice_explanations" in sql
    assert "uq_reported_notice_explanation_cache_key" in sql
    assert "public_event_id" in sql
    assert "notice_revision_key" in sql
