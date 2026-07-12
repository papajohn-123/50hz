from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.events.models import EventStatus
from app.events.revisions import EventAuthority
from app.persistence.reads import (
    GridReadRepository,
    _event_lifecycle_history_statement,
)


EVENT_ID = "evt_" + "a" * 20
FIRST = datetime(2026, 7, 10, 9, tzinfo=UTC)


def row(revision: int, *, status: EventStatus, changes: list[dict[str, Any]] | None):
    published = FIRST + timedelta(hours=revision - 1)
    return {
        "event_id": EVENT_ID,
        "revision_number": revision,
        "status": status,
        "authority": EventAuthority.AUTHORITATIVE_NOTICE.value,
        "published_at": published,
        "effective_start": FIRST + timedelta(days=1),
        "effective_end": FIRST + timedelta(days=2),
        "asset_id": "eic-asset-1",
        "asset_name": "Example Unit 1",
        "asset_identity_reliable": True,
        "unavailable_mw": 400.0,
        "normal_capacity_mw": 600.0,
        "planned": False,
        "reported_cause": "The participant reports equipment repair work.",
        "evidence_checksum": f"{revision}" * 64,
        "material_reason": None if revision == 1 else "Source revised capacity",
        "superseded_by_event_id": None,
        "source_ids": ["elexon.remit"],
        "source_record_ids": [f"elexon:REMIT:example:r{revision}"],
        "from_revision": revision - 1 if revision > 1 else None,
        "to_revision": revision if revision > 1 else None,
        "changes": changes,
        "first_published_at": FIRST,
        "latest_published_at": FIRST + timedelta(hours=2),
        "total_revision_count": 3,
    }


class FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> FakeMappings:
        return FakeMappings(self.rows)


class FakeSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.rows)


def test_repository_reads_newest_public_safe_revision_slice_with_deltas() -> None:
    rows = [
        row(
            3,
            status=EventStatus.RESOLVED,
            changes=[
                {"field": "status", "before": "updated", "after": "resolved"},
                {
                    "field": "material_reason",
                    "before": "Source revised capacity",
                    "after": "Source marked the notice resolved",
                },
            ],
        ),
        row(
            2,
            status=EventStatus.UPDATED,
            changes=[
                {"field": "unavailable_mw", "before": 500.0, "after": 400.0},
                {
                    "field": "evidence_checksum",
                    "before": "1" * 64,
                    "after": "2" * 64,
                },
            ],
        ),
        row(1, status=EventStatus.OPEN, changes=None),
    ]
    session = FakeSession(rows)
    repository = GridReadRepository(lambda: session)

    history = asyncio.run(repository.get_event_lifecycle_history(EVENT_ID))

    assert history is not None
    assert history.current.status is EventStatus.RESOLVED
    assert [item.revision_number for item in history.revisions] == [3, 2, 1]
    assert [item.field.value for item in history.revisions[1].changes] == [
        "unavailable_mw",
        "evidence_checksum",
    ]
    assert history.first_published_at == FIRST
    assert history.latest_published_at == FIRST + timedelta(hours=2)
    assert history.is_truncated is False


def test_history_query_is_bounded_reported_only_and_never_selects_private_columns() -> None:
    statement = _event_lifecycle_history_statement(EVENT_ID, limit=37)
    sql = str(statement).lower()

    assert "event_lifecycle_revisions.event_id" in sql
    assert "event_lifecycle_revisions.event_kind" in sql
    assert "event_lifecycle_revisions.evidence_class" in sql
    assert "order by event_lifecycle_revisions.revision_number desc" in sql
    assert statement._limit_clause.value == 37
    for forbidden in (
        "event_lifecycle_revisions.payload",
        "event_lifecycle_revisions.created_at",
        "event_lifecycle_revisions.id,",
        "warning_text",
        "request_url",
        "raw_payload",
        " error",
    ):
        assert forbidden not in sql


def test_repository_returns_none_for_unknown_and_rejects_unbounded_inputs() -> None:
    session = FakeSession([])
    repository = GridReadRepository(lambda: session)

    assert asyncio.run(repository.get_event_lifecycle_history(EVENT_ID)) is None
    with pytest.raises(ValueError, match="stable public"):
        asyncio.run(repository.get_event_lifecycle_history("not-an-event"))
    with pytest.raises(ValueError, match="between 1 and 100"):
        asyncio.run(repository.get_event_lifecycle_history(EVENT_ID, limit=101))
    assert len(session.statements) == 1


def test_later_revision_without_audited_delta_is_rejected() -> None:
    session = FakeSession([row(2, status=EventStatus.UPDATED, changes=None)])
    repository = GridReadRepository(lambda: session)

    with pytest.raises(ValueError, match="audited delta"):
        asyncio.run(repository.get_event_lifecycle_history(EVENT_ID))
