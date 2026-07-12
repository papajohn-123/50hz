from datetime import UTC, datetime

import pytest

from app.source_health.api import _source_status_cache_time


def test_source_status_evaluation_time_is_stable_within_thirty_second_bucket() -> None:
    first = _source_status_cache_time(
        datetime(2026, 7, 11, 12, 0, 1, 999_999, tzinfo=UTC)
    )
    last = _source_status_cache_time(
        datetime(2026, 7, 11, 12, 0, 29, 999_999, tzinfo=UTC)
    )

    assert first == last == datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    assert _source_status_cache_time(
        datetime(2026, 7, 11, 12, 0, 30, tzinfo=UTC)
    ) == datetime(2026, 7, 11, 12, 0, 30, tzinfo=UTC)


def test_source_status_cache_time_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _source_status_cache_time(datetime(2026, 7, 11, 12, 0))
