import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.events.models import EvidenceFact
from app.intelligence.ask import (
    AskRequest,
    AskUnavailableError,
    EvidenceEnvelope,
    OpenRouterAskClient,
)
from app.intelligence.budget import DailyCallBudget
from app.intelligence.models import SourceCitation


NOW = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)


class Provider:
    async def call(self, name: str, arguments: dict[str, Any]) -> EvidenceEnvelope:
        assert name == "get_current_grid_state"
        return EvidenceEnvelope(
            as_of=NOW,
            freshness="fresh",
            evidence_class="observed",
            facts=[
                EvidenceFact(
                    fact_id="demand",
                    metric="demand_mw",
                    label="national demand",
                    value=38_400,
                    unit="MW",
                    observed_at=NOW,
                    source_record_ids=["elexon:indo:1"],
                )
            ],
            source_refs={
                "elexon": SourceCitation(
                    source_id="elexon",
                    publisher="Elexon",
                    title="Demand outturn",
                    canonical_url="https://bmrs.elexon.co.uk/",
                )
            },
        )


@pytest.mark.asyncio
async def test_ask_uses_server_citation_when_model_emits_unknown_ref() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        request_body = json.loads(request.content)
        assert request_body["provider"] == {"zdr": True}
        assert "Prefer exact evidence values" in request_body["messages"][0]["content"]
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "get_current_grid_state", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        answer = {
            "answer": "National demand is 38,400 MW.",
            "as_of": NOW.isoformat(),
            "freshness": "fresh",
            "evidence_refs": ["model-invented-source"],
            "limitations": [],
            "suggested_questions": ["How has demand changed?"],
        }
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(answer)}}]})

    client = OpenRouterAskClient(
        api_key="temporary",
        model="test",
        base_url="https://openrouter.test",
        public_base_url="https://50hz.test",
        timeout_seconds=1,
        budget=DailyCallBudget(2),
        provider=Provider(),
        transport=httpx.MockTransport(handler),
    )
    answer = await client.ask(AskRequest(question="What is demand?"))
    await client.close()
    assert calls == 2
    assert answer.evidence_refs == ["elexon"]
    assert answer.citations[0].source_id == "elexon"


def power_envelope() -> EvidenceEnvelope:
    return EvidenceEnvelope(
        as_of=NOW,
        freshness="fresh",
        evidence_class="observed",
        facts=[
            EvidenceFact(
                fact_id="wind",
                metric="wind_mw",
                label="wind generation",
                value=7_188,
                unit="MW",
                observed_at=NOW,
                source_record_ids=["fuelinst:wind:1"],
            )
        ],
        source_refs={
            "elexon": SourceCitation(
                source_id="elexon",
                publisher="Elexon",
                title="Generation mix",
                canonical_url="https://bmrs.elexon.co.uk/",
            )
        },
    )


@pytest.mark.asyncio
async def test_ask_allows_deterministically_rounded_megawatt_to_gigawatt_value() -> None:
    client = OpenRouterAskClient(
        api_key="temporary",
        model="test",
        base_url="https://openrouter.test",
        public_base_url="https://50hz.test",
        timeout_seconds=1,
        budget=DailyCallBudget(1),
        provider=Provider(),
    )
    content = json.dumps(
        {
            "answer": "Wind is generating 7.2 GW.",
            "suggested_questions": [],
        }
    )

    result = client._validate_final(
        content,
        [power_envelope()],
        AskRequest(question="What is happening on the grid?"),
    )
    await client.close()
    assert result.answer == "Wind is generating 7.2 GW."


@pytest.mark.parametrize(
    "claim",
    (
        "Wind is generating 7.2 MW.",
        "Wind is generating 7.2 Hz.",
        "Wind is generating 7.2.",
        "Wind is generating 7.3 GW.",
    ),
)
@pytest.mark.asyncio
async def test_ask_rejects_converted_value_with_wrong_unit_or_value(
    claim: str,
) -> None:
    client = OpenRouterAskClient(
        api_key="temporary",
        model="test",
        base_url="https://openrouter.test",
        public_base_url="https://50hz.test",
        timeout_seconds=1,
        budget=DailyCallBudget(1),
        provider=Provider(),
    )
    content = json.dumps({"answer": claim, "suggested_questions": []})

    with pytest.raises(AskUnavailableError, match="unsupported numerical claim"):
        client._validate_final(
            content,
            [power_envelope()],
            AskRequest(question="What is happening on the grid?"),
        )
    await client.close()


