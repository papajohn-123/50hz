"""Database-agnostic ingestion worker foundation."""

from app.worker.contracts import (
    AdvisoryLockProvider,
    IngestionCheckpoint,
    IngestionRepository,
    PersistOutcome,
)
from app.worker.defaults import build_elexon_schedules
from app.worker.scheduler import (
    IngestionWorker,
    PollSchedule,
    RunMode,
    RunOutcome,
    RunStatus,
    WindowPlanner,
)

__all__ = [
    "AdvisoryLockProvider",
    "IngestionCheckpoint",
    "IngestionRepository",
    "IngestionWorker",
    "PersistOutcome",
    "PollSchedule",
    "RunMode",
    "RunOutcome",
    "RunStatus",
    "WindowPlanner",
    "build_elexon_schedules",
]

