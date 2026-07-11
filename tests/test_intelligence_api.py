from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.notices import reported_notice_event_id
from app.intelligence.api import get_intelligence_runtime
from app.intelligence.ask import AskAnswer, AskUnavailableError
from app.intelligence.models import (
    ExplanationResult,
    GroundedExplanation,
    SourceCitation,
)
from app.intelligence.provider import DatabaseGridToolProvider
from app.intelligence.service import EventExplanationService, ExplainedEvent
from app.main import app
from app.persistence.reads import ReportedNoticeRead, SourceMetadataRead


NOW = datetime(2026, 7, 11, 14, 30, tzinfo=UTC)
CITATION = SourceCitation(
    source_id="elexon.indo",
    publisher="Elexon",
    title="Initial demand outturn",
    canonical_url="https://bmrs.elexon.co.uk/api-documentation",
)


class FakeAskClient:
    async def ask(self, request):
        assert request.question == "What is demand?"
        assert request.region_code == "13"
        return AskAnswer(
            answer="National demand is 38400 MW.",
            as_of=NOW,
            freshness="fresh",
            evidence_refs=["elexon.indo"],
            citations=[CITATION],
            limitations=[],
            suggested_questions=["How has demand changed?"],
        )


class FakeExplanationService:
    async def explain(self, event_id: str) -> ExplainedEvent:
        assert event_id == "evt_123"
        return ExplainedEvent(
            event_id="evt_123",
            revision=2,
            explanation=GroundedExplanation(
                headline="Wind is leading",
                plain_language="Wind is the largest generation source.",
                evidence_refs=["elexon.indo"],
            ),
            citations=(CITATION,),
            model="test-model",
            used_fallback=False,
        )


def _runtime():
    return SimpleNamespace(
        ask_client=FakeAskClient(),
        explanation_service=FakeExplanationService(),
    )


def test_ask_route_uses_mobile_camel_case_and_server_citations() -> None:
    app.dependency_overrides[get_intelligence_runtime] = _runtime
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/ask",
                json={"question": "What is demand?", "regionCode": "13"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert datetime.fromisoformat(payload["asOf"].replace("Z", "+00:00")) == NOW
    assert payload["evidenceRefs"] == ["elexon.indo"]
    assert payload["citations"][0]["sourceID"] == "elexon.indo"
    assert payload["citations"][0]["canonicalURL"].startswith("https://bmrs")
    assert "canonical_url" not in payload["citations"][0]


def test_event_explanation_route_exposes_grounded_contract() -> None:
    app.dependency_overrides[get_intelligence_runtime] = _runtime
    try:
        with TestClient(app) as client:
            response = client.get("/v1/events/evt_123/explanation")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["eventID"] == "evt_123"
    assert payload["explanation"]["plainLanguage"].startswith("Wind")
    assert payload["usedFallback"] is False


def test_ask_route_returns_bounded_service_unavailable_error() -> None:
    class UnavailableClient:
        async def ask(self, request):
            raise AskUnavailableError("Ask the Grid has reached its daily usage limit")

    app.dependency_overrides[get_intelligence_runtime] = lambda: SimpleNamespace(
        ask_client=UnavailableClient(),
        explanation_service=FakeExplanationService(),
    )
    try:
        with TestClient(app) as client:
            response = client.post("/v1/ask", json={"question": "What changed?"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert "daily usage limit" in response.json()["detail"]


class _NoEventResult:
    def scalar_one_or_none(self):
        return None


class _NoEventSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement):
        return _NoEventResult()

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _reported_notice(cause: str | None) -> ReportedNoticeRead:
    return ReportedNoticeRead(
        id="notice-row-1",
        source_id="elexon.remit",
        notice_kind="remit_unavailability",
        external_id="mrid-50hz-1",
        revision_key="revision-2",
        revision_number=2,
        published_at=NOW - timedelta(minutes=10),
        retrieved_at=NOW - timedelta(minutes=1),
        event_start=NOW - timedelta(hours=1),
        event_end=NOW + timedelta(hours=2),
        heading="Unit unavailability",
        event_type="Unavailability",
        event_status="Active",
        affected_unit="Example Unit 1",
        asset_id="asset-1",
        fuel_type="nuclear",
        normal_capacity_mw=610,
        available_capacity_mw=0,
        unavailable_capacity_mw=610,
        reported_cause=cause,
        reported_related_information=None,
        warning_type=None,
        warning_text=None,
        evidence={"classification": "reported"},
    )


@pytest.mark.parametrize(
    ("cause", "expected_cause_reported"),
    (("Equipment repair work", True), (None, False)),
)
def test_real_reported_notice_id_gets_grounded_explanation(
    cause: str | None,
    expected_cause_reported: bool,
) -> None:
    notice = _reported_notice(cause)
    public_id = reported_notice_event_id(notice)
    source = SourceMetadataRead(
        id="elexon.remit",
        provider="Elexon",
        dataset="REMIT",
        display_name="Elexon REMIT notices",
        documentation_url="https://bmrs.elexon.co.uk/api-documentation",
        licence_url=None,
        attribution="Data supplied by Elexon.",
        expected_cadence_seconds=300,
    )

    class NoticeRepository:
        async def get_active_notices(self, *, as_of=None):
            return (notice,)

        async def list_sources(self):
            return (source,)

    class ExplanationClient:
        packet = None

        async def explain(self, packet):
            self.packet = packet
            return ExplanationResult(
                explanation=GroundedExplanation(
                    headline="Reported unit unavailability",
                    plain_language="The notice reports 610 MW unavailable.",
                    caveat=None if packet.cause_reported else "Cause has not been reported.",
                    evidence_refs=["elexon.remit"],
                ),
                model="test-model",
                used_fallback=False,
            )

    explanation_client = ExplanationClient()
    provider = DatabaseGridToolProvider(
        NoticeRepository(),
        lambda: _NoEventSession(),
        clock=lambda: NOW,
    )
    service = EventExplanationService(
        provider=provider,
        client=explanation_client,
        session_factory=lambda: _NoEventSession(),
        configured_model="test-model",
    )
    app.dependency_overrides[get_intelligence_runtime] = lambda: SimpleNamespace(
        ask_client=FakeAskClient(),
        explanation_service=service,
    )
    try:
        with TestClient(app) as client:
            response = client.get(f"/v1/events/{public_id}/explanation")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["eventID"] == public_id
    assert payload["revision"] == 2
    assert payload["citations"][0]["sourceID"] == "elexon.remit"
    assert explanation_client.packet.cause_reported is expected_cause_reported
