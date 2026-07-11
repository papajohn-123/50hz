"""Persistence and advisory-lock ports required by the ingestion worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, AsyncContextManager, Protocol

from app.sources.types import AdapterResult, ObservationWindow


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class IngestionCheckpoint:
    """Durable scheduling state for one incremental or reconciliation job."""

    job_id: str
    last_attempted_at: datetime
    last_succeeded_at: datetime | None
    window_end: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "last_attempted_at",
            _utc(self.last_attempted_at, "last_attempted_at"),
        )
        if self.last_succeeded_at is not None:
            object.__setattr__(
                self,
                "last_succeeded_at",
                _utc(self.last_succeeded_at, "last_succeeded_at"),
            )
        if self.window_end is not None:
            object.__setattr__(self, "window_end", _utc(self.window_end, "window_end"))


@dataclass(frozen=True, slots=True)
class PersistOutcome:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


class IngestionRepository(Protocol):
    """Database operations expected to be transactional in the implementation."""

    async def get_checkpoint(self, job_id: str) -> IngestionCheckpoint | None:
        ...

    async def persist_success(
        self,
        *,
        job_id: str,
        result: AdapterResult[Any],
        attempted_at: datetime,
        completed_at: datetime,
    ) -> PersistOutcome:
        """Store raw payload, normalized rows, and the checkpoint atomically."""

    async def record_failure(
        self,
        *,
        job_id: str,
        window: ObservationWindow,
        attempted_at: datetime,
        failed_at: datetime,
        error_type: str,
        error_message: str,
    ) -> None:
        ...


class AdvisoryLockProvider(Protocol):
    def acquire(self, lock_name: str) -> AsyncContextManager[bool]:
        """Return a lease yielding False when another worker holds the lock."""

