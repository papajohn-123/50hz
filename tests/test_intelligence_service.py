import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    DetectedEvent,
    EventExplanation,
    ReportedNoticeExplanation,
)
from app.domain.enums import (
    EventSeverity,
    EventStatus,
    EvidenceConfidence,
)
from app.events.models import EvidenceFact
from app.intelligence.ask import EvidenceEnvelope
from app.intelligence.models import (
    ExplanationResult,
    GroundedExplanation,
    SourceCitation,
)
from app.intelligence.provider import GridEventNotFoundError
from app.intelligence.service import EventExplanationService, PROMPT_VERSION
from app.persistence.reads import ReportedNoticeRead


NOW = datetime(2026, 7, 11, 16, tzinfo=UTC)
EVENT_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
NOTICE_EVENT_ID = "evt_0123456789abcdef0123"
CITATION = SourceCitation(
    source_id="elexon.remit",
    publisher="Elexon",
    title="REMIT notices",
    canonical_url="https://bmrs.elexon.co.uk/api-documentation",
)
FACT = EvidenceFact(
    fact_id="capacity",
    metric="unavailable_capacity_mw",
    label="reported unavailable capacity",
    value=610,
    unit="MW",
    observed_at=NOW,
    source_record_ids=["remit:1"],
)


def event_row() -> DetectedEvent:
    return DetectedEvent(
        id=EVENT_UUID,
        deterministic_key="reported:unit:1",
        event_type="reported_unit_unavailability",
        status=EventStatus.OPEN,
        severity=EventSeverity.MATERIAL,
        confidence=EvidenceConfidence.AUTHORITATIVE,
        title="Unit unavailable",
        deterministic_summary="610 MW is reported unavailable.",
        rule_version="1",
        evidence_version=3,
        evidence_checksum="a" * 64,
        evidence={
            "evidence_class": "reported",
            "cause_reported": False,
            "unknowns": ["Cause has not been reported"],
            "facts": [FACT.model_dump(mode="json")],
        },
        source_ids=["elexon.remit"],
        related_asset_ids=["unit-1"],
        event_started_at=NOW,
        first_detected_at=NOW,
        last_observed_at=NOW,
    )


class Provider:
    def __init__(self) -> None:
        self.event = event_row()

    async def get_event_row(self, event_id: str):
        return self.event

    async def call(self, name: str, arguments: dict):
        assert name == "get_event_evidence"
        assert arguments["event_id"] == "evt_11111111222233334444555555555555"
        return EvidenceEnvelope(
            as_of=NOW,
            freshness="fresh",
            evidence_class="reported",
            facts=[FACT],
            source_refs={"elexon.remit": CITATION},
            limitations=[],
        )


class Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class Session:
    def __init__(self, *, result=None, commit_error: Exception | None = None):
        self.result = result
        self.commit_error = commit_error
        self.added = []
        self.statements = []
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(self.result)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        if self.commit_error:
            raise self.commit_error
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class Client:
    def __init__(self):
        self.packets = []

    async def explain(self, packet):
        self.packets.append(packet)
        return ExplanationResult(
            explanation=GroundedExplanation(
                headline="A unit has reported an unavailability",
                plain_language="The notice reports 610 MW unavailable.",
                caveat="Cause has not been reported.",
                evidence_refs=["elexon.remit"],
            ),
            model="provider/test-model",
            used_fallback=False,
            input_tokens=100,
            output_tokens=40,
        )


@pytest.mark.asyncio
async def test_service_builds_packet_and_persists_validated_explanation() -> None:
    read_session = Session()
    write_session = Session()
    sessions = iter((read_session, write_session))
    client = Client()
    service = EventExplanationService(
        provider=Provider(),
        client=client,
        session_factory=lambda: next(sessions),
        configured_model="test-model",
    )

    result = await service.explain("evt_any")

    assert result.revision == 3
    assert result.citations == (CITATION,)
    assert client.packets[0].cause_reported is False
    assert client.packets[0].unknowns == ["Cause has not been reported"]
    assert write_session.committed is True
    cached = write_session.added[0]
    assert cached.prompt_version == PROMPT_VERSION
    assert cached.model == "test-model"
    assert cached.structured_response["evidence_refs"] == ["elexon.remit"]


@pytest.mark.asyncio
async def test_service_reuses_validated_database_cache_without_model_call() -> None:
    cached = EventExplanation(
        event_id=EVENT_UUID,
        evidence_checksum="a" * 64,
        provider="openrouter",
        model="test-model",
        prompt_version=PROMPT_VERSION,
        locale="en-GB",
        status="succeeded",
        explanation="The notice reports 610 MW unavailable.",
        structured_response=GroundedExplanation(
            headline="Reported unavailability",
            plain_language="The notice reports 610 MW unavailable.",
            evidence_refs=["elexon.remit"],
        ).model_dump(mode="json"),
        input_tokens=100,
        output_tokens=40,
    )

    class NeverClient:
        async def explain(self, packet):
            raise AssertionError("cached explanations must not call OpenRouter")

    service = EventExplanationService(
        provider=Provider(),
        client=NeverClient(),
        session_factory=lambda: Session(result=cached),
        configured_model="test-model",
    )
    result = await service.explain("evt_any")
    assert result.used_fallback is False
    assert result.explanation.headline == "Reported unavailability"


