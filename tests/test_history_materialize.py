from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.history import (
    MaterializationReason,
    MaterializationStatus,
    MetricSeriesIdentity,
    RawMetricObservation,
    RawMetricSeries,
    expected_settlement_intervals,
    materialize_half_hours,
)


LONDON = ZoneInfo("Europe/London")
START = datetime(2026, 7, 12, 12, tzinfo=UTC)
IDENTITY = MetricSeriesIdentity(
    metric_id="generation.transmission_visible_by_fuel.wind",
    geography="GB",
    unit="MW",
    fact_class="observed",
    source_id="elexon.fuelinst",
    methodology_version="fuelinst-generation-v1",
)


def raw(
    timestamp: datetime,
    value: float,
    *,
    revision: int = 1,
    record_id: str | None = None,
) -> RawMetricObservation:
    return RawMetricObservation(
        timestamp=timestamp,
        value=value,
        revision=revision,
        source_record_id=(
            record_id
            or f"raw:{timestamp.isoformat()}:{revision}:{value}"
        ),
    )


def series(
    observations: list[RawMetricObservation],
    *,
    cadence_minutes: int = 5,
) -> RawMetricSeries:
    return RawMetricSeries(
        identity=IDENTITY,
        source_cadence_minutes=cadence_minutes,
        observations=tuple(observations),
    )


def complete_interval(
    start: datetime = START,
    *,
    cadence_minutes: int = 5,
    base_value: float = 0,
) -> list[RawMetricObservation]:
    return [
        raw(
            start + timedelta(minutes=offset),
            base_value + index,
            record_id=f"raw:{start.isoformat()}:{offset}",
        )
        for index, offset in enumerate(range(0, 30, cadence_minutes))
    ]


@pytest.mark.parametrize(
    ("day", "expected_intervals"),
    [
        (date(2026, 3, 29), 46),
        (date(2026, 7, 12), 48),
        (date(2026, 10, 25), 50),
    ],
)
def test_materializes_complete_london_settlement_days_across_dst(
    day: date,
    expected_intervals: int,
) -> None:
    settlement_intervals = expected_settlement_intervals(day)
    observations = [
        raw(
            interval.start + timedelta(minutes=offset),
            interval.settlement_period * 10 + sample_index,
            record_id=(
                f"{day}:sp-{interval.settlement_period}:sample-{sample_index}"
            ),
        )
        for interval in settlement_intervals
        for sample_index, offset in enumerate(range(0, 30, 5))
    ]

    result = materialize_half_hours(
        series(observations),
        start=settlement_intervals[0].start,
        end=settlement_intervals[-1].end,
    )

    assert result.interval_count == expected_intervals
    assert len(result.intervals) == expected_intervals
    assert len(result.series.observations) == expected_intervals
    assert result.raw_sample_count == expected_intervals * 6
    assert result.selected_sample_count == expected_intervals * 6
    assert result.outside_bounds_raw_count == 0
    assert all(
        interval.expected_sample_count == 6
        and interval.selected_sample_count == 6
        and interval.raw_sample_count == 6
        and interval.coverage_fraction == 1
        and interval.status == MaterializationStatus.AVAILABLE
        for interval in result.intervals
    )
    assert result.series.observations[0].value == 12.5
    assert all(
        observation.start.tzinfo == UTC
        and observation.end - observation.start == timedelta(minutes=30)
        for observation in result.series.observations
    )
    assert all(
        interval.start.astimezone(LONDON).date() == day
        for interval in result.intervals
    )


def test_half_hour_source_cadence_materializes_one_exact_sample() -> None:
    result = materialize_half_hours(
        series([raw(START, 321, record_id="half-hour")], cadence_minutes=30),
        start=START,
        end=START + timedelta(minutes=30),
    )

    interval = result.intervals[0]
    assert interval.expected_sample_count == 1
    assert interval.selected_sample_count == 1
    assert interval.value == 321
    assert result.series.observations[0].value == 321


