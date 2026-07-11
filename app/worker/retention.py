from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol


logger = logging.getLogger(__name__)


class RawPayloadDeleteRepository(Protocol):
    async def delete_batch(self, *, before: datetime, limit: int) -> int: ...


@dataclass(frozen=True, slots=True)
class RetentionOutcome:
    cutoff: datetime
    deleted_rows: int
    batch_attempts: int
    hit_batch_limit: bool


class RawPayloadRetentionWorker:
    """Periodically prune raw JSON while leaving normalized records intact."""

    DEFAULT_BATCH_SIZE = 25
    DEFAULT_MAX_BATCHES = 8

    def __init__(
        self,
        repository: RawPayloadDeleteRepository,
        *,
        retention: timedelta,
        interval: timedelta,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_batches: int = DEFAULT_MAX_BATCHES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if retention <= timedelta(0):
            raise ValueError("raw-payload retention must be positive")
        if interval <= timedelta(0):
            raise ValueError("raw-payload cleanup interval must be positive")
        if batch_size <= 0 or max_batches <= 0:
            raise ValueError("raw-payload cleanup bounds must be positive")
        self.repository = repository
        self.retention = retention
        self.interval = interval
        self.batch_size = batch_size
        self.max_batches = max_batches
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self, *, now: datetime | None = None) -> RetentionOutcome:
        instant = _utc(now or self.clock())
        cutoff = instant - self.retention
        deleted_rows = 0
        batch_attempts = 0
        last_batch = 0

        for _ in range(self.max_batches):
            last_batch = await self.repository.delete_batch(
                before=cutoff,
                limit=self.batch_size,
            )
            if not 0 <= last_batch <= self.batch_size:
                raise ValueError("retention repository returned an invalid delete count")
            batch_attempts += 1
            deleted_rows += last_batch
            if last_batch < self.batch_size:
                break

        return RetentionOutcome(
            cutoff=cutoff,
            deleted_rows=deleted_rows,
            batch_attempts=batch_attempts,
            hit_batch_limit=(
                batch_attempts == self.max_batches
                and last_batch == self.batch_size
            ),
        )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                outcome = await self.run_once()
            except Exception:
                # Retention is maintenance, not ingestion. A failed cleanup
                # must never take the source worker down; retry next interval.
                logger.exception(
                    "Raw-payload cleanup failed; retrying on the next interval"
                )
            else:
                if outcome.deleted_rows:
                    logger.info(
                        "Raw-payload cleanup deleted %d rows older than %s",
                        outcome.deleted_rows,
                        outcome.cutoff.isoformat(),
                    )
                if outcome.hit_batch_limit:
                    logger.warning(
                        "Raw-payload cleanup reached its per-run batch limit; "
                        "remaining rows will be retried"
                    )
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.interval.total_seconds(),
                )
            except asyncio.TimeoutError:
                pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("retention clock must include a timezone")
    return value.astimezone(UTC)
