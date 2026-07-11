from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RawPayload


SessionFactory = Callable[[], AsyncSession]


class RawPayloadRetentionRepository:
    """Delete expired raw documents without deleting normalized evidence.

    Observation, forecast, and reported-notice foreign keys use ON DELETE SET
    NULL. Their normalized facts and provenance columns therefore survive this
    bounded raw-document cleanup.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def delete_batch(self, *, before: datetime, limit: int) -> int:
        cutoff = _utc(before)
        if limit <= 0:
            raise ValueError("retention batch limit must be positive")

        async with self._session_factory() as session:
            try:
                identifiers = tuple(
                    (
                        await session.execute(
                            select(RawPayload.id)
                            .where(RawPayload.retrieved_at < cutoff)
                            .order_by(RawPayload.retrieved_at, RawPayload.id)
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not identifiers:
                    return 0

                deleted = tuple(
                    (
                        await session.execute(
                            delete(RawPayload)
                            .where(RawPayload.id.in_(identifiers))
                            .returning(RawPayload.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                await session.commit()
                return len(deleted)
            except BaseException:
                await session.rollback()
                raise


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("retention cutoff must include a timezone")
    return value.astimezone(UTC)