def test_identity_and_source_methodology_are_preserved_exactly() -> None:
    raw_series = series(complete_interval())

    result = materialize_half_hours(
        raw_series,
        start=START,
        end=START + timedelta(minutes=30),
    )

    assert result.identity == IDENTITY
    assert result.series.identity == IDENTITY
    assert result.identity.methodology_version == "fuelinst-generation-v1"
    assert result.methodology.version == "50hz.history.half-hour-mean.v1"
    assert result.methodology.aggregate == "mean"
    assert result.methodology.interpolation == "none"


def test_only_highest_revision_at_each_exact_timestamp_is_selected() -> None:
    observations = [
        raw(START, 100, revision=1, record_id="old"),
        raw(START, 250, revision=3, record_id="latest"),
        raw(START, 200, revision=2, record_id="middle"),
    ]

    result = materialize_half_hours(
        series(observations, cadence_minutes=30),
        start=START,
        end=START + timedelta(minutes=30),
    )

    interval = result.intervals[0]
    assert interval.raw_sample_count == 3
    assert interval.selected_sample_count == 1
    assert interval.value == 250
    assert interval.selected_samples[0].revision == 3
    assert interval.selected_samples[0].source_record_ids == ("latest",)
    assert interval.raw_source_record_ids == ("latest", "middle", "old")


def test_equivalent_duplicates_at_highest_revision_are_one_logical_sample() -> None:
    observations = [
        raw(START, 250, revision=3, record_id="delivery-b"),
        raw(START, 250, revision=3, record_id="delivery-a"),
    ]

    result = materialize_half_hours(
        series(observations, cadence_minutes=30),
        start=START,
        end=START + timedelta(minutes=30),
    )

    interval = result.intervals[0]
    assert interval.raw_sample_count == 2
    assert interval.selected_sample_count == 1
    assert interval.value == 250
    assert interval.selected_samples[0].source_record_ids == (
        "delivery-a",
        "delivery-b",
    )


def test_conflicting_values_at_highest_revision_are_rejected() -> None:
    observations = [
        raw(START, 250, revision=3, record_id="conflict-a"),
        raw(START, 251, revision=3, record_id="conflict-b"),
    ]

    with pytest.raises(ValueError, match="conflicting values at highest revision 3"):
        materialize_half_hours(
            series(observations, cadence_minutes=30),
            start=START,
            end=START + timedelta(minutes=30),
        )


def test_conflicting_discarded_revision_does_not_override_clean_highest_revision() -> None:
    observations = [
        raw(START, 100, revision=1, record_id="old-a"),
        raw(START, 101, revision=1, record_id="old-b"),
        raw(START, 250, revision=2, record_id="selected"),
    ]

    result = materialize_half_hours(
        series(observations, cadence_minutes=30),
        start=START,
        end=START + timedelta(minutes=30),
    )

    assert result.intervals[0].value == 250
    assert result.intervals[0].selected_samples[0].source_record_ids == (
        "selected",
    )


def test_default_complete_coverage_withholds_value_for_one_gap() -> None:
    observations = complete_interval()
    missing_timestamp = START + timedelta(minutes=10)
    observations = [item for item in observations if item.timestamp != missing_timestamp]

    result = materialize_half_hours(
        series(observations),
        start=START,
        end=START + timedelta(minutes=30),
    )

    interval = result.intervals[0]
    assert interval.status == MaterializationStatus.INSUFFICIENT_DATA
    assert interval.expected_sample_count == 6
    assert interval.selected_sample_count == 5
    assert interval.raw_sample_count == 5
    assert interval.coverage_fraction == pytest.approx(5 / 6)
    assert interval.missing_timestamps == (missing_timestamp,)
    assert interval.value is None
    assert interval.materialized_source_record_id is None
    assert MaterializationReason.MISSING_EXPECTED_TIMESTAMPS in interval.reasons
    assert MaterializationReason.COVERAGE_BELOW_THRESHOLD in interval.reasons
    assert result.series.observations == []


