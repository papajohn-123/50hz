from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.briefing import (
    BestWindowInput,
    BriefingCoverageInput,
    BriefingInput,
    BriefingSection,
    BriefingSourceStatus,
    BriefingStatus,
    ComparisonPeriod,
    CurrentFactClass,
    CurrentPositionInput,
    CurrentPositionStatus,
    CurrentValueInput,
    FutureFactClass,
    FutureMomentInput,
    ObservedChangeInput,
    ReportedEventInput,
    ReportedEventSeverity,
    ReportedEventTiming,
    RevisionWatermark,
    SourceState,
    build_briefing,
)


NOW = datetime(2026, 7, 11, 19, 0, tzinfo=UTC)
LAST_HOUR = ComparisonPeriod(
    id="last-hour",
    label="the previous hour",
    start=NOW - timedelta(hours=1),
    end=NOW,
)


def watermark(as_of: datetime = NOW) -> RevisionWatermark:
    return RevisionWatermark(
        revision_token="briefing:2026-07-11T19:00Z:r7",
        as_of=as_of,
        observed_through=as_of - timedelta(minutes=2),
        forecast_captured_through=as_of - timedelta(minutes=10),
        reported_through=as_of - timedelta(minutes=5),
    )


def source(
    source_id: str,
    state: SourceState = SourceState.LIVE,
    *,
    revision: int = 1,
) -> BriefingSourceStatus:
    return BriefingSourceStatus(
        source_id=source_id,
        dataset=source_id.split(".")[-1].upper(),
        state=state,
        revision=revision,
        observed_at=NOW - timedelta(minutes=5),
        retrieved_at=NOW - timedelta(minutes=1),
    )


def current_value(
    metric_id: str,
    label: str,
    value: float,
    unit: str,
    *,
    fact_class: CurrentFactClass = CurrentFactClass.OBSERVED,
    priority: float = 0.5,
    observed_at: datetime = NOW - timedelta(minutes=5),
    stable_id: str | None = None,
    revision: int = 1,
) -> CurrentValueInput:
    return CurrentValueInput(
        stable_id=stable_id or f"current:{metric_id}",
        metric_id=metric_id,
        label=label,
        value=value,
        unit=unit,
        fact_class=fact_class,
        observed_at=observed_at,
        source_ids=[f"source.{metric_id}"],
        priority=priority,
        revision=revision,
    )


def change(
    stable_id: str,
    metric_id: str,
    delta: float,
    *,
    significance: float,
    threshold: float = 5,
    observed_at: datetime = NOW - timedelta(minutes=5),
    revision: int = 1,
    comparison_period_id: str = "last-hour",
) -> ObservedChangeInput:
    return ObservedChangeInput(
        stable_id=stable_id,
        metric_id=metric_id,
        label=metric_id.replace(".", " ").title(),
        current_value=100 + delta,
        previous_value=100,
        delta=delta,
        unit="MW",
        observed_at=observed_at,
        comparison_period_id=comparison_period_id,
        meaningful_threshold=threshold,
        significance=significance,
        source_ids=[f"source.{metric_id}"],
        revision=revision,
    )