def notice(*, revision_key: str = "revision-2") -> ReportedNoticeRead:
    return ReportedNoticeRead(
        id="notice-row-2",
        source_id="elexon.remit",
        notice_kind="remit_unavailability",
        external_id="mrid-cache-1",
        revision_key=revision_key,
        revision_number=2,
        published_at=NOW,
        retrieved_at=NOW,
        event_start=NOW,
        event_end=None,
        heading="Unit unavailability",
        event_type="Unavailability",
        event_status="Active",
        affected_unit="Example Unit 1",
        asset_id="asset-1",
        fuel_type="nuclear",
        normal_capacity_mw=610,
        available_capacity_mw=0,
        unavailable_capacity_mw=610,
        reported_cause=None,
        reported_related_information=None,
        warning_type=None,
        warning_text=None,
        evidence={"classification": "reported"},
    )


class NoticeProvider:
    def __init__(self, value: ReportedNoticeRead | None = None) -> None:
        self.notice = value or notice()

    async def get_event_row(self, event_id: str):
        raise GridEventNotFoundError(event_id)

    async def get_reported_notice(self, event_id: str) -> ReportedNoticeRead:
        assert event_id == NOTICE_EVENT_ID
        return self.notice

    async def reported_notice_evidence(self, value: ReportedNoticeRead):
        assert value is self.notice
        return EvidenceEnvelope(
            as_of=NOW,
            freshness="fresh",
            evidence_class="reported",
            facts=[FACT],
            source_refs={"elexon.remit": CITATION},
            limitations=[],
        )


@pytest.mark.asyncio
async def test_reported_notice_explanation_is_cached_by_public_id_and_revision() -> None:
    read_session = Session()
    write_session = Session()
    sessions = iter((read_session, write_session))
    client = Client()
    service = EventExplanationService(
        provider=NoticeProvider(),
        client=client,
        session_factory=lambda: next(sessions),
        configured_model="test-model",
    )

    result = await service.explain(NOTICE_EVENT_ID)

    assert result.revision == 2
    assert len(client.packets) == 1
    assert write_session.committed is True
    cached = write_session.added[0]
    assert isinstance(cached, ReportedNoticeExplanation)
    assert cached.public_event_id == NOTICE_EVENT_ID
    assert cached.notice_revision_key == "revision-2"
    assert cached.notice_revision_number == 2
    assert cached.model == "test-model"
    assert cached.prompt_version == PROMPT_VERSION
    query_values = set(read_session.statements[0].compile().params.values())
    assert {
        NOTICE_EVENT_ID,
        "revision-2",
        "openrouter",
        "test-model",
        PROMPT_VERSION,
        "en-GB",
        "succeeded",
    }.issubset(query_values)


@pytest.mark.asyncio
async def test_reported_notice_explanation_reuses_validated_cache() -> None:
    cached = ReportedNoticeExplanation(
        public_event_id=NOTICE_EVENT_ID,
        notice_revision_key="revision-2",
        notice_revision_number=2,
        provider="openrouter",
        model="test-model",
        prompt_version=PROMPT_VERSION,
        locale="en-GB",
        status="succeeded",
        explanation="The notice reports 610 MW unavailable.",
        structured_response=GroundedExplanation(
            headline="Reported unavailability",
            plain_language="The notice reports 610 MW unavailable.",
            evidence_refs=["elexon.remit"],
        ).model_dump(mode="json"),
        input_tokens=100,
        output_tokens=40,
    )

    class NeverClient:
        async def explain(self, packet):
            raise AssertionError("cached notice explanations must not call OpenRouter")

    service = EventExplanationService(
        provider=NoticeProvider(),
        client=NeverClient(),
        session_factory=lambda: Session(result=cached),
        configured_model="test-model",
    )
    result = await service.explain(NOTICE_EVENT_ID)
    assert result.used_fallback is False
    assert result.explanation.headline == "Reported unavailability"


@pytest.mark.asyncio
async def test_reported_notice_fallback_is_not_cached() -> None:
    class FallbackClient:
        async def explain(self, packet):
            return ExplanationResult(
                explanation=GroundedExplanation(
                    headline="Deterministic fallback",
                    plain_language="The notice reports 610 MW unavailable.",
                    evidence_refs=["elexon.remit"],
                ),
                model="deterministic",
                used_fallback=True,
            )

    sessions = iter((Session(),))
    service = EventExplanationService(
        provider=NoticeProvider(),
        client=FallbackClient(),
        session_factory=lambda: next(sessions),
        configured_model="test-model",
    )
    result = await service.explain(NOTICE_EVENT_ID)
    assert result.used_fallback is True


@pytest.mark.asyncio
async def test_concurrent_reported_notice_cache_insert_is_tolerated() -> None:
    read_session = Session()
    write_session = Session(
        commit_error=IntegrityError(
            "insert reported notice explanation",
            {},
            RuntimeError("unique cache key"),
        )
    )
    sessions = iter((read_session, write_session))
    service = EventExplanationService(
        provider=NoticeProvider(),
        client=Client(),
        session_factory=lambda: next(sessions),
        configured_model="test-model",
    )

    result = await service.explain(NOTICE_EVENT_ID)

    assert result.used_fallback is False
    assert write_session.rolled_back is True
