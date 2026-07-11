from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EventExplanation as EventExplanationRow
from app.db.models import ReportedNoticeExplanation as ReportedNoticeExplanationRow
from app.intelligence.client import OpenRouterExplanationClient
from app.intelligence.models import (
    EvidencePacket,
    ExplanationResult,
    GroundedExplanation,
    SourceCitation,
)
from app.intelligence.provider import (
    DatabaseGridToolProvider,
    GridEventNotFoundError,
    public_event_id,
)
from app.persistence.reads import ReportedNoticeRead


SessionFactory = Callable[[], AsyncSession]
PROMPT_VERSION = "event-explanation-v1"


@dataclass(frozen=True, slots=True)
class ExplainedEvent:
    event_id: str
    revision: int
    explanation: GroundedExplanation
    citations: tuple[SourceCitation, ...]
    model: str
    used_fallback: bool


class EventExplanationService:
    """Build evidence packets and cache only successfully validated model output."""

    def __init__(
        self,
        *,
        provider: DatabaseGridToolProvider,
        client: OpenRouterExplanationClient,
        session_factory: SessionFactory,
        configured_model: str,
        locale: str = "en-GB",
    ) -> None:
        self.provider = provider
        self.client = client
        self.session_factory = session_factory
        self.configured_model = configured_model
        self.locale = locale

    async def explain(self, requested_event_id: str) -> ExplainedEvent:
        try:
            event = await self.provider.get_event_row(requested_event_id)
        except GridEventNotFoundError:
            notice = await self.provider.get_reported_notice(requested_event_id)
            return await self._explain_reported_notice(requested_event_id, notice)
        event_id = public_event_id(event.id)
        envelope = await self.provider.call(
            "get_event_evidence", {"event_id": event_id}
        )
        cached = await self._read_cache(event.id, event.evidence_checksum)
        if cached is not None:
            return ExplainedEvent(
                event_id=event_id,
                revision=event.evidence_version,
                explanation=cached.explanation,
                citations=tuple(
                    envelope.source_refs[ref]
                    for ref in cached.explanation.evidence_refs
                    if ref in envelope.source_refs
                ),
                model=cached.model,
                used_fallback=False,
            )

        evidence = dict(event.evidence or {})
        packet = EvidencePacket(
            event_id=event_id,
            revision=event.evidence_version,
            event_type=event.event_type,
            status=event.status.value,
            as_of=envelope.as_of,
            freshness=envelope.freshness,
            facts=envelope.facts,
            permitted_comparisons=_string_list(evidence.get("permitted_comparisons")),
            unknowns=_string_list(evidence.get("unknowns")),
            source_refs=envelope.source_refs,
            cause_reported=bool(evidence.get("cause_reported", False)),
        )
        result = await self.client.explain(packet)
        if not result.used_fallback:
            await self._write_cache(
                event_id=event.id,
                evidence_checksum=event.evidence_checksum,
                result=result,
            )
        return ExplainedEvent(
            event_id=event_id,
            revision=event.evidence_version,
            explanation=result.explanation,
            citations=tuple(
                envelope.source_refs[ref]
                for ref in result.explanation.evidence_refs
                if ref in envelope.source_refs
            ),
            model=result.model,
            used_fallback=result.used_fallback,
        )

    async def _explain_reported_notice(
        self,
        event_id: str,
        notice: ReportedNoticeRead,
    ) -> ExplainedEvent:
        envelope = await self.provider.reported_notice_evidence(notice)
        cached = await self._read_reported_notice_cache(
            public_event_id=event_id,
            notice_revision_key=notice.revision_key,
        )
        if cached is not None:
            return ExplainedEvent(
                event_id=event_id,
                revision=notice.revision_number or 1,
                explanation=cached.explanation,
                citations=tuple(
                    envelope.source_refs[ref]
                    for ref in cached.explanation.evidence_refs
                    if ref in envelope.source_refs
                ),
                model=cached.model,
                used_fallback=False,
            )

        unknowns = (
            ["Cause has not been reported"]
            if notice.notice_kind != "system_warning" and not notice.reported_cause
            else []
        )
        packet = EvidencePacket(
            event_id=event_id,
            revision=notice.revision_number or 1,
            event_type=(
                "reported_system_warning"
                if notice.notice_kind == "system_warning"
                else "reported_unit_unavailability"
            ),
            status=notice.event_status or "active",
            as_of=envelope.as_of,
            freshness=envelope.freshness,
            facts=envelope.facts,
            unknowns=unknowns,
            source_refs=envelope.source_refs,
            cause_reported=bool(notice.reported_cause),
        )
        result = await self.client.explain(packet)
        if not result.used_fallback:
            await self._write_reported_notice_cache(
                public_event_id=event_id,
                notice=notice,
                result=result,
            )
        return ExplainedEvent(
            event_id=event_id,
            revision=packet.revision,
            explanation=result.explanation,
            citations=tuple(
                envelope.source_refs[ref]
                for ref in result.explanation.evidence_refs
                if ref in envelope.source_refs
            ),
            model=result.model,
            used_fallback=result.used_fallback,
        )

    async def _read_reported_notice_cache(
        self,
        *,
        public_event_id: str,
        notice_revision_key: str,
    ) -> ExplanationResult | None:
        statement = select(ReportedNoticeExplanationRow).where(
            ReportedNoticeExplanationRow.public_event_id == public_event_id,
            ReportedNoticeExplanationRow.notice_revision_key == notice_revision_key,
            ReportedNoticeExplanationRow.provider == "openrouter",
            ReportedNoticeExplanationRow.model == self.configured_model,
            ReportedNoticeExplanationRow.prompt_version == PROMPT_VERSION,
            ReportedNoticeExplanationRow.locale == self.locale,
            ReportedNoticeExplanationRow.status == "succeeded",
        )
        async with self.session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
        if row is None or row.structured_response is None:
            return None
        try:
            explanation = GroundedExplanation.model_validate(row.structured_response)
        except ValueError:
            return None
        return ExplanationResult(
            explanation=explanation,
            model=row.model,
            used_fallback=False,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
        )

    async def _write_reported_notice_cache(
        self,
        *,
        public_event_id: str,
        notice: ReportedNoticeRead,
        result: ExplanationResult,
    ) -> None:
        row = ReportedNoticeExplanationRow(
            public_event_id=public_event_id,
            notice_revision_key=notice.revision_key,
            notice_revision_number=notice.revision_number,
            provider="openrouter",
            model=self.configured_model,
            prompt_version=PROMPT_VERSION,
            locale=self.locale,
            status="succeeded",
            explanation=result.explanation.plain_language,
            structured_response=result.explanation.model_dump(mode="json"),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        async with self.session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Another request may finish the same public notice revision
                # first. Its validated row is the canonical cache entry.
                await session.rollback()

    async def _read_cache(
        self, event_id, evidence_checksum: str
    ) -> ExplanationResult | None:
        statement = select(EventExplanationRow).where(
            EventExplanationRow.event_id == event_id,
            EventExplanationRow.evidence_checksum == evidence_checksum,
            EventExplanationRow.provider == "openrouter",
            EventExplanationRow.model == self.configured_model,
            EventExplanationRow.prompt_version == PROMPT_VERSION,
            EventExplanationRow.locale == self.locale,
            EventExplanationRow.status == "succeeded",
        )
        async with self.session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
        if row is None or row.structured_response is None:
            return None
        try:
            explanation = GroundedExplanation.model_validate(row.structured_response)
        except ValueError:
            return None
        return ExplanationResult(
            explanation=explanation,
            model=row.model,
            used_fallback=False,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
        )

    async def _write_cache(
        self,
        *,
        event_id,
        evidence_checksum: str,
        result: ExplanationResult,
    ) -> None:
        row = EventExplanationRow(
            event_id=event_id,
            evidence_checksum=evidence_checksum,
            provider="openrouter",
            # Keep the configured identifier as part of the stable cache key;
            # OpenRouter may return a provider-qualified variant in the result.
            model=self.configured_model,
            prompt_version=PROMPT_VERSION,
            locale=self.locale,
            status="succeeded",
            explanation=result.explanation.plain_language,
            structured_response=result.explanation.model_dump(mode="json"),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        async with self.session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Concurrent requests can race on the unique cache key. The
                # winner's validated explanation is sufficient.
                await session.rollback()


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)][:12]