def moment(
    stable_id: str,
    minutes_from_now: int,
    *,
    importance: float,
    value: float | None = None,
    unit: str | None = None,
    fact_class: FutureFactClass = FutureFactClass.FORECAST,
    revision: int = 1,
) -> FutureMomentInput:
    starts_at = NOW + timedelta(minutes=minutes_from_now)
    return FutureMomentInput(
        stable_id=stable_id,
        label=stable_id.replace("-", " ").title(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        fact_class=fact_class,
        importance=importance,
        source_ids=["source.forecast"],
        value=value,
        unit=unit,
        updated_at=NOW - timedelta(minutes=10),
        revision=revision,
    )


def event(
    stable_id: str,
    severity: ReportedEventSeverity,
    *,
    published_minutes_ago: int,
    starts_in_minutes: int = -60,
    ends_in_minutes: int | None = 120,
    revision_number: int = 1,
    summary: str | None = None,
) -> ReportedEventInput:
    starts_at = NOW + timedelta(minutes=starts_in_minutes)
    ends_at = (
        NOW + timedelta(minutes=ends_in_minutes)
        if ends_in_minutes is not None
        else None
    )
    return ReportedEventInput(
        stable_id=stable_id,
        revision_id=f"{stable_id}:r{revision_number}",
        revision_number=revision_number,
        title=stable_id.replace("-", " ").title(),
        summary=summary or f"The publisher reports {stable_id.replace('-', ' ')}.",
        severity=severity,
        published_at=NOW - timedelta(minutes=published_minutes_ago),
        starts_at=starts_at,
        ends_at=ends_at,
        source_ids=["elexon.reported"],
    )


def best_window(
    *,
    start: datetime = NOW + timedelta(hours=2),
    end: datetime = NOW + timedelta(hours=3),
) -> BestWindowInput:
    return BestWindowInput(
        stable_id="best-window:carbon:1",
        label="Lowest national carbon forecast window",
        start=start,
        end=end,
        average_value=42,
        unit="gCO2/kWh",
        source_ids=["neso.carbon.forecast"],
        methodology_version="50hz.local.flexible-use.v1",
        captured_at=NOW - timedelta(minutes=10),
    )


def briefing_input(**updates: Any) -> BriefingInput:
    values: dict[str, Any] = {
        "as_of": NOW,
        "revision_watermark": watermark(),
    }
    values.update(updates)
    return BriefingInput(**values)


def test_current_position_is_typed_ranked_bounded_and_deterministic() -> None:
    older_demand = current_value(
        "demand",
        "Demand",
        27_900,
        "MW",
        priority=0.9,
        stable_id="current:demand",
        revision=1,
    )
    revised_demand = current_value(
        "demand",
        "Demand",
        28_100,
        "MW",
        priority=0.9,
        stable_id="current:demand",
        revision=2,
    )
    values = [
        current_value(
            "carbon",
            "Carbon intensity",
            84,
            "gCO2/kWh",
            fact_class=CurrentFactClass.ESTIMATED,
            priority=0.8,
        ),
        current_value("frequency", "Frequency", 50.01, "Hz", priority=0.7),
        current_value("imports", "Net imports", -420, "MW", priority=0.6),
        older_demand,
        revised_demand,
    ]
    facts = briefing_input(
        now=CurrentPositionInput(
            values=values,
            expected_metric_ids=["demand", "carbon", "frequency"],
        ),
        source_statuses=[source("source.live")],
    )

    first = build_briefing(facts)
    second = build_briefing(
        facts.model_copy(
            update={
                "now": facts.now.model_copy(
                    update={"values": list(reversed(values))}
                )
            }
        )
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.generated_at == NOW
    assert first.as_of == NOW
    assert first.now.status is CurrentPositionStatus.COMPLETE
    assert [value.metric_id for value in first.now.values] == [
        "demand",
        "carbon",
        "frequency",
    ]
    assert first.now.values[0].value == 28_100
    assert first.now.values[1].fact_class is CurrentFactClass.ESTIMATED
    assert "Carbon intensity is 84 gCO2/kWh (estimated)" in first.now.text
    assert BriefingSection.NOW in first.coverage.available_sections
    assert first.methodology.max_current_values == 3


def test_meaningful_changes_filter_zero_small_future_and_old_revisions() -> None:
    older = change(
        "change:demand",
        "demand",
        10,
        significance=0.9,
        revision=1,
    )
    revised = change(
        "change:demand",
        "demand",
        30,
        significance=0.9,
        revision=2,
    )
    candidates = [
        change("change:wind", "wind", 20, significance=0.9, threshold=5),
        older,
        revised,
        change("change:carbon", "carbon", -40, significance=0.8, threshold=10),
        change("change:frequency", "frequency", 8, significance=0.7),
        change("change:zero", "zero", 0, significance=1.0),
        change("change:small", "small", 4, significance=1.0, threshold=5),
        change(
            "change:future",
            "future",
            100,
            significance=1.0,
            observed_at=NOW + timedelta(minutes=1),
        ),
    ]
    result = build_briefing(
        briefing_input(
            changes=candidates,
            comparison_periods=[LAST_HOUR],
            source_statuses=[source("source.observed")],
        )
    )

    assert [item.stable_id for item in result.changes] == [
        "change:demand",
        "change:wind",
        "change:carbon",
    ]
    assert result.changes[0].delta == 30
    assert result.changes[2].direction.value == "down"
    assert all(item.delta != 0 for item in result.changes)
    assert all(item.text for item in result.changes)
    assert result.changes[0].text == "Demand rose by 30 MW over the previous hour."
    assert result.comparison_periods == [LAST_HOUR]
    generated_text = " ".join(item.text for item in result.changes).casefold()
    assert "because" not in generated_text
    assert "caused" not in generated_text


def test_next_moments_are_future_only_deduplicated_and_chronological() -> None:
    old_revision = moment("wind-peak", 60, importance=0.9, value=8_000, unit="MW")
    new_revision = old_revision.model_copy(
        update={"revision": 2, "value": 8_200}
    )
    candidates = [
        moment("later", 120, importance=1.0),
        moment("same-time-b", 30, importance=0.9),
        moment("same-time-a", 30, importance=0.9),
        moment("middle", 90, importance=0.8),
        old_revision,
        new_revision,
        moment("past", -30, importance=1.0),
        moment(
            "reported-plan",
            45,
            importance=0.95,
            fact_class=FutureFactClass.REPORTED,
        ),
    ]

    result = build_briefing(
        briefing_input(
            next_moments=candidates,
            source_statuses=[source("source.forecast")],
        )
    )

    assert [item.stable_id for item in result.next_moments] == [
        "same-time-a",
        "same-time-b",
        "reported-plan",
    ]
    assert all(item.starts_at > NOW for item in result.next_moments)
    assert result.next_moments[0].text.endswith("20:30.")
    assert "is reported for" in result.next_moments[2].text
    assert len(result.next_moments) == 3


def test_cross_day_copy_includes_london_weekday_and_date() -> None:
    result = build_briefing(
        briefing_input(
            next_moments=[moment("overnight-wind", 6 * 60, importance=0.8)],
            best_window=best_window(
                start=NOW + timedelta(hours=6),
                end=NOW + timedelta(hours=7),
            ),
        )
    )

    assert "Sun 12 Jul, 02:00" in result.next_moments[0].text
    assert "Sun 12 Jul, 02:00–Sun 12 Jul, 03:00" in result.best_window.text


def test_event_heavy_briefing_ranks_three_but_reports_deduped_total() -> None:
    old_revision = event(
        "critical-active",
        ReportedEventSeverity.NOTABLE,
        published_minutes_ago=30,
        revision_number=1,
    )
    revised = event(
        "critical-active",
        ReportedEventSeverity.CRITICAL,
        published_minutes_ago=10,
        revision_number=2,
    )
    candidates = [
        event(
            "critical-upcoming",
            ReportedEventSeverity.CRITICAL,
            published_minutes_ago=20,
            starts_in_minutes=120,
            ends_in_minutes=240,
        ),
        old_revision,
        revised,
        event(
            "material-active",
            ReportedEventSeverity.MATERIAL,
            published_minutes_ago=5,
        ),
        event(
            "notable-active",
            ReportedEventSeverity.NOTABLE,
            published_minutes_ago=2,
        ),
        event(
            "info-active",
            ReportedEventSeverity.INFO,
            published_minutes_ago=1,
        ),
        event(
            "too-far-away",
            ReportedEventSeverity.CRITICAL,
            published_minutes_ago=1,
            starts_in_minutes=24 * 60 + 1,
            ends_in_minutes=26 * 60,
        ),
        event(
            "ended",
            ReportedEventSeverity.CRITICAL,
            published_minutes_ago=60,
            starts_in_minutes=-120,
            ends_in_minutes=-1,
        ),
    ]
    facts = briefing_input(
        reported_events=candidates,
        source_statuses=[source("source.reported")],
    )

    first = build_briefing(facts)
    second = build_briefing(
        facts.model_copy(update={"reported_events": list(reversed(candidates))})
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.reported_events.total_count == 5
    assert [item.stable_id for item in first.reported_events.items] == [
        "critical-active",
        "critical-upcoming",
        "material-active",
    ]
    assert first.reported_events.items[0].revision_number == 2
    assert first.reported_events.items[0].timing is ReportedEventTiming.ACTIVE
    assert first.reported_events.items[1].timing is ReportedEventTiming.UPCOMING
    assert first.reported_events.items[1].text.startswith(
        "Upcoming reported event at"
    )
    assert all(
        item.evidence_class == "reported"
        for item in first.reported_events.items
    )


def test_best_window_requires_complete_source_coverage_and_can_start_now() -> None:
    result = build_briefing(
        briefing_input(
            best_window=best_window(start=NOW, end=NOW + timedelta(hours=1)),
            source_statuses=[source("source.forecast")],
        )
    )

    assert result.best_window is not None
    assert result.best_window.start == NOW
    assert result.best_window.coverage_fraction == 1
    assert "20:00–21:00" in result.best_window.text
    assert "savings" not in result.best_window.text.casefold()
    assert BriefingSection.BEST_WINDOW in result.coverage.available_sections

    expired = build_briefing(
        briefing_input(
            best_window=best_window(
                start=NOW - timedelta(hours=2),
                end=NOW - timedelta(hours=1),
            )
        )
    )
    assert expired.best_window is None


def test_partial_current_facts_remain_useful_and_explicit() -> None:
    result = build_briefing(
        briefing_input(
            now=CurrentPositionInput(
                values=[current_value("demand", "Demand", 28_100, "MW")],
                expected_metric_ids=["demand", "carbon", "frequency"],
            ),
            source_statuses=[source("source.demand", SourceState.DELAYED)],
            coverage=BriefingCoverageInput(missing_families=["carbon", "frequency"]),
        )
    )

    assert result.now.status is CurrentPositionStatus.PARTIAL
    assert result.now.missing_metric_ids == ["carbon", "frequency"]
    assert result.now.values[0].value == 28_100
    assert result.coverage.status is BriefingStatus.PARTIAL
    assert "partial" in result.summary.casefold()
    assert BriefingSection.NOW in result.coverage.available_sections


def test_expected_current_values_missing_with_future_data_is_partial() -> None:
    result = build_briefing(
        briefing_input(
            now=CurrentPositionInput(expected_metric_ids=["demand", "carbon"]),
            next_moments=[moment("wind-rise", 60, importance=0.8)],
            source_statuses=[source("source.forecast")],
        )
    )

    assert result.now.status is CurrentPositionStatus.UNAVAILABLE
    assert result.now.missing_metric_ids == ["carbon", "demand"]
    assert result.coverage.status is BriefingStatus.PARTIAL


def test_offline_briefing_retains_supplied_current_and_reported_evidence() -> None:
    result = build_briefing(
        briefing_input(
            now=CurrentPositionInput(
                values=[current_value("frequency", "Frequency", 49.99, "Hz")],
                expected_metric_ids=["frequency"],
            ),
            reported_events=[
                event(
                    "system-warning",
                    ReportedEventSeverity.MATERIAL,
                    published_minutes_ago=15,
                )
            ],
            source_statuses=[
                source("source.live", SourceState.UNAVAILABLE),
                source("source.reported", SourceState.UNAVAILABLE),
            ],
        )
    )

    assert result.coverage.status is BriefingStatus.OFFLINE
    assert result.now.values[0].value == 49.99
    assert result.reported_events.total_count == 1
    assert "supplied evidence" in result.summary
    assert result.summary


def test_empty_and_observed_only_fixtures_have_finite_useful_copy() -> None:
    empty = build_briefing(briefing_input())
    observed_only = build_briefing(
        briefing_input(
            now=CurrentPositionInput(
                values=[current_value("demand", "Demand", 28_100, "MW")],
                expected_metric_ids=["demand"],
            ),
            changes=[change("change:demand", "demand", 15, significance=0.8)],
            comparison_periods=[LAST_HOUR],
            source_statuses=[source("source.observed")],
        )
    )

    assert empty.coverage.status is BriefingStatus.EMPTY
    assert empty.now.status is CurrentPositionStatus.UNAVAILABLE
    assert empty.now.text == "No validated current values are available."
    assert "No material observed changes" in empty.summary
    assert observed_only.coverage.status is BriefingStatus.OBSERVED_ONLY
    assert "no qualifying future" in observed_only.summary.casefold()
    _assert_finite(empty)
    _assert_finite(observed_only)


def test_source_statuses_are_revision_deduped_and_zero_states_are_omitted() -> None:
    older = source("source.carbon", SourceState.STALE, revision=1)
    newer = source("source.carbon", SourceState.LIVE, revision=2)
    result = build_briefing(
        briefing_input(
            now=CurrentPositionInput(
                values=[current_value("carbon", "Carbon", 84, "gCO2/kWh")]
            ),
            source_statuses=[older, source("source.demand"), newer],
        )
    )

    assert [item.source_id for item in result.source_statuses] == [
        "source.carbon",
        "source.demand",
    ]
    assert result.source_statuses[0].state is SourceState.LIVE
    assert result.coverage.source_counts_by_state == {SourceState.LIVE: 2}
    assert SourceState.STALE not in result.coverage.source_counts_by_state


def test_london_display_period_metadata_is_dst_aware_and_versioned() -> None:
    summer = build_briefing(briefing_input())
    fallback_now = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    fallback = build_briefing(
        BriefingInput(
            as_of=fallback_now,
            revision_watermark=RevisionWatermark(
                revision_token="fallback:r1",
                as_of=fallback_now,
            ),
        )
    )

    assert summer.display_period.timezone == "Europe/London"
    assert summer.display_period.label == "Saturday evening"
    assert summer.display_period.starts_at.utcoffset() == timedelta(hours=1)
    assert summer.methodology.version == "50hz.briefing.v1"
    assert summer.methodology.causal_attribution is False
    assert fallback.display_period.name.value == "overnight"
    assert fallback.display_period.starts_at.utcoffset() == timedelta(hours=1)
    assert fallback.display_period.ends_at.utcoffset() == timedelta(0)
    assert (
        fallback.display_period.ends_at.astimezone(UTC)
        - fallback.display_period.starts_at.astimezone(UTC)
    ) == timedelta(hours=7)


def test_revision_watermark_and_only_used_comparison_periods_are_preserved() -> None:
    unused = ComparisonPeriod(
        id="unused",
        label="an unused period",
        start=NOW - timedelta(days=1),
        end=NOW - timedelta(hours=23),
    )
    mark = watermark()
    result = build_briefing(
        briefing_input(
            changes=[change("change:wind", "wind", 20, significance=0.8)],
            comparison_periods=[unused, LAST_HOUR],
            revision_watermark=mark,
        )
    )

    assert result.revision_watermark == mark
    assert result.comparison_periods == [LAST_HOUR]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ObservedChangeInput(
            stable_id="bad",
            metric_id="demand",
            label="Demand",
            current_value=110,
            previous_value=100,
            delta=9,
            unit="MW",
            observed_at=NOW,
            comparison_period_id="last-hour",
            meaningful_threshold=5,
            significance=0.5,
            source_ids=["source.demand"],
        ),
        lambda: FutureMomentInput(
            stable_id="bad",
            label="Bad future value",
            starts_at=NOW + timedelta(hours=1),
            fact_class=FutureFactClass.FORECAST,
            importance=0.5,
            source_ids=["source.forecast"],
            value=12,
            updated_at=NOW,
        ),
        lambda: CurrentValueInput(
            stable_id="bad",
            metric_id="frequency",
            label="Frequency",
            value=float("nan"),
            unit="Hz",
            fact_class=CurrentFactClass.OBSERVED,
            observed_at=NOW,
            source_ids=["source.frequency"],
            priority=0.5,
        ),
        lambda: CurrentPositionInput(expected_metric_ids=["demand", "demand"]),
        lambda: BestWindowInput(
            stable_id="bad",
            label="Bad coverage",
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=2),
            average_value=40,
            unit="gCO2/kWh",
            source_ids=["source.forecast"],
            coverage_fraction=0.5,
            methodology_version="v1",
            captured_at=NOW,
        ),
        lambda: RevisionWatermark(
            revision_token="bad",
            as_of=NOW,
            observed_through=NOW + timedelta(seconds=1),
        ),
    ],
)
def test_invalid_or_nonfinite_fact_contracts_are_rejected(factory) -> None:
    with pytest.raises((ValidationError, ValueError)):
        factory()


