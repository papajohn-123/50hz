from datetime import UTC, date, datetime, timedelta

import pytest

from app.domain.settlement import (
    iter_settlement_periods,
    settlement_day_bounds_utc,
    settlement_period_at,
    settlement_period_count,
    settlement_period_for_instant,
)


@pytest.mark.parametrize(
    ("day", "expected_periods", "expected_duration_hours"),
    [
        (date(2026, 7, 11), 48, 24),
        (date(2026, 3, 29), 46, 23),
        (date(2026, 10, 25), 50, 25),
    ],
)
def test_gb_settlement_day_lengths(
    day: date, expected_periods: int, expected_duration_hours: int
) -> None:
    start, end = settlement_day_bounds_utc(day)

    assert settlement_period_count(day) == expected_periods
    assert end - start == timedelta(hours=expected_duration_hours)


def test_spring_clock_change_skips_missing_local_hour() -> None:
    periods = iter_settlement_periods(date(2026, 3, 29))

    assert periods[1].start_local.hour == 0
    assert periods[1].start_local.minute == 30
    assert periods[2].start_local.hour == 2
    assert periods[2].start_local.minute == 0
    assert all(period.start_local.hour != 1 for period in periods)


def test_autumn_clock_change_distinguishes_repeated_local_hour() -> None:
    periods = iter_settlement_periods(date(2026, 10, 25))
    one_am_periods = [period for period in periods if period.start_local.hour == 1]

    assert len(one_am_periods) == 4
    assert [period.start_local.fold for period in one_am_periods] == [0, 0, 1, 1]
    assert [period.start_local.utcoffset() for period in one_am_periods] == [
        timedelta(hours=1),
        timedelta(hours=1),
        timedelta(0),
        timedelta(0),
    ]


@pytest.mark.parametrize(
    "day", [date(2026, 7, 11), date(2026, 3, 29), date(2026, 10, 25)]
)
def test_every_period_round_trips_from_an_instant(day: date) -> None:
    periods = iter_settlement_periods(day)

    for expected in periods:
        midpoint = expected.start_utc + timedelta(minutes=15)
        resolved = settlement_period_for_instant(midpoint)
        assert (resolved.settlement_date, resolved.period) == (
            expected.settlement_date,
            expected.period,
        )

    assert all(
        current.end_utc == following.start_utc
        for current, following in zip(periods, periods[1:], strict=False)
    )


def test_midnight_belongs_to_next_settlement_day() -> None:
    resolved = settlement_period_for_instant(datetime(2026, 7, 11, 23, 0, tzinfo=UTC))

    assert resolved.settlement_date == date(2026, 7, 12)
    assert resolved.period == 1


def test_invalid_period_and_naive_instant_are_rejected() -> None:
    with pytest.raises(ValueError, match="between 1 and 46"):
        settlement_period_at(date(2026, 3, 29), 47)
    with pytest.raises(TypeError, match="integer"):
        settlement_period_at(date(2026, 7, 11), True)
    with pytest.raises(ValueError, match="timezone-aware"):
        settlement_period_for_instant(datetime(2026, 7, 11, 12, 0))

