from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AwareDatetime, Field, HttpUrl

from app.api.dependencies import get_grid_read_repository
from app.api.models import MobileModel
from app.config import get_settings
from app.db import get_session_factory
from app.intelligence.ask import (
    AskRequest,
    AskUnavailableError,
    OpenRouterAskClient,
)
from app.intelligence.budget import DailyCallBudget
from app.intelligence.client import OpenRouterExplanationClient
from app.intelligence.models import SourceCitation
from app.intelligence.provider import (
    DatabaseGridToolProvider,
    GridEventNotFoundError,
)
from app.intelligence.service import EventExplanationService, ExplainedEvent
from app.persistence import GridReadRepository


router = APIRouter(prefix="/v1", tags=["intelligence"])


class AskAPIRequest(MobileModel):
    question: str = Field(min_length=2, max_length=500)
    map_time: AwareDatetime | None = None
    region_code: str | None = Field(default=None, max_length=32)


class CitationResponse(MobileModel):
    source_id: str = Field(alias="sourceID")
    publisher: str
    title: str
    canonical_url: HttpUrl = Field(alias="canonicalURL")
    published_at: AwareDatetime | None = None


class AskAPIResponse(MobileModel):
    answer: str
    as_of: AwareDatetime
    freshness: str
    evidence_refs: list[str]
    citations: list[CitationResponse]
    limitations: list[str]
    suggested_questions: list[str]


class ExplanationResponse(MobileModel):
    headline: str
    plain_language: str
    why_it_matters: str | None
    caveat: str | None
    evidence_refs: list[str]
    suggested_questions: list[str]


class EventExplanationResponse(MobileModel):
    event_id: str = Field(alias="eventID")
    revision: int
    explanation: ExplanationResponse
    citations: list[CitationResponse]
    model: str
    used_fallback: bool


class IntelligenceRuntime:
    def __init__(
        self,
        *,
        ask_client: OpenRouterAskClient,
        explanation_client: OpenRouterExplanationClient,
        explanation_service: EventExplanationService,
    ) -> None:
        self.ask_client = ask_client
        self.explanation_client = explanation_client
        self.explanation_service = explanation_service

    async def close(self) -> None:
        await self.ask_client.close()
        await self.explanation_client.close()


@lru_cache(maxsize=1)
def get_daily_openrouter_budget() -> DailyCallBudget:
    return DailyCallBudget(get_settings().openrouter_daily_call_limit)


async def get_intelligence_runtime(
    repository: Annotated[GridReadRepository, Depends(get_grid_read_repository)],
) -> AsyncIterator[IntelligenceRuntime]:
    settings = get_settings()
    session_factory = get_session_factory()
    provider = DatabaseGridToolProvider(repository, session_factory)
    budget = get_daily_openrouter_budget()
    ask_client = OpenRouterAskClient(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        public_base_url=settings.public_base_url,
        timeout_seconds=settings.openrouter_timeout_seconds,
        budget=budget,
        provider=provider,
    )
    explanation_client = OpenRouterExplanationClient(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        public_base_url=settings.public_base_url,
        timeout_seconds=settings.openrouter_timeout_seconds,
        budget=budget,
    )
    service = EventExplanationService(
        provider=provider,
        client=explanation_client,
        session_factory=session_factory,
        configured_model=settings.openrouter_model,
    )
    runtime = IntelligenceRuntime(
        ask_client=ask_client,
        explanation_client=explanation_client,
        explanation_service=service,
    )
    try:
        yield runtime
    finally:
        await runtime.close()


Runtime = Annotated[IntelligenceRuntime, Depends(get_intelligence_runtime)]


@router.post("/ask", response_model=AskAPIResponse)
async def ask_grid(payload: AskAPIRequest, runtime: Runtime) -> AskAPIResponse:
    try:
        request = AskRequest.model_validate(payload.model_dump(by_alias=False))
        answer = await runtime.ask_client.ask(request)
    except AskUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
            headers={"Retry-After": "60"},
        ) from error
    return AskAPIResponse(
        answer=answer.answer,
        as_of=answer.as_of,
        freshness=answer.freshness,
        evidence_refs=answer.evidence_refs,
        citations=[_citation_response(value) for value in answer.citations],
        limitations=answer.limitations,
        suggested_questions=answer.suggested_questions,
    )


@router.get(
    "/events/{event_id}/explanation",
    response_model=EventExplanationResponse,
)
async def explain_event(event_id: str, runtime: Runtime) -> EventExplanationResponse:
    try:
        result = await runtime.explanation_service.explain(event_id)
    except GridEventNotFoundError as error:
        raise HTTPException(status_code=404, detail="Grid event not found") from error
    return _explained_event_response(result)


def _citation_response(value: SourceCitation) -> CitationResponse:
    return CitationResponse(
        source_id=value.source_id,
        publisher=value.publisher,
        title=value.title,
        canonical_url=value.canonical_url,
        published_at=value.published_at,
    )


def _explained_event_response(value: ExplainedEvent) -> EventExplanationResponse:
    explanation = value.explanation
    return EventExplanationResponse(
        event_id=value.event_id,
        revision=value.revision,
        explanation=ExplanationResponse(
            headline=explanation.headline,
            plain_language=explanation.plain_language,
            why_it_matters=explanation.why_it_matters,
            caveat=explanation.caveat,
            evidence_refs=explanation.evidence_refs,
            suggested_questions=explanation.suggested_questions,
        ),
        citations=[_citation_response(citation) for citation in value.citations],
        model=value.model,
        used_fallback=value.used_fallback,
    )
