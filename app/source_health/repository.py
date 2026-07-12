from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestionRun, SourceMetadata
from app.domain.enums import IngestionRunStatus
from app.persistence.records import PUBLIC_SOURCE_IDS, PUBLIC_SOURCE_PROVIDERS


SessionFactory = Callable[[], AsyncSession]


@dataclass(frozen=True, slots=True)
class SourceRunSummary:
    source_id: str
    last_attempted_at: datetime | None
    last_attempt_state: str | None
    last_succeeded_at: datetime | None


class SourceHealthRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self._session_factory = session_factory

    async def load(
        self,
    ) -> tuple[tuple[SourceMetadata, ...], dict[str, SourceRunSummary]]:
        async with self._session_factory() as session:
            sources = tuple(
                (
                    await session.execute(
                        _public_sources_statement()
                    )
                )
                .scalars()
                .all()
            )
            if not sources:
                return (), {}
            source_ids = tuple(source.id for source in sources)
            latest = tuple(
                (await session.execute(_latest_run_statement(source_ids)))
                .scalars()
                .all()
            )
            successful = tuple(
                (await session.execute(_latest_success_statement(source_ids)))
                .scalars()
                .all()
            )

        latest_by_source = {run.source_id: run for run in latest}
        success_by_source = {run.source_id: run for run in successful}
        return sources, {
            source_id: SourceRunSummary(
                source_id=source_id,
                last_attempted_at=(
                    latest_by_source[source_id].started_at
                    if source_id in latest_by_source
                    else None
                ),
                last_attempt_state=(
                    latest_by_source[source_id].status.value
                    if source_id in latest_by_source
                    else None
                ),
                last_succeeded_at=(
                    success_by_source[source_id].completed_at
                    if source_id in success_by_source
                    else None
                ),
            )
            for source_id in source_ids
        }


def _public_sources_statement() -> Select[tuple[SourceMetadata]]:
    return (
        select(SourceMetadata)
        .where(
            SourceMetadata.active.is_(True),
            SourceMetadata.provider.in_(PUBLIC_SOURCE_PROVIDERS),
            SourceMetadata.id.in_(PUBLIC_SOURCE_IDS),
        )
        .order_by(SourceMetadata.provider, SourceMetadata.dataset)
    )


def _latest_run_statement(source_ids: tuple[str, ...]) -> Select[tuple[IngestionRun]]:
    return (
        select(IngestionRun)
        .where(IngestionRun.source_id.in_(source_ids))
        .distinct(IngestionRun.source_id)
        .order_by(
            IngestionRun.source_id,
            IngestionRun.started_at.desc(),
            IngestionRun.id.desc(),
        )
    )


def _latest_success_statement(
    source_ids: tuple[str, ...],
) -> Select[tuple[IngestionRun]]:
    return (
        select(IngestionRun)
        .where(
            IngestionRun.source_id.in_(source_ids),
            IngestionRun.status == IngestionRunStatus.SUCCEEDED,
            IngestionRun.completed_at.is_not(None),
        )
        .distinct(IngestionRun.source_id)
        .order_by(
            IngestionRun.source_id,
            IngestionRun.completed_at.desc(),
            IngestionRun.started_at.desc(),
            IngestionRun.id.desc(),
        )
    )
