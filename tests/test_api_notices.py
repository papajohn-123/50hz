from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.api.dependencies import get_grid_read_repository
from app.api.notices import (
    present_reported_notices,
    reported_notice_event_id,
    reported_notice_to_grid_event,
)
from app.main import app
from app.persistence.reads import ReportedNoticeRead


NOW = datetime(2026, 7, 11, 12, tzinfo=UTC)


def notice(
    *,
    kind: str = "remit_unavailability",
    revision: int = 1,
    unavailable_mw: float | None = 504,
    heading: str | None = None,
    affected_unit: str | None = None,
) -> ReportedNoticeRead:
    return ReportedNoticeRead(
        id=f"row-{revision}",
        source_id="elexon.remit" if kind == "remit_unavailability" else "elexon.syswarn",
        notice_kind=kind,
        external_id="mrid-1" if kind == "remit_unavailability" else "syswarn:1",
        revision_key=f"r{revision}",
        revision_number=revision,
        published_at=NOW - timedelta(minutes=10),
        retrieved_at=NOW - timedelta(minutes=1),
        event_start=NOW - timedelta(hours=1) if kind == "remit_unavailability" else None,
        event_end=NOW + timedelta(hours=1) if kind == "remit_unavailability" else None,
        heading=(heading or "Unit unavailability") if kind == "remit_unavailability" else None,
        event_type="Unavailability" if kind == "remit_unavailability" else None,
        event_status="Active" if kind == "remit_unavailability" else None,
        affected_unit=(affected_unit or "Example Unit 1") if kind == "remit_unavailability" else None,
        asset_id="asset-1" if kind == "remit_unavailability" else None,
        fuel_type="nuclear" if kind == "remit_unavailability" else None,
        normal_capacity_mw=610 if kind == "remit_unavailability" else None,
        available_capacity_mw=106 if kind == "remit_unavailability" else None,
        unavailable_capacity_mw=unavailable_mw if kind == "remit_unavailability" else None,
        reported_cause="Equipment repair work" if kind == "remit_unavailability" else None,
        reported_related_information=None,
        warning_type="System Warning" if kind == "system_warning" else None,
        warning_text=(
            "A tight system margin has been reported."
            if kind == "system_warning"
            else None
        ),
        evidence={"classification": "reported", "revisionNumber": revision},
    )


def test_notice_event_id_is_stable_across_reported_revisions() -> None:
    assert reported_notice_event_id(notice(revision=1)) == reported_notice_event_id(
        notice(revision=9)
    )


def test_remit_mapping_preserves_reported_semantics_and_source() -> None:
    event = reported_notice_to_grid_event(notice())
    assert event.evidence_class == "reported"
    assert event.is_authoritatively_reported is True
    assert event.source_ids == ["elexon.remit"]
    assert event.severity == "important"
    assert "reported unavailability of 504 MW" in event.summary
    assert "Reported cause: Equipment repair work" in event.summary


def test_system_warning_sorts_ahead_of_small_unavailability() -> None:
    events = present_reported_notices(
        (notice(unavailable_mw=50), notice(kind="system_warning"))
    )
    assert events[0].title == "System Warning"
    assert events[0].severity == "important"


def test_negative_capacity_field_is_not_presented_as_negative_unavailability() -> None:
    event = reported_notice_to_grid_event(notice(unavailable_mw=-12))

    assert event.severity == "info"
    assert "-12 MW" not in event.summary
    assert "do not state a positive unavailable amount" in event.summary


def test_generic_remit_heading_uses_the_affected_unit_as_the_public_title() -> None:
    event = reported_notice_to_grid_event(
        notice(heading="REMIT Information", affected_unit="DRAXX-4")
    )

    assert event.title == "DRAXX-4: reported unavailability"


class NoticeRepository:
    async def get_active_notices(self, **_: object) -> tuple[ReportedNoticeRead, ...]:
        return (notice(),)


def test_events_list_and_detail_share_the_stable_public_id() -> None:
    app.dependency_overrides[get_grid_read_repository] = lambda: NoticeRepository()
    try:
        with TestClient(app) as client:
            listing = client.get("/v1/events")
            event_id = listing.json()[0]["id"]
            detail = client.get(f"/v1/events/{event_id}")
            missing = client.get("/v1/events/evt_missing")
    finally:
        app.dependency_overrides.clear()

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["id"] == event_id
    assert detail.json()["evidenceClass"] == "reported"
    assert missing.status_code == 404
