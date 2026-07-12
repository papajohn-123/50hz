"""Isolated post-ingestion maintenance for normalized observed events."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.events.observed import (
    ObservedEventEvaluation,
    ObservedEventEvaluator,
    ObservedEvidenceBatch,
)
from app.persistence.observed_events import ObservedEventPersistenceOutcome
from app.worker.contracts import (
    AdvisoryLockProvider,
    PostIngestionAction,
    PostIngestionContext,
)


RELEVANT_OBSERVED_EVENT_JOBS = frozenset(
    {
        "elexon.fuelinst",
        "elexon.interconnectors",
        "elexon.freq",
    }
)
OBSERVED_EVENT_LOCK_NAME = "50hz:maintenance:observed-events:v1"


class ObservedEvidenceLoader(Protocol):
    async def load(self, *, cutoff_at: datetime) -> ObservedEvidenceBatch: ...


class ObservedEventStore(Protocol):
    async def apply(
        self,
        evaluation: ObservedEventEvaluation,
    ) -> ObservedEventPersistenceOutcome: ...

    async def expire(self, *, as_of: datetime) -> ObservedEventPersistenceOutcome: ...


class ObservedEventMaintenanceAction(PostIngestionAction):
    """Evaluate once per distinct coherent evidence set, then enforce expiry."""

    def __init__(
        self,
        *,
        loader: ObservedEvidenceLoader,
        store: ObservedEventStore,
        locks: AdvisoryLockProvider,
        evaluator: ObservedEventEvaluator | None = None,
    ) -> None:
        self._loader = loader
        self._store = store
        self._locks = locks
        self._evaluator = evaluator or ObservedEventEvaluator()
        # This is an optimization, not the idempotency boundary. A restart may
        # replay one evidence set; deterministic keys and checksums make it safe.
        self._component_fingerprints: dict[str, str] = {}

    async def after_success(self, context: PostIngestionContext) -> None:
        if _base_job_id(context.job_id) not in RELEVANT_OBSERVED_EVENT_JOBS:
            return

        async with self._locks.acquire(OBSERVED_EVENT_LOCK_NAME) as acquired:
            if not acquired:
                return
            batch = await self._loader.load(cutoff_at=context.completed_at)
            changed_components = tuple(
                component
                for component in batch.components
                if self._component_fingerprints.get(component.kind.value)
                != component.evidence_fingerprint
            )
            if changed_components:
                changed_batch = ObservedEvidenceBatch(
                    cutoff_at=batch.cutoff_at,
                    components=changed_components,
                )
                evaluation = self._evaluator.evaluate(changed_batch)
                await self._store.apply(evaluation)
                self._component_fingerprints.update(
                    {
                        component.kind.value: component.evidence_fingerprint
                        for component in changed_components
                    }
                )
            # Expiry is wall-clock maintenance and remains necessary when an
            # upstream succeeds without publishing a new coherent timestamp.
            await self._store.expire(as_of=context.completed_at)


def _base_job_id(job_id: str) -> str:
    return job_id.removesuffix(".reconcile")
