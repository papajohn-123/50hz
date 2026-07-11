import json
from datetime import UTC, datetime

import httpx
import pytest

from app.events.models import EvidenceFact
from app.intelligence.budget import DailyCallBudget
from app.intelligence.client import OpenRouterExplanationClient, _strict_response_schema
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


def test_validation_allows_time_tokens_present_in_string_evidence() -> None:
    value = packet().model_copy(
        update={
            "facts": [
                EvidenceFact(
                    fact_id="start",
                    metric="reported_event_start",
                    label="reported event starts",
                    value="2026-07-11T13:30:00+00:00",
                    observed_at=NOW,
                    source_record_ids=["src_1"],
                )
            ]
        }
    )
    explanation = GroundedExplanation(
        headline="Event timing",
        plain_language="The notice reports a start time of 13:30.",
        evidence_refs=["src_1"],
    )
    validate_explanation(explanation, value)


@pytest.mark.asyncio
async def test_client_falls_back_on_invalid_model_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["provider"] == {"zdr": True}
        schema = payload["response_format"]["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert schema["properties"]["evidence_refs"]["items"]["enum"] == [
            "src_1"
        ]
        assert "default" not in schema["properties"]["why_it_matters"]
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


def test_strict_schema_requires_nullable_and_defaulted_fields_without_defaults() -> None:
    schema = _strict_response_schema(GroundedExplanation.model_json_schema())

    assert set(schema["required"]) == {
        "headline",
        "plain_language",
        "why_it_matters",
        "caveat",
        "evidence_refs",
        "suggested_questions",
    }
    assert schema["additionalProperties"] is False
    assert "default" not in schema["properties"]["caveat"]
    assert "default" not in schema["properties"]["why_it_matters"]
