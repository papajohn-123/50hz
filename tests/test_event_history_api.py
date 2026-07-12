from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

from fastapi.testclient import TestClient

from app.api.dependencies import get_grid_read_repository
from app.events.models import EventStatus
from app.events.revisions import (
    EventAuthority,
    RevisionField,
    RevisionFieldDelta,
)
from app.main import app
from app.persistence.reads import (
    EventLifecycleHistoryRead,
    EventLifecycleRevisionRead,
)


EVENT_ID = "evt_" + "b" * 20
FIRST = datetime(2026, 7, 10, 8, tzinfo=UTC)


def revision(
    number: int,
    status: EventStatus,
    *,
    changes: tuple[RevisionFieldDelta, ...] = (),
) -> EventLifecycleRevisionRead:
    return EventLifecycleRevisionRead(
        revision_number=number,
        status=status,
        authority=EventAuthority.AUTHORITATIVE_NOTICE,
        published_at=FIRST + timedelta(hours=number - 1),
        effective_start=FIRST + timedelta(days=1),
        effective_end=FIRST + timedelta(days=2),
        asset_id="10X1001A1001A001",
        asset_name="Example Unit 1",
        asset_identity_reliable=True,
        unavailable_mw=350,
        normal_capacity_mw=600,
        planned=False,
        reported_cause="The participant reports equipment repair work.",
        evidence_checksum=f"{number}" * 64,
        material_reason=None if number == 1 else "Source revised status",
        superseded_by_event_id=None,
        source_ids=("elexon.remit",),
        source_record_ids=(f"elexon:REMIT:example:r{number}",),
        changes=changes,
    )


HISTORY = EventLifecycleHistoryRead(
    event_id=EVENT_ID,
    first_published_at=FIRST,
    latest_published_at=FIRST + timedelta(hours=2),
    total_revision_count=3,
    revisions=(
        revision(
            3,
            EventStatus.RESOLVED,
            changes=(
                RevisionFieldDelta(
                    field=RevisionField.STATUS,
                    before=EventStatus.UPDATED,
                    after=EventStatus.RESOLVED,
                ),
            ),
        ),
        revision(
            2,
            EventStatus.UPDATED,
            changes=(
                RevisionFieldDelta(
                    field=RevisionField.UNAVAILABLE_MW,
                    before=500.0,
                    after=350.0,
                ),
            ),
        ),
        revision(1, EventStatus.OPEN),
    ),
)


class FakeRepository:
    def __init__(self, history: EventLifecycleHistoryRead | None = HISTORY) -> None:
        self.history = history
        self.calls: list[tuple[str, int]] = []

    async def get_event_lifecycle_history(
        self,
        event_id: str,
        *,
        limit: int,
    ) -> EventLifecycleHistoryRead | None:
        self.calls.append((event_id, limit))
        return self.history


def request(repository: FakeRepository, path: str, **kwargs):
    app.dependency_overrides[get_grid_read_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            return client.get(path, **kwargs)
    finally:
        app.dependency_overrides.clear()


def test_terminal_inactive_event_history_is_public_safe_and_camel_case() -> None:
    repository = FakeRepository()
    response = request(repository, f"/v1/events/{EVENT_ID}/history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["eventID"] == EVENT_ID
    assert payload["lifecycleStatus"] == "resolved"
    assert payload["revisionOrder"] == "newestFirst"
    assert payload["revisionCount"] == 3
    assert payload["firstPublishedAt"] == FIRST.isoformat().replace("+00:00", "Z")
    assert [item["revisionNumber"] for item in payload["revisions"]] == [3, 2, 1]
    assert payload["revisions"][0]["changes"] == [
        {"field": "status", "before": "updated", "after": "resolved"}
    ]
    assert payload["revisions"][1]["changes"][0]["field"] == "unavailableMW"
    assert payload["revisions"][0]["reportedAsset"]["assetID"]
    assert payload["revisions"][0]["reportedCapacity"]["normalCapacityMW"] == 600
    assert payload["revisions"][0]["sourceIDs"] == ["elexon.remit"]
    assert repository.calls == [(EVENT_ID, 100)]

    serialized = json.dumps(payload).casefold()
    for forbidden in (
        "warningtext",
        "warning text",
        "rawpayload",
        "requesturl",
        "databaseid",
        "createdat",
        "internalerror",
    ):
        assert forbidden not in serialized


def test_history_limit_is_bounded_and_forwarded() -> None:
    repository = FakeRepository()
    response = request(repository, f"/v1/events/{EVENT_ID}/history?limit=2")
    too_large = request(repository, f"/v1/events/{EVENT_ID}/history?limit=101")

    assert response.status_code == 200
    assert repository.calls[0] == (EVENT_ID, 2)
    assert too_large.status_code == 422


def test_unknown_and_malformed_ids_share_404_without_malformed_query() -> None:
    unknown = FakeRepository(history=None)
    malformed = FakeRepository()

    unknown_response = request(unknown, f"/v1/events/{EVENT_ID}/history")
    malformed_response = request(malformed, "/v1/events/not-an-event/history")

    assert unknown_response.status_code == 404
    assert malformed_response.status_code == 404
    assert unknown_response.json() == malformed_response.json()
    assert unknown.calls == [(EVENT_ID, 100)]
    assert malformed.calls == []


def test_history_has_sixty_second_etag_and_conditional_response() -> None:
    repository = FakeRepository()
    app.dependency_overrides[get_grid_read_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            first = client.get(f"/v1/events/{EVENT_ID}/history")
            second = client.get(
                f"/v1/events/{EVENT_ID}/history",
                headers={"If-None-Match": first.headers["etag"]},
            )
    finally:
        app.dependency_overrides.clear()

    assert first.headers["cache-control"].startswith("public, max-age=60")
    assert second.status_code == 304
    assert second.content == b""


def test_openapi_publishes_camel_case_bounded_history_schema() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/v1/events/{event_id}/history"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("EventHistoryResponse")
    model = schema["components"]["schemas"]["EventHistoryResponse"]
    assert "eventID" in model["properties"]
    assert "firstPublishedAt" in model["properties"]
    revisions = schema["components"]["schemas"]["EventHistoryRevision"]
    assert "sourceRecordIDs" in revisions["properties"]