def test_explicit_partial_threshold_can_emit_mean_but_never_interpolates_gap() -> None:
    observations = complete_interval()
    missing_timestamp = START + timedelta(minutes=10)
    observations = [item for item in observations if item.timestamp != missing_timestamp]

    result = materialize_half_hours(
        series(observations),
        start=START,
        end=START + timedelta(minutes=30),
        minimum_coverage_fraction=5 / 6,
    )

    interval = result.intervals[0]
    assert interval.status == MaterializationStatus.AVAILABLE
    assert interval.missing_timestamps == (missing_timestamp,)
    assert MaterializationReason.PARTIAL_COVERAGE_ACCEPTED in interval.reasons
    assert interval.value == pytest.approx((0 + 1 + 3 + 4 + 5) / 5)
    assert len(result.series.observations) == 1


def test_unexpected_timestamp_invalidates_interval_even_with_complete_grid() -> None:
    unexpected = raw(
        START + timedelta(minutes=7),
        999,
        record_id="unexpected-seven-minutes",
    )

    result = materialize_half_hours(
        series([*complete_interval(), unexpected]),
        start=START,
        end=START + timedelta(minutes=30),
    )

    interval = result.intervals[0]
    assert interval.selected_sample_count == 6
    assert interval.raw_sample_count == 7
    assert interval.coverage_fraction == 1
    assert interval.unexpected_timestamps == (unexpected.timestamp,)
    assert interval.unexpected_samples[0].source_record_ids == (
        "unexpected-seven-minutes",
    )
    assert interval.status == MaterializationStatus.INSUFFICIENT_DATA
    assert interval.value is None
    assert interval.reasons == (MaterializationReason.UNEXPECTED_TIMESTAMPS,)
    assert result.series.observations == []


def test_misaligned_value_does_not_fill_an_expected_timestamp() -> None:
    observations = complete_interval()
    expected = START + timedelta(minutes=5)
    observations = [item for item in observations if item.timestamp != expected]
    observations.append(raw(START + timedelta(minutes=6), 1, record_id="misaligned"))

    result = materialize_half_hours(
        series(observations),
        start=START,
        end=START + timedelta(minutes=30),
    )

    interval = result.intervals[0]
    assert interval.missing_timestamps == (expected,)
    assert interval.unexpected_timestamps == (START + timedelta(minutes=6),)
    assert interval.selected_sample_count == 5
    assert interval.status == MaterializationStatus.INSUFFICIENT_DATA
    assert interval.value is None


def test_outside_bounds_samples_are_exposed_without_poisoning_complete_interval() -> None:
    before = raw(START - timedelta(minutes=5), 1, record_id="before")
    at_end = raw(START + timedelta(minutes=30), 2, record_id="at-end")

    result = materialize_half_hours(
        series([before, *complete_interval(), at_end]),
        start=START,
        end=START + timedelta(minutes=30),
    )

    assert result.raw_sample_count == 8
    assert result.outside_bounds_raw_count == 2
    assert result.outside_bounds_timestamps == (
        before.timestamp,
        at_end.timestamp,
    )
    assert result.outside_bounds_source_record_ids == ("at-end", "before")
    assert result.intervals[0].status == MaterializationStatus.AVAILABLE


def test_conflicting_outside_values_do_not_invalidate_requested_window() -> None:
    outside_timestamp = START - timedelta(minutes=5)
    outside = [
        raw(outside_timestamp, 1, revision=4, record_id="outside-conflict-a"),
        raw(outside_timestamp, 2, revision=4, record_id="outside-conflict-b"),
    ]

    result = materialize_half_hours(
        series([*outside, *complete_interval()]),
        start=START,
        end=START + timedelta(minutes=30),
    )

    assert result.outside_bounds_raw_count == 2
    assert result.outside_bounds_timestamps == (outside_timestamp,)
    assert result.outside_bounds_source_record_ids == (
        "outside-conflict-a",
        "outside-conflict-b",
    )
    assert result.intervals[0].status == MaterializationStatus.AVAILABLE
    assert len(result.series.observations) == 1


