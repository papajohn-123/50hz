from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


GB_TIME_ZONE = ZoneInfo("Europe/London")
SETTLEMENT_PERIOD = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class SettlementPeriod:
    settlement_date: date
    period: int
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=GB_TIME_ZONE)


def settlement_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Return the UTC bounds of a Great Britain electricity settlement day."""

    start = _local_midnight(day).astimezone(UTC)
    end = _local_midnight(day + timedelta(days=1)).astimezone(UTC)
    return start, end


def settlement_period_count(day: date) -> int:
    start, end = settlement_day_bounds_utc(day)
    return int((end - start) / SETTLEMENT_PERIOD)


def settlement_period_at(day: date, period: int) -> SettlementPeriod:
    count = settlement_period_count(day)
    if isinstance(period, bool) or not isinstance(period, int):
        raise TypeError("settlement period must be an integer")
    if not 1 <= period <= count:
        raise ValueError(f"settlement period must be between 1 and {count} for {day}")

    day_start, _ = settlement_day_bounds_utc(day)
    start_utc = day_start + (period - 1) * SETTLEMENT_PERIOD
    end_utc = start_utc + SETTLEMENT_PERIOD
    return SettlementPeriod(
        settlement_date=day,
        period=period,
        start_utc=start_utc,
        end_utc=end_utc,
        start_local=start_utc.astimezone(GB_TIME_ZONE),
        end_local=end_utc.astimezone(GB_TIME_ZONE),
    )


def iter_settlement_periods(day: date) -> tuple[SettlementPeriod, ...]:
    return tuple(
        settlement_period_at(day, period)
        for period in range(1, settlement_period_count(day) + 1)
    )


def settlement_period_for_instant(instant: datetime) -> SettlementPeriod:
    """Resolve an aware instant to its unambiguous GB settlement period."""

    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant must be timezone-aware")
    instant_utc = instant.astimezone(UTC)
    day = instant_utc.astimezone(GB_TIME_ZONE).date()
    day_start, day_end = settlement_day_bounds_utc(day)
    if not day_start <= instant_utc < day_end:
        # Defensive guard for future timezone database changes around midnight.
        raise ValueError("instant does not fall within its local settlement day")
    period = int((instant_utc - day_start) / SETTLEMENT_PERIOD) + 1
    return settlement_period_at(day, period)

