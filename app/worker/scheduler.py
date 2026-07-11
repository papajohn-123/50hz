"""Polling scheduler with overlap windows and periodic reconciliation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Callable, Iterable

from app.sources.types import ObservationWindow, SourceAdapter
from app.worker.contracts import (
    AdvisoryLockProvider,
    IngestionCheckpoint,
    IngestionRepository,
    PersistOutcome,
)


class RunMode(StrEnum):
    INCREMENTAL = "incremental"
    RECONCILIATION = "reconciliation"


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_NOT_DUE = "skipped_not_due"
    SKIPPED_LOCKED = "skipped_locked"


@dataclass(frozen=True, slots=True)
class PollSchedule:
    job_id: str
    adapter: SourceAdapter[Any]
    cadence: timedelta
    overlap: timedelta
    initial_lookback: timedelta
    max_incremental_lookback: timedelta = timedelta(hours=48)
    reconcile_every: timedelta | None = timedelta(hours=1)
    reconcile_lookback: timedelta = timedelta(hours=48)

    def __post_init__(self) -> None:
        durations = {
            "cadence": self.cadence,
            "overlap": self.overlap,
            "initial_lookback": self.initial_lookback,
            "max_incremental_lookback": self.max_incremental_lookback,
            "reconcile_lookback": self.reconcile_lookback,
        }
        for name, duration in durations.items():
            if duration <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.reconcile_every is not None and self.reconcile_every <= timedelta(0):
            raise ValueError("reconcile_every must be positive when enabled")
        if self.overlap >= self.max_incremental_lookback:
            raise ValueError("overlap must be shorter than max_incremental_lookback")

    @property
    def lock_name(self) -> str:
        return f"50hz:ingest:{self.adapter.source_id}"

    @property
    def reconciliation_job_id(self) -> str:
        return f"{self.job_id}.reconcile"


@dataclass(frozen=True, slots=True)
class RunOutcome:
    job_id: str
    mode: RunMode
    status: RunStatus
    window: ObservationWindow | None = None
    record_count: int = 0
    persistence: PersistOutcome | None = None
    error_type: str | None = None
    error_message: str | None = None


class WindowPlanner:
    @staticmethod
    def is_due(
        checkpoint: IngestionCheckpoint | None,
        *,
        now: datetime,
        interval: timedelta,
    ) -> bool:
        now = _utc(now, "now")
        if checkpoint is None:
            return True
        return checkpoint.last_attempted_at + interval <= now

    @staticmethod
    def incremental(
        schedule: PollSchedule,
        checkpoint: IngestionCheckpoint | None,
        *,
        now: datetime,
    ) -> ObservationWindow:
        now = _utc(now, "now")
        floor = now - schedule.max_incremental_lookback
        if checkpoint is None or checkpoint.window_end is None:
            start = now - schedule.initial_lookback
        else:
            start = checkpoint.window_end - schedule.overlap
        start = max(start, floor)
        if start >= now:
            # Protect against a future checkpoint caused by clock skew while still
            # retaining an overlap large enough to recover the latest observations.
            start = now - min(schedule.initial_lookback, schedule.overlap)
        return ObservationWindow(start=start, end=now)

    @staticmethod
    def reconciliation(schedule: PollSchedule, *, now: datetime) -> ObservationWindow:
        now = _utc(now, "now")
        return ObservationWindow(start=now - schedule.reconcile_lookback, end=now)


class IngestionWorker:
    """Runs source jobs without embedding a database or process framework."""

    def __init__(
        self,
        *,
        schedules: Iterable[PollSchedule],
        repository: IngestionRepository,
        locks: AdvisoryLockProvider,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.schedules = tuple(schedules)
        if not self.schedules:
            raise ValueError("at least one poll schedule is required")
        if len({schedule.job_id for schedule in self.schedules}) != len(self.schedules):
            raise ValueError("poll schedule job IDs must be unique")
        self.repository = repository
        self.locks = locks
        self.clock = clock or (lambda: datetime.now(UTC))

    async def run_once(self, *, now: datetime | None = None) -> tuple[RunOutcome, ...]:
        tick_time = _utc(now or self.clock(), "now")
        outcomes: list[RunOutcome] = []

        for schedule in self.schedules:
            incremental_checkpoint = await self.repository.get_checkpoint(schedule.job_id)
            if WindowPlanner.is_due(
                incremental_checkpoint,
                now=tick_time,
                interval=schedule.cadence,
            ):
                window = WindowPlanner.incremental(
                    schedule,
                    incremental_checkpoint,
                    now=tick_time,
                )
                outcomes.append(
                    await self._execute(
                        schedule,
                        job_id=schedule.job_id,
                        mode=RunMode.INCREMENTAL,
                        window=window,
                        attempted_at=tick_time,
                    )
                )
            else:
                outcomes.append(
                    RunOutcome(
                        job_id=schedule.job_id,
                        mode=RunMode.INCREMENTAL,
                        status=RunStatus.SKIPPED_NOT_DUE,
                    )
                )

            if schedule.reconcile_every is None:
                continue
            reconciliation_checkpoint = await self.repository.get_checkpoint(
                schedule.reconciliation_job_id
            )
            if WindowPlanner.is_due(
                reconciliation_checkpoint,
                now=tick_time,
                interval=schedule.reconcile_every,
            ):
                outcomes.append(
                    await self._execute(
                        schedule,
                        job_id=schedule.reconciliation_job_id,
                        mode=RunMode.RECONCILIATION,
                        window=WindowPlanner.reconciliation(schedule, now=tick_time),
                        attempted_at=tick_time,
                    )
                )
            else:
                outcomes.append(
                    RunOutcome(
                        job_id=schedule.reconciliation_job_id,
                        mode=RunMode.RECONCILIATION,
                        status=RunStatus.SKIPPED_NOT_DUE,
                    )
                )

        return tuple(outcomes)

    async def _execute(
        self,
        schedule: PollSchedule,
        *,
        job_id: str,
        mode: RunMode,
        window: ObservationWindow,
        attempted_at: datetime,
    ) -> RunOutcome:
        async with self.locks.acquire(schedule.lock_name) as acquired:
            if not acquired:
                return RunOutcome(
                    job_id=job_id,
                    mode=mode,
                    status=RunStatus.SKIPPED_LOCKED,
                    window=window,
                )

            try:
                result = await schedule.adapter.fetch(window)
                completed_at = _utc(self.clock(), "clock")
                persistence = await self.repository.persist_success(
                    job_id=job_id,
                    result=result,
                    attempted_at=attempted_at,
                    completed_at=completed_at,
                )
            except Exception as exc:
                failed_at = _utc(self.clock(), "clock")
                await self.repository.record_failure(
                    job_id=job_id,
                    window=window,
                    attempted_at=attempted_at,
                    failed_at=failed_at,
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:2000],
                )
                return RunOutcome(
                    job_id=job_id,
                    mode=mode,
                    status=RunStatus.FAILED,
                    window=window,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

        return RunOutcome(
            job_id=job_id,
            mode=mode,
            status=RunStatus.SUCCEEDED,
            window=window,
            record_count=len(result.records),
            persistence=persistence,
        )

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        tick_interval: timedelta = timedelta(seconds=5),
    ) -> None:
        if tick_interval <= timedelta(0):
            raise ValueError("tick_interval must be positive")
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=tick_interval.total_seconds(),
                )
            except asyncio.TimeoutError:
                pass


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)

