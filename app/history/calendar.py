from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.history.models import INTERVAL_MINUTES, SettlementInterval


LONDON = ZoneInfo("Europe/London")
INTERVAL = timedelta(minutes=INTERVAL_MINUTES)


def expected_settlement_intervals(day: date) -> list[SettlementInterval]:
    """Return the GB settlement periods for one Europe/London civil date."""

    if isinstance(day, datetime) or not isinstance(day, date):
        raise TypeError("day must be a date, not a datetime")
    next_day = day + timedelta(days=1)
    start = datetime.combine(day, time.min, tzinfo=LONDON).astimezone(UTC)
    end = datetime.combine(next_day, time.min, tzinfo=LONDON).astimezone(UTC)
    intervals: list[SettlementInterval] = []
    cursor = start
    period = 1
    while cursor < end:
        intervals.append(
            SettlementInterval(
                settlement_date=day,
                settlement_period=period,
                start=cursor,
                end=cursor + INTERVAL,
            )
        )
        cursor += INTERVAL
        period += 1
    return intervals
