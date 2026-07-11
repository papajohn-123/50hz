import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.events.models import EvidenceFact
from app.intelligence.ask import (
    AskRequest,
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
async def test_ask_runs_bounded_tool_then_returns_cited_answer() -> None:
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
            "evidence_refs": ["elexon"],
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
