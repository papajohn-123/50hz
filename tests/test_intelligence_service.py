import uuid
from datetime import UTC, datetime

import pytest

from app.db.models import DetectedEvent, EventExplanation
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
from app.intelligence.service import EventExplanationService, PROMPT_VERSION


NOW = datetime(2026, 7, 11, 16, tzinfo=UTC)
EVENT_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")
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
    def __init__(self, *, result=None):
        self.result = result
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement):
        return Result(self.result)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        return None


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