def test_changes_must_reference_explicit_comparison_period_metadata() -> None:
    with pytest.raises(ValidationError, match="comparison period"):
        briefing_input(
            changes=[change("change:wind", "wind", 20, significance=0.8)]
        )


def test_conflicting_duplicate_revision_is_rejected() -> None:
    first = current_value("demand", "Demand", 28_000, "MW", revision=2)
    conflict = current_value("demand", "Demand", 28_100, "MW", revision=2)

    with pytest.raises(ValueError, match="conflicting duplicate revision"):
        build_briefing(
            briefing_input(now=CurrentPositionInput(values=[first, conflict]))
        )


def test_all_fixture_outputs_are_finite_bounded_and_have_non_llm_copy() -> None:
    fixtures = [
        build_briefing(briefing_input()),
        build_briefing(
            briefing_input(
                now=CurrentPositionInput(
                    values=[current_value("demand", "Demand", 28_100, "MW")]
                ),
                next_moments=[moment("wind-rise", 60, importance=0.8)],
                best_window=best_window(),
                source_statuses=[source("source.live")],
            )
        ),
        build_briefing(
            briefing_input(
                coverage=BriefingCoverageInput(missing_families=["forecast"]),
                source_statuses=[source("source.forecast", SourceState.STALE)],
                reported_events=[
                    event(
                        "reported-only",
                        ReportedEventSeverity.NOTABLE,
                        published_minutes_ago=5,
                    )
                ],
            )
        ),
    ]

    for result in fixtures:
        assert result.headline
        assert result.summary
        assert result.now.text
        assert len(result.now.values) <= 3
        assert len(result.changes) <= 3
        assert len(result.next_moments) <= 3
        assert len(result.reported_events.items) <= 3
        assert all(item.text for item in result.changes)
        assert all(item.text for item in result.next_moments)
        assert all(item.text for item in result.reported_events.items)
        _assert_finite(result)


def _assert_finite(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        for item in value.values():
            _assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite(item)
    elif isinstance(value, float):
        assert math.isfinite(value)
