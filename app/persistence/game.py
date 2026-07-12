"""Immutable persistence for deterministic daily-prediction resolutions."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PredictionResolutionRevision
from app.game.models import PredictionResolution, PredictionResolutionState
from app.persistence.locks import advisory_lock_key


SessionFactory = Callable[[], AsyncSession]
_RESOLUTION_NAMESPACE = uuid.UUID("c8c01c39-8dd0-55a0-a271-c77d738fdbba")


class PredictionResolutionLedger(Protocol):
    async def persist(
        self,
        resolution: PredictionResolution,
    ) -> PredictionResolution: ...


class PostgresPredictionResolutionLedger:
    """Append terminal results while preserving source corrections explicitly.

    A transaction-scoped PostgreSQL advisory lock serializes revision-number
    allocation for one prediction/rule pair, including the first insert where
    there is no row to lock. Unique constraints and ``ON CONFLICT`` provide an
    additional idempotence guard for retries and concurrent identical reads.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def persist(
        self,
        resolution: PredictionResolution,
    ) -> PredictionResolution:
        if resolution.state is PredictionResolutionState.PENDING:
            raise ValueError("pending prediction resolutions are not persisted")

        async with self._session_factory() as session:
            async with session.begin():
                if session.get_bind().dialect.name == "postgresql":
                    await session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {
                            "lock_key": advisory_lock_key(
                                "50hz:prediction-resolution:"
                                f"{resolution.prediction_id}:"
                                f"{resolution.rule_version}"
                            )
                        },
                    )

                existing = await _by_evidence_checksum(session, resolution)
                if existing is not None:
                    return _stored_resolution(existing)

                latest = await _latest_revision(session, resolution)
                revision_number = (
                    latest.resolution_revision + 1 if latest is not None else 1
                )
                persisted = resolution.model_copy(
                    update={
                        "resolution_revision": revision_number,
                        "is_correction": revision_number > 1,
                    }
                )
                inserted = (
                    await session.execute(_insert_statement(persisted))
                ).scalar_one_or_none()
                if inserted is None:
                    # A concurrent identical request may have won before an
                    # advisory lock was available (for example during a mixed
                    # rollout). Return its immutable row rather than duplicating.
                    existing = await _by_evidence_checksum(session, resolution)
                    if existing is not None:
                        return _stored_resolution(existing)
                    raise RuntimeError(
                        "prediction resolution revision allocation conflicted"
                    )
                return persisted


async def _by_evidence_checksum(
    session: AsyncSession,
    resolution: PredictionResolution,
) -> PredictionResolutionRevision | None:
    result = await session.execute(
        select(PredictionResolutionRevision).where(
            PredictionResolutionRevision.prediction_id == resolution.prediction_id,
            PredictionResolutionRevision.rule_version == resolution.rule_version,
            PredictionResolutionRevision.evidence_checksum
            == resolution.evidence_checksum,
        )
    )
    return result.scalar_one_or_none()


async def _latest_revision(
    session: AsyncSession,
    resolution: PredictionResolution,
) -> PredictionResolutionRevision | None:
    result = await session.execute(
        select(PredictionResolutionRevision)
        .where(
            PredictionResolutionRevision.prediction_id == resolution.prediction_id,
            PredictionResolutionRevision.rule_version == resolution.rule_version,
        )
        .order_by(PredictionResolutionRevision.resolution_revision.desc())
        .limit(1)
        .with_for_update()
    )
    return result.scalar_one_or_none()


def _insert_statement(resolution: PredictionResolution):
    revision_id = uuid.uuid5(
        _RESOLUTION_NAMESPACE,
        f"{resolution.prediction_id}:{resolution.rule_version}:"
        f"{resolution.resolution_revision}",
    )
    return (
        pg_insert(PredictionResolutionRevision)
        .values(
            id=revision_id,
            prediction_id=resolution.prediction_id,
            prediction_date=resolution.date,
            rule_version=resolution.rule_version,
            resolution_revision=resolution.resolution_revision,
            state=resolution.state.value,
            outcome=resolution.outcome.value if resolution.outcome is not None else None,
            evidence_checksum=resolution.evidence_checksum,
            revision_watermark_at=resolution.revision_watermark_at,
            computed_at=resolution.computed_at,
            payload=resolution.model_dump(mode="json", by_alias=True),
        )
        .on_conflict_do_nothing()
        .returning(PredictionResolutionRevision.id)
    )


def _stored_resolution(row: PredictionResolutionRevision) -> PredictionResolution:
    return PredictionResolution.model_validate(row.payload)
