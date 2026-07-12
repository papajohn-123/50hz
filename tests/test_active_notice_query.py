from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.persistence.reads import (
    MAX_ACTIVE_NOTICE_CANDIDATES,
    ReportedNoticeRead,
    _latest_notice_revisions_statement,
    _notice_is_active,
)


NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


def notice(*, status: str | None, end: datetime | None) -> ReportedNoticeRead:
    return ReportedNoticeRead(
        id="internal-row-not-used-publicly",
        source_id="elexon.remit",
        notice_kind="remit_unavailability",
        external_id="mrid-example",
        revision_key="r2",
        revision_number=2,
        published_at=NOW - timedelta(minutes=5),
        retrieved_at=NOW - timedelta(minutes=4),
        event_start=NOW - timedelta(hours=1),
        event_end=end,
        heading=None,
        event_type=None,
        event_status=status,
        affected_unit=None,
        asset_id=None,
        fuel_type=None,
        normal_capacity_mw=None,
        available_capacity_mw=None,
        unavailable_capacity_mw=None,
        reported_cause=None,
        reported_related_information=None,
        warning_type=None,
        warning_text=None,
        evidence={},
    )


def test_active_notice_query_bounds_identities_before_ranking_and_latest_rows_after() -> None:
    statement = _latest_notice_revisions_statement(
        NOW,
        warning_fresh_for=timedelta(minutes=15),
    )
    sql = " ".join(str(statement).lower().split())

    assert "select distinct" in sql
    assert "row_number() over" in sql
    assert "notice_rank" in sql
    assert "reported_notices.event_start" in sql
    assert "reported_notices.published_at" in sql
    assert "event_status" in sql
    assert "notice_kind desc" in sql
    assert statement._limit_clause.value == MAX_ACTIVE_NOTICE_CANDIDATES == 500


def test_terminal_latest_revision_cannot_resurrect_an_earlier_active_window() -> None:
    future = NOW + timedelta(hours=3)

    assert _notice_is_active(
        notice(status="Active", end=future),
        as_of=NOW,
        warning_fresh_for=timedelta(minutes=15),
    )
    for terminal in (
        "Cancelled",
        "Resolved",
        "Completed",
        "Superseded",
        "Withdrawn",
    ):
        assert not _notice_is_active(
            notice(status=terminal, end=future),
            as_of=NOW,
            warning_fresh_for=timedelta(minutes=15),
        )
