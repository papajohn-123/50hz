from collections import defaultdict
from datetime import UTC, date, datetime
from threading import Lock


class BudgetExceededError(RuntimeError):
    pass


class DailyCallBudget:
    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        self.limit = limit
        self._calls: dict[date, int] = defaultdict(int)
        self._lock = Lock()

    def claim(self, now: datetime | None = None) -> None:
        day = (now or datetime.now(UTC)).date()
        with self._lock:
            if self._calls[day] >= self.limit:
                raise BudgetExceededError("Daily OpenRouter call limit reached")
            self._calls[day] += 1

    def used(self, now: datetime | None = None) -> int:
        with self._lock:
            return self._calls[(now or datetime.now(UTC)).date()]
