from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.events.identity import reported_notice_event_id
from app.events.models import EventStatus
from app.events.revisions import EventAuthority, RevisionField, diff_revisions
from app.persistence.event_lifecycle import (
    build_reported_notice_revision,
    materialize_reported_notice_rows,
)


NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


class FakeScalars:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


class FakeResult:
    def __init__(self, values: list[Any] | None = None) -> None:
        self.values = values or []

    def scalars(self) -> FakeScalars:
        return FakeScalars(self.values)


class FakeSession:
    def __init__(self, responses: list[FakeResult]) -> None:
        self.responses = list(responses)
        self.executed: list[Any] = []

    async def execute(self, statement: Any) -> FakeResult:
        self.executed.append(statement)
        assert self.responses, "unexpected lifecycle persistence statement"
        return self.responses.pop(0)


def notice(
    *,
    revision: int = 1,
    checksum: str = "a" * 64,
    status: str | None = "Active",
    unavailable_mw: float = 500,
    retrieved_at: datetime = NOW,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source_id": "elexon.remit",
        "notice_kind": "remit_unavailability",
        "external_id": "mrid-1",
        "revision_key": f"r{revision}",
        "revision_number": revision,
        "source_record_id": f"elexon:REMIT:mrid-1:r{revision}",
        "content_sha256": checksum,
        "published_at": NOW + timedelta(minutes=revision),
        "retrieved_at": retrieved_at,
        "event_start": NOW - timedelta(hours=1),
        "event_end": NOW + timedelta(hours=5),
        "heading": "Reported unit unavailability",
        "event_status": status,
        "unavailability_type": "Unplanned",
        "asset_id": "asset-1",
        "affected_unit": "Example Unit",
        "affected_unit_eic": "48W000000000001A",
        "normal_capacity_mw": 1_000,
        "available_capacity_mw": 1_000 - unavailable_mw,
        "unavailable_capacity_mw": unavailable_mw,
        "reported_cause": "Participant reports equipment work",
        "warning_type": None,
        "warning_text": None,
        "evidence": evidence or {"classification": "reported"},
    }


def warning_notice(
    *,
    checksum: str,
    warning_text: str,
    retrieved_at: datetime,
) -> dict[str, Any]:
    return {
        "source_id": "elexon.syswarn",
        "notice_kind": "system_warning",
        "external_id": "syswarn:stable-publication",
        "revision_key": checksum,
        "revision_number": None,
        "source_record_id": f"elexon:SYSWARN:{checksum[:16]}",
        "content_sha256": checksum,
        "published_at": NOW,
        "retrieved_at": retrieved_at,
        "event_start": None,
        "event_end": None,
        "heading": None,
        "event_status": None,
        "unavailability_type": None,
        "asset_id": None,
        "affected_unit": None,
        "affected_unit_eic": None,
        "normal_capacity_mw": None,
        "available_capacity_mw": None,
        "unavailable_capacity_mw": None,
        "reported_cause": None,
        "warning_type": "System Warning",
        "warning_text": warning_text,
        "evidence": {
            "classification": "reported",
            "warningTextSha256": checksum,
        },
    }


def stored_revision(row: dict[str, Any], *, local_revision: int) -> Any:
    previous = None
    if local_revision != 1:
        raise AssertionError("test helper currently stores the initial revision only")
    revision = build_reported_notice_revision(
        row,
        revision_number=local_revision,
        previous=previous,
    )
    return SimpleNamespace(
        event_id=revision.event_id,
        payload={
            "revision": revision.model_dump(mode="json"),
            "sourceNotice": {
                "sourceRevisionNumber": row["revision_number"],
                "sourceRetrievedAt": row["retrieved_at"].isoformat(),
                "revisionKey": row["revision_key"],
            },
        },
    )


def test_correction_maps_reported_fields_and_produces_an_audited_delta() -> None:
    first_row = notice()
    corrected_row = notice(
        checksum="b" * 64,
        unavailable_mw=700,
        retrieved_at=NOW + timedelta(minutes=2),
    )
    first = build_reported_notice_revision(first_row, revision_number=1)
    corrected = build_reported_notice_revision(
        corrected_row,
        revision_number=2,
        previous=first,
    )

    delta = diff_revisions(first, corrected)

    assert first.status is EventStatus.OPEN
    assert corrected.status is EventStatus.UPDATED
    assert corrected.authority is EventAuthority.AUTHORITATIVE_NOTICE
    assert corrected.asset_id == "asset-1"
    assert corrected.asset_identity_reliable is True
    assert corrected.planned is False
    assert corrected.reported_cause == "Participant reports equipment work"
    assert corrected.material_reason == "Source revised capacity"
    assert delta.changed_fields == (
        RevisionField.UNAVAILABLE_MW,
        RevisionField.STATUS,
        RevisionField.EVIDENCE_CHECKSUM,
        RevisionField.MATERIAL_REASON,
    )


def test_system_warning_correction_uses_reported_warning_authority() -> None:
    first_row = warning_notice(
        checksum="a" * 64,
        warning_text="First published warning text",
        retrieved_at=NOW,
    )
    corrected_row = warning_notice(
        checksum="b" * 64,
        warning_text="Corrected published warning text",
        retrieved_at=NOW + timedelta(minutes=1),
    )
    first = build_reported_notice_revision(first_row, revision_number=1)
    corrected = build_reported_notice_revision(
        corrected_row,
        revision_number=2,
        previous=first,
    )

    delta = diff_revisions(first, corrected)

    assert first.authority is EventAuthority.SYSTEM_WARNING
    assert first.status is EventStatus.OPEN
    assert corrected.status is EventStatus.UPDATED
    assert corrected.asset_id is None
    assert corrected.reported_cause is None
    assert delta.changed_fields == (
        RevisionField.STATUS,
        RevisionField.EVIDENCE_CHECKSUM,
        RevisionField.MATERIAL_REASON,
    )