def test_empty_raw_series_yields_evidence_but_no_values() -> None:
    result = materialize_half_hours(
        series([]),
        start=START,
        end=START + timedelta(hours=1),
    )

    assert result.interval_count == 2
    assert result.raw_sample_count == 0
    assert result.selected_sample_count == 0
    assert result.series.observations == []
    assert all(
        interval.status == MaterializationStatus.INSUFFICIENT_DATA
        and interval.value is None
        and interval.selected_sample_count == 0
        and interval.expected_sample_count == 6
        for interval in result.intervals
    )


@pytest.mark.parametrize("cadence", [0, 4, 7, 29, 31])
def test_source_cadence_must_divide_thirty_minutes(cadence: int) -> None:
    with pytest.raises(ValidationError):
        series([], cadence_minutes=cadence)


@pytest.mark.parametrize("cadence", [True, 5.0, "5"])
def test_source_cadence_is_a_strict_integer(cadence: object) -> None:
    with pytest.raises(ValidationError):
        RawMetricSeries(
            identity=IDENTITY,
            source_cadence_minutes=cadence,
            observations=(),
        )


@pytest.mark.parametrize(
    "timestamp",
    [START.replace(tzinfo=None)],
)
def test_raw_timestamp_must_be_aware(timestamp: datetime) -> None:
    with pytest.raises(ValidationError):
        raw(timestamp, 1)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_raw_values_must_be_finite(value: float) -> None:
    with pytest.raises(ValidationError):
        raw(START, value)


@pytest.mark.parametrize(
    ("start", "end", "error"),
    [
        (
            START.replace(tzinfo=None),
            START + timedelta(minutes=30),
            "timezone-aware",
        ),
        (
            START + timedelta(minutes=5),
            START + timedelta(minutes=30),
            "exact UTC half-hour",
        ),
        (START, START, "after start"),
        (START + timedelta(minutes=30), START, "after start"),
    ],
)
def test_materialization_requires_aware_ordered_exact_half_hour_bounds(
    start: datetime,
    end: datetime,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        materialize_half_hours(series([]), start=start, end=end)


@pytest.mark.parametrize(
    "threshold",
    [0, -0.1, 1.1, float("nan"), float("inf")],
)
def test_coverage_threshold_must_be_finite_and_bounded(threshold: float) -> None:
    with pytest.raises(ValueError, match="finite and in"):
        materialize_half_hours(
            series([]),
            start=START,
            end=START + timedelta(minutes=30),
            minimum_coverage_fraction=threshold,
        )


def test_coverage_threshold_rejects_bool_and_non_number() -> None:
    with pytest.raises(TypeError, match="must be a number"):
        materialize_half_hours(
            series([]),
            start=START,
            end=START + timedelta(minutes=30),
            minimum_coverage_fraction=True,
        )
    with pytest.raises(TypeError, match="must be a number"):
        materialize_half_hours(
            series([]),
            start=START,
            end=START + timedelta(minutes=30),
            minimum_coverage_fraction="1",  # type: ignore[arg-type]
        )


def test_equivalent_instants_in_different_timezones_share_revision_selection() -> None:
    local_timestamp = START.astimezone(LONDON)
    observations = [
        raw(START, 100, revision=1, record_id="utc-old"),
        raw(local_timestamp, 200, revision=2, record_id="london-latest"),
    ]

    result = materialize_half_hours(
        series(observations, cadence_minutes=30),
        start=START,
        end=START + timedelta(minutes=30),
    )

    selected = result.intervals[0].selected_samples[0]
    assert selected.timestamp == START
    assert selected.revision == 2
    assert selected.value == 200


def test_materialized_provenance_is_deterministic_across_input_order() -> None:
    observations = complete_interval()

    forward = materialize_half_hours(
        series(observations),
        start=START,
        end=START + timedelta(minutes=30),
    )
    reverse = materialize_half_hours(
        series(list(reversed(observations))),
        start=START,
        end=START + timedelta(minutes=30),
    )

    assert (
        forward.series.observations[0].source_record_id
        == reverse.series.observations[0].source_record_id
    )
    assert forward.series.observations[0].source_record_id.startswith(
        "50hz:half-hour-mean:v1:"
    )
