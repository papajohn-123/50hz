from datetime import UTC, datetime

import httpx
import pytest

from app.events.models import EvidenceFact
from app.intelligence.budget import DailyCallBudget
from app.intelligence.client import OpenRouterExplanationClient
from app.intelligence.models import EvidencePacket, GroundedExplanation, SourceCitation
from app.intelligence.validation import ExplanationValidationError, validate_explanation


NOW = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)


def packet() -> EvidencePacket:
    return EvidencePacket(
        event_id="evt_1",
        revision=1,
        event_type="reported_unit_unavailability",
        status="open",
        as_of=NOW,
        freshness="fresh",
        facts=[
            EvidenceFact(
                fact_id="capacity",
                metric="unavailable_capacity",
                label="reported unavailable capacity",
                value=610,
                unit="MW",
                observed_at=NOW,
                source_record_ids=["src_1"],
            )
        ],
        unknowns=["Cause has not been reported"],
        source_refs={
            "src_1": SourceCitation(
                source_id="src_1",
                publisher="Elexon",
                title="REMIT notice",
                canonical_url="https://bmrs.elexon.co.uk/",
            )
        },
        cause_reported=False,
    )


def test_validation_rejects_invented_number() -> None:
    explanation = GroundedExplanation(
        headline="Unit unavailable",
        plain_language="The notice reports 999 MW unavailable.",
        evidence_refs=["src_1"],
    )
    with pytest.raises(ExplanationValidationError):
        validate_explanation(explanation, packet())


def test_validation_rejects_unsupported_causation() -> None:
    explanation = GroundedExplanation(
        headline="Unit unavailable",
        plain_language="The event caused imports to rise.",
        evidence_refs=["src_1"],
    )
    with pytest.raises(ExplanationValidationError):
        validate_explanation(explanation, packet())


@pytest.mark.asyncio
async def test_client_falls_back_on_invalid_model_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test",
                "choices": [{"message": {"content": '{"headline":"Bad","plain_language":"999 MW","evidence_refs":["src_1"]}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    client = OpenRouterExplanationClient(
        api_key="temporary",
        model="test",
        base_url="https://openrouter.test",
        public_base_url="https://50hz.test",
        timeout_seconds=1,
        budget=DailyCallBudget(1),
        transport=httpx.MockTransport(handler),
    )
    result = await client.explain(packet())
    await client.close()
    assert result.used_fallback is True
    assert result.model == "deterministic"