@pytest.mark.parametrize(
    ("source_status", "expected", "evidence"),
    [
        ("Cancelled", EventStatus.WITHDRAWN, None),
        ("Resolved", EventStatus.RESOLVED, None),
        (
            "Superseded",
            EventStatus.SUPERSEDED,
            {"supersededByExternalId": "mrid-replacement"},
        ),
    ],
)
def test_reported_terminal_statuses_are_preserved(
    source_status: str,
    expected: EventStatus,
    evidence: dict[str, Any] | None,
) -> None:
    first = build_reported_notice_revision(notice(), revision_number=1)
    terminal_row = notice(
        revision=2,
        checksum="b" * 64,
        status=source_status,
        evidence=evidence,
        retrieved_at=NOW + timedelta(minutes=2),
    )

    terminal = build_reported_notice_revision(
        terminal_row,
        revision_number=2,
        previous=first,
    )

    assert terminal.status is expected
    assert terminal.material_reason == f"Source marked the notice {expected.value}"
    if expected is EventStatus.SUPERSEDED:
        assert terminal.superseded_by_event_id == reported_notice_event_id(
            source_id="elexon.remit",
            notice_kind="remit_unavailability",
            external_id="mrid-replacement",
        )
    else:
        assert terminal.superseded_by_event_id is None


def test_superseded_status_requires_an_explicit_reported_replacement() -> None:
    first = build_reported_notice_revision(notice(), revision_number=1)

    with pytest.raises(ValueError, match="reported replacement"):
        build_reported_notice_revision(
            notice(revision=2, checksum="b" * 64, status="Superseded"),
            revision_number=2,
            previous=first,
        )


def test_repeated_ingestion_is_a_ledger_no_op() -> None:
    row = notice()
    session = FakeSession([FakeResult([stored_revision(row, local_revision=1)])])

    outcome = asyncio.run(materialize_reported_notice_rows(session, [row]))

    assert outcome.revisions == 0
    assert outcome.deltas == 0
    assert outcome.unchanged == 1
    assert outcome.skipped == 0
    assert len(session.executed) == 1
    assert session.responses == []


def test_correction_appends_revision_and_delta_with_conflict_guards() -> None:
    first_row = notice()
    correction = notice(
        checksum="b" * 64,
        unavailable_mw=750,
        retrieved_at=NOW + timedelta(minutes=2),
    )
    session = FakeSession(
        [
            FakeResult([stored_revision(first_row, local_revision=1)]),
            FakeResult(),
            FakeResult(),
        ]
    )

    outcome = asyncio.run(materialize_reported_notice_rows(session, [correction]))

    assert outcome.revisions == 1
    assert outcome.deltas == 1
    assert outcome.unchanged == 0
    assert outcome.skipped == 0
    writes = session.executed[1:]
    assert [statement.table.name for statement in writes] == [
        "event_lifecycle_revisions",
        "event_lifecycle_deltas",
    ]
    assert all(
        "ON CONFLICT" in str(statement.compile(dialect=postgresql.dialect())).upper()
        for statement in writes
    )
    revision_params = writes[0].compile(dialect=postgresql.dialect()).params
    delta_params = writes[1].compile(dialect=postgresql.dialect()).params
    assert revision_params["revision_number"] == 2
    assert revision_params["event_kind"] == "reported"
    assert revision_params["evidence_class"] == "reported"
    assert delta_params["from_revision"] == 1
    assert delta_params["to_revision"] == 2


def test_publication_after_terminal_state_is_not_appended() -> None:
    first = build_reported_notice_revision(notice(), revision_number=1)
    withdrawn_row = notice(revision=2, checksum="b" * 64, status="Cancelled")
    withdrawn = build_reported_notice_revision(
        withdrawn_row,
        revision_number=2,
        previous=first,
    )
    stored = SimpleNamespace(
        event_id=withdrawn.event_id,
        payload={"revision": withdrawn.model_dump(mode="json")},
    )
    later = notice(revision=3, checksum="c" * 64, status="Active")
    session = FakeSession([FakeResult([stored])])

    outcome = asyncio.run(materialize_reported_notice_rows(session, [later]))

    assert outcome.revisions == 0
    assert outcome.deltas == 0
    assert outcome.skipped == 1
    assert len(session.executed) == 1


def test_invalid_lifecycle_projection_does_not_block_normalized_ingestion() -> None:
    invalid = notice(checksum="not-a-sha256")
    session = FakeSession([])

    outcome = asyncio.run(materialize_reported_notice_rows(session, [invalid]))

    assert outcome.revisions == 0
    assert outcome.deltas == 0
    assert outcome.unchanged == 0
    assert outcome.skipped == 1
    assert session.executed == []


def test_one_invalid_revision_does_not_block_other_event_lifecycles() -> None:
    invalid = notice(status="Superseded", evidence={"classification": "reported"})
    valid = warning_notice(
        checksum="c" * 64,
        warning_text="A valid independently reported warning",
        retrieved_at=NOW,
    )
    session = FakeSession([FakeResult(), FakeResult()])

    outcome = asyncio.run(materialize_reported_notice_rows(session, [invalid, valid]))

    assert outcome.revisions == 1
    assert outcome.deltas == 0
    assert outcome.skipped == 1
    assert len(session.executed) == 2
