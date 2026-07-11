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
        await client.ask(AskRequest(question="What is demand?"))
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