@pytest.mark.asyncio
async def test_daily_budget_counts_every_openrouter_round_trip() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_current_grid_state",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = OpenRouterAskClient(
        api_key="temporary",
        model="test",
        base_url="https://openrouter.test",
        public_base_url="https://50hz.test",
        timeout_seconds=1,
        budget=DailyCallBudget(1),
        provider=Provider(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AskUnavailableError, match="daily usage limit"):
        await client.ask(AskRequest(question="What is demand?"))
    await client.close()
    assert calls == 1


@pytest.mark.asyncio
async def test_ask_rejects_causality_without_an_explicit_reported_cause() -> None:
    client = OpenRouterAskClient(
        api_key="temporary",
        model="test",
        base_url="https://openrouter.test",
        public_base_url="https://50hz.test",
        timeout_seconds=1,
        budget=DailyCallBudget(1),
        provider=Provider(),
    )
    envelope = EvidenceEnvelope(
        as_of=NOW,
        freshness="fresh",
        evidence_class="observed",
        facts=[
            EvidenceFact(
                fact_id="wind",
                metric="wind_mw",
                label="wind generation",
                value=5_000,
                unit="MW",
                observed_at=NOW,
                source_record_ids=["fuelinst:1"],
            )
        ],
        source_refs={
            "elexon": SourceCitation(
                source_id="elexon",
                publisher="Elexon",
                title="Generation mix",
                canonical_url="https://bmrs.elexon.co.uk/",
            )
        },
    )
    content = json.dumps(
        {
            "answer": "Wind rose because a nuclear outage caused it.",
            "evidence_refs": ["elexon"],
            "suggested_questions": [],
        }
    )

    with pytest.raises(AskUnavailableError, match="unsupported causal claim"):
        client._validate_final(content, [envelope], AskRequest(question="Why did wind rise?"))
    await client.close()


@pytest.mark.asyncio
async def test_ask_rejects_a_number_not_present_in_evidence() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "get_current_grid_state",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        answer = {
            "answer": "National demand is 99,999 MW.",
            "evidence_refs": ["elexon"],
            "suggested_questions": [],
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(answer)}}]},
        )

    client = OpenRouterAskClient(
        api_key="temporary",
        model="test",
        base_url="https://openrouter.test",
        public_base_url="https://50hz.test",
        timeout_seconds=1,
        budget=DailyCallBudget(2),
        provider=Provider(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AskUnavailableError, match="unsupported numerical claim"):
        await client.ask(AskRequest(question="Is demand 99,999 MW?"))
    await client.close()


@pytest.mark.asyncio
async def test_ask_allows_time_tokens_from_a_string_fact_and_injects_map_time() -> None:
    selected = datetime(2026, 7, 11, 13, 30, tzinfo=UTC)

    class WindowProvider:
        def __init__(self) -> None:
            self.arguments = None

        async def call(self, name: str, arguments: dict[str, Any]) -> EvidenceEnvelope:
            self.arguments = arguments
            return EvidenceEnvelope(
                as_of=NOW,
                freshness="fresh",
                evidence_class="forecast",
                facts=[
                    EvidenceFact(
                        fact_id="window_start",
                        metric="cleanest_window_start",
                        label="cleanest forecast window starts",
                        value=selected.isoformat(),
                        observed_at=NOW,
                        source_record_ids=["forecast:1"],
                    )
                ],
                source_refs={
                    "neso": SourceCitation(
                        source_id="neso",
                        publisher="NESO",
                        title="Carbon forecast",
                        canonical_url="https://carbonintensity.org.uk/",
                    )
                },
            )

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "get_current_grid_state",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        answer = {
            "answer": "The cleanest window starts at 13:30.",
            "evidence_refs": ["neso"],
            "suggested_questions": [],
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(answer)}}]},
        )

    provider = WindowProvider()
    client = OpenRouterAskClient(
        api_key="temporary",
        model="test",
        base_url="https://openrouter.test",
        public_base_url="https://50hz.test",
        timeout_seconds=1,
        budget=DailyCallBudget(2),
        provider=provider,
        transport=httpx.MockTransport(handler),
    )
    result = await client.ask(
        AskRequest(question="When is the cleanest window?", map_time=selected)
    )
    await client.close()
    assert result.answer.endswith("13:30.")
    assert provider.arguments["_as_of"] == selected
