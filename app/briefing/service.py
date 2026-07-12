"""Deterministic selection and copy for a bounded grid briefing."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.briefing.models import (
    DISPLAY_TIMEZONE,
    MAX_SECTION_ITEMS,
    BestWindowInput,
    Briefing,
    BriefingBestWindow,
    BriefingCoverage,
    BriefingCurrentPosition,
    BriefingCurrentValue,
    BriefingFutureMoment,
    BriefingInput,
    BriefingObservedChange,
    BriefingReportedEvent,
    BriefingReportedEvents,
    BriefingSection,
    BriefingSourceStatus,
    BriefingStatus,
    ChangeDirection,
    ComparisonPeriod,
    CurrentPositionInput,
    CurrentPositionStatus,
    CurrentValueInput,
    DisplayPeriod,
    DisplayPeriodName,
    FutureFactClass,
    FutureMomentInput,
    ObservedChangeInput,
    ReportedEventInput,
    ReportedEventSeverity,
    ReportedEventTiming,
    SourceState,
)


_LONDON = ZoneInfo(DISPLAY_TIMEZONE)
_SEVERITY_RANK = {
    ReportedEventSeverity.INFO: 0,
    ReportedEventSeverity.NOTABLE: 1,
    ReportedEventSeverity.MATERIAL: 2,
    ReportedEventSeverity.CRITICAL: 3,
}
_SOURCE_STATE_RANK = {
    SourceState.LIVE: 0,
    SourceState.DELAYED: 1,
    SourceState.STALE: 2,
    SourceState.UNAVAILABLE: 3,
}


def build_briefing(facts: BriefingInput) -> Briefing:
    as_of = facts.as_of.astimezone(UTC)
    period = _display_period(as_of)
    current = _present_current(facts.now, as_of=as_of)
    comparison_periods = {
        period.id: period for period in facts.comparison_periods
    }
    changes = _select_changes(facts.changes, comparison_periods, now=as_of)
    next_moments = _select_next(facts.next_moments, now=as_of)
    relevant_events = _relevant_reported_events(facts.reported_events, now=as_of)
    selected_events = [
        _present_event(event, now=as_of)
        for event in relevant_events[:MAX_SECTION_ITEMS]
    ]
    best_window = _present_best_window(facts.best_window, now=as_of)
    sources = _dedupe_sources(facts.source_statuses)
    coverage = _coverage(
        current=current,
        changes=changes,
        next_moments=next_moments,
        events=selected_events,
        best_window=best_window,
        sources=sources,
        missing_families=facts.coverage.missing_families,
        notes=facts.coverage.notes,
    )
    used_period_ids = {change.comparison_period_id for change in changes}
    used_periods = sorted(
        (
            comparison_periods[period_id]
            for period_id in used_period_ids
        ),
        key=lambda item: (item.start, item.end, item.id),
    )

    return Briefing(
        generated_at=as_of,
        as_of=as_of,
        now=current,
        display_period=period,
        headline=f"{period.label.capitalize()} grid briefing",
        summary=_summary(
            coverage.status,
            current_count=len(current.values),
            change_count=len(changes),
            next_count=len(next_moments),
            shown_event_count=len(selected_events),
            total_event_count=len(relevant_events),
            has_best_window=best_window is not None,
        ),
        changes=changes,
        next_moments=next_moments,
        reported_events=BriefingReportedEvents(
            items=selected_events,
            total_count=len(relevant_events),
        ),
        best_window=best_window,
        coverage=coverage,
        source_statuses=sources,
        comparison_periods=used_periods,
        revision_watermark=facts.revision_watermark,
        limitations=[
            "The briefing ranks supplied observed, forecast, and reported facts; "
            "it does not infer why a change occurred.",
            "Omitted sections mean no qualifying fact was supplied, not that the "
            "underlying value was zero.",
        ],
    )


def _present_current(
    position: CurrentPositionInput,
    *,
    as_of: datetime,
) -> BriefingCurrentPosition:
    eligible = [item for item in position.values if item.observed_at <= as_of]
    deduped = _dedupe(
        eligible,
        identity=lambda item: item.stable_id,
        revision_key=lambda item: (
            item.revision,
            item.observed_at,
            item.metric_id,
            item.label,
            item.value,
            item.unit,
            item.fact_class,
            item.priority,
            tuple(sorted(set(item.source_ids))),
        ),
    )
    available = deduped
    available_metric_ids = {item.metric_id for item in available}
    missing = sorted(set(position.expected_metric_ids) - available_metric_ids)
    ranked = sorted(
        available,
        key=lambda item: (
            -item.priority,
            -item.observed_at.timestamp(),
            item.metric_id,
            item.stable_id,
        ),
    )
    unique_metrics: list[CurrentValueInput] = []
    used_metrics: set[str] = set()
    for item in ranked:
        if item.metric_id in used_metrics:
            continue
        unique_metrics.append(item)
        used_metrics.add(item.metric_id)
        if len(unique_metrics) == MAX_SECTION_ITEMS:
            break
    values = [
        BriefingCurrentValue(
            stable_id=item.stable_id,
            metric_id=item.metric_id,
            label=item.label,
            value=item.value,
            unit=item.unit,
            fact_class=item.fact_class,
            observed_at=item.observed_at,
            source_ids=sorted(set(item.source_ids)),
        )
        for item in unique_metrics
    ]
    if not values:
        status = CurrentPositionStatus.UNAVAILABLE
        text = "No validated current values are available."
        position_as_of = None
    else:
        status = (
            CurrentPositionStatus.PARTIAL
            if missing
            else CurrentPositionStatus.COMPLETE
        )
        text = "Current position: " + "; ".join(
            _current_value_text(value) for value in values
        ) + "."
        position_as_of = max(value.observed_at for value in values)
    return BriefingCurrentPosition(
        status=status,
        as_of=position_as_of,
        values=values,
        missing_metric_ids=missing,
        text=text,
    )


def _select_changes(
    candidates: list[ObservedChangeInput],
    periods: dict[str, ComparisonPeriod],
    *,
    now: datetime,
) -> list[BriefingObservedChange]:
    eligible = [
        item
        for item in candidates
        if item.observed_at <= now
        and periods[item.comparison_period_id].end <= now
    ]
    deduped = _dedupe(
        eligible,
        identity=lambda item: item.stable_id,
        revision_key=lambda item: (
            item.revision,
            item.observed_at,
            item.metric_id,
            item.label,
            item.current_value,
            item.previous_value,
            item.delta,
            item.unit,
            item.meaningful_threshold,
            item.significance,
            tuple(sorted(set(item.source_ids))),
        ),
    )
    meaningful = [
        item
        for item in deduped
        if item.delta != 0
        and abs(item.delta) >= item.meaningful_threshold
    ]
    ranked = sorted(
        meaningful,
        key=lambda item: (
            -item.significance,
            -(abs(item.delta) / item.meaningful_threshold),
            -item.observed_at.timestamp(),
            item.metric_id,
            item.stable_id,
        ),
    )[:MAX_SECTION_ITEMS]
    return [
        BriefingObservedChange(
            stable_id=item.stable_id,
            metric_id=item.metric_id,
            label=item.label,
            direction=(
                ChangeDirection.UP if item.delta > 0 else ChangeDirection.DOWN
            ),
            current_value=item.current_value,
            previous_value=item.previous_value,
            delta=item.delta,
            unit=item.unit,
            observed_at=item.observed_at,
            comparison_period_id=item.comparison_period_id,
            significance=item.significance,
            source_ids=sorted(set(item.source_ids)),
            text=_change_copy(item, periods[item.comparison_period_id]),
        )
        for item in ranked
    ]


def _select_next(
    candidates: list[FutureMomentInput],
    *,
    now: datetime,
) -> list[BriefingFutureMoment]:
    eligible = [item for item in candidates if item.updated_at <= now]
    deduped = _dedupe(
        eligible,
        identity=lambda item: item.stable_id,
        revision_key=lambda item: (
            item.revision,
            item.updated_at,
            item.starts_at,
            item.label,
            item.ends_at or item.starts_at,
            item.value if item.value is not None else float("-inf"),
            item.unit or "",
            item.fact_class,
            item.importance,
            tuple(sorted(set(item.source_ids))),
        ),
    )
    future = [item for item in deduped if item.starts_at > now]
    ranked = sorted(
        future,
        key=lambda item: (
            item.starts_at,
            -item.importance,
            item.stable_id,
        ),
    )[:MAX_SECTION_ITEMS]
    return [
        BriefingFutureMoment(
            stable_id=item.stable_id,
            label=item.label,
            starts_at=item.starts_at,
            ends_at=item.ends_at,
            fact_class=item.fact_class,
            importance=item.importance,
            source_ids=sorted(set(item.source_ids)),
            value=item.value,
            unit=item.unit,
            text=_next_copy(item, now=now),
        )
        for item in ranked
    ]


def _relevant_reported_events(
    candidates: list[ReportedEventInput],
    *,
    now: datetime,
) -> list[ReportedEventInput]:
    published = [item for item in candidates if item.published_at <= now]
    deduped = _dedupe(
        published,
        identity=lambda item: item.stable_id,
        revision_key=lambda item: (
            item.revision_number,
            item.published_at,
            item.revision_id,
            item.title,
            item.summary or "",
            item.severity,
            item.starts_at or datetime.min.replace(tzinfo=UTC),
            item.ends_at or datetime.max.replace(tzinfo=UTC),
            tuple(sorted(set(item.source_ids))),
        ),
    )
    relevant = [
        item
        for item in deduped
        if (
            (
                (item.starts_at is None or item.starts_at <= now)
                and (item.ends_at is None or item.ends_at > now)
            )
            or (
                item.starts_at is not None
                and now < item.starts_at <= now + timedelta(hours=24)
            )
        )
    ]
    return sorted(
        relevant,
        key=lambda item: (
            -_SEVERITY_RANK[item.severity],
            0 if _event_timing(item, now=now) is ReportedEventTiming.ACTIVE else 1,
            (
                -item.published_at.timestamp()
                if _event_timing(item, now=now) is ReportedEventTiming.ACTIVE
                else item.starts_at.timestamp()
            ),
            item.stable_id,
        ),
    )


def _present_event(
    event: ReportedEventInput,
    *,
    now: datetime,
) -> BriefingReportedEvent:
    timing = _event_timing(event, now=now)
    content = (event.summary or event.title).rstrip(".")
    reported_copy = (
        f"Upcoming reported event at {_local_time(event.starts_at, relative_to=now)}: {content}."
        if timing is ReportedEventTiming.UPCOMING and event.starts_at is not None
        else f"Reported: {content}."
    )
    return BriefingReportedEvent(
        stable_id=event.stable_id,
        revision_id=event.revision_id,
        revision_number=event.revision_number,
        title=event.title,
        severity=event.severity,
        timing=timing,
        published_at=event.published_at,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        source_ids=sorted(set(event.source_ids)),
        text=reported_copy,
    )


def _present_best_window(
    window: BestWindowInput | None,
    *,
    now: datetime,
) -> BriefingBestWindow | None:
    if window is None or window.end <= now or window.captured_at > now:
        return None
    start = _local_time(window.start, relative_to=now)
    end = _local_time(window.end, relative_to=now)
    return BriefingBestWindow(
        stable_id=window.stable_id,
        label=window.label,
        start=window.start,
        end=window.end,
        average_value=window.average_value,
        unit=window.unit,
        source_ids=sorted(set(window.source_ids)),
        coverage_fraction=window.coverage_fraction,
        fact_class=window.fact_class,
        methodology_version=window.methodology_version,
        captured_at=window.captured_at,
        text=(
            f"{window.label}: {start}–{end}, with an average forecast value of "
            f"{_quantity(window.average_value, window.unit)}."
        ),
    )


def _dedupe_sources(
    candidates: list[BriefingSourceStatus],
) -> list[BriefingSourceStatus]:
    selected = _dedupe(
        candidates,
        identity=lambda item: item.source_id,
        revision_key=lambda item: (
            item.revision,
            item.retrieved_at or datetime.min.replace(tzinfo=UTC),
            _SOURCE_STATE_RANK[item.state],
            item.dataset,
            item.observed_at or datetime.min.replace(tzinfo=UTC),
            item.detail or "",
        ),
    )
    return sorted(selected, key=lambda item: (item.source_id, item.dataset))


def _coverage(
    *,
    current: BriefingCurrentPosition,
    changes: list[BriefingObservedChange],
    next_moments: list[BriefingFutureMoment],
    events: list[BriefingReportedEvent],
    best_window: BriefingBestWindow | None,
    sources: list[BriefingSourceStatus],
    missing_families: list[str],
    notes: list[str],
) -> BriefingCoverage:
    sections = []
    if current.values:
        sections.append(BriefingSection.NOW)
    if changes:
        sections.append(BriefingSection.CHANGES)
    if next_moments:
        sections.append(BriefingSection.NEXT)
    if events:
        sections.append(BriefingSection.REPORTED_EVENTS)
    if best_window is not None:
        sections.append(BriefingSection.BEST_WINDOW)

    counts = Counter(source.state for source in sources)
    source_counts = {
        state: counts[state]
        for state in SourceState
        if counts[state] > 0
    }
    missing = sorted(set(value.strip() for value in missing_families if value.strip()))
    unhealthy = any(
        source.state in {
            SourceState.DELAYED,
            SourceState.STALE,
            SourceState.UNAVAILABLE,
        }
        for source in sources
    )
    all_offline = bool(sources) and all(
        source.state is SourceState.UNAVAILABLE for source in sources
    )
    if all_offline:
        status = BriefingStatus.OFFLINE
    elif (
        missing
        or unhealthy
        or current.status is CurrentPositionStatus.PARTIAL
        or (
            current.status is CurrentPositionStatus.UNAVAILABLE
            and bool(current.missing_metric_ids)
        )
    ):
        status = BriefingStatus.PARTIAL
    elif not sections:
        status = BriefingStatus.EMPTY
    elif (
        not next_moments
        and best_window is None
        and not any(event.timing is ReportedEventTiming.UPCOMING for event in events)
    ):
        status = BriefingStatus.OBSERVED_ONLY
    else:
        status = BriefingStatus.COMPLETE
    return BriefingCoverage(
        status=status,
        available_sections=sections,
        missing_families=missing,
        source_counts_by_state=source_counts,
        notes=list(dict.fromkeys(note.strip() for note in notes if note.strip())),
    )


def _display_period(now: datetime) -> DisplayPeriod:
    local = now.astimezone(_LONDON)
    hour = local.hour
    if hour < 6:
        name = DisplayPeriodName.OVERNIGHT
        start_hour, end_hour = 0, 6
    elif hour < 12:
        name = DisplayPeriodName.MORNING
        start_hour, end_hour = 6, 12
    elif hour < 18:
        name = DisplayPeriodName.AFTERNOON
        start_hour, end_hour = 12, 18
    else:
        name = DisplayPeriodName.EVENING
        start_hour, end_hour = 18, 24
    starts_at = datetime.combine(
        local.date(),
        time(hour=start_hour),
        tzinfo=_LONDON,
    )
    if end_hour == 24:
        ends_at = datetime.combine(
            local.date() + timedelta(days=1),
            time.min,
            tzinfo=_LONDON,
        )
    else:
        ends_at = datetime.combine(
            local.date(),
            time(hour=end_hour),
            tzinfo=_LONDON,
        )
    return DisplayPeriod(
        local_date=local.date(),
        name=name,
        label=f"{local.strftime('%A')} {name.value}",
        starts_at=starts_at,
        ends_at=ends_at,
    )


def _summary(
    status: BriefingStatus,
    *,
    current_count: int,
    change_count: int,
    next_count: int,
    shown_event_count: int,
    total_event_count: int,
    has_best_window: bool,
) -> str:
    if status is BriefingStatus.OFFLINE:
        context = []
        if current_count:
            context.append(_count(current_count, "validated current value"))
        if total_event_count:
            context.append(_count(total_event_count, "active or upcoming reported event"))
        if context:
            return (
                "Live sources are unavailable; the briefing retains "
                f"{_join(context)} from the supplied evidence."
            )
        return (
            "Live sources are unavailable, and no current verified briefing "
            "items are available."
        )
    if status is BriefingStatus.EMPTY:
        return (
            "No material observed changes, future moments, or active or upcoming reported "
            "events are available for this display period."
        )
    if status is BriefingStatus.PARTIAL:
        return (
            "This briefing is partial because one or more supplied sources or "
            "fact families are delayed, stale, or unavailable."
        )
    if status is BriefingStatus.OBSERVED_ONLY:
        return (
            "Observed or reported facts are available; no qualifying future "
            "moment or complete best window is included."
        )

    parts = []
    if current_count:
        parts.append(_count(current_count, "validated current value"))
    if change_count:
        parts.append(_count(change_count, "meaningful observed change"))
    if next_count:
        parts.append(_count(next_count, "future moment"))
    if total_event_count:
        event_text = _count(
            total_event_count,
            "active or upcoming reported event",
        )
        if shown_event_count < total_event_count:
            event_text = f"{shown_event_count} of {event_text}"
        parts.append(event_text)
    if has_best_window:
        parts.append("one complete forecast best window")
    return f"This briefing includes {_join(parts)}."


def _change_copy(
    change: ObservedChangeInput,
    period: ComparisonPeriod,
) -> str:
    verb = "rose" if change.delta > 0 else "fell"
    return (
        f"{change.label} {verb} by {_quantity(abs(change.delta), change.unit)} "
        f"over {period.label}."
    )


def _next_copy(moment: FutureMomentInput, *, now: datetime) -> str:
    basis = "is forecast for" if moment.fact_class is FutureFactClass.FORECAST else "is reported for"
    value = (
        f" at {_quantity(moment.value, moment.unit)}"
        if moment.value is not None and moment.unit is not None
        else ""
    )
    return (
        f"{moment.label} {basis} "
        f"{_local_time(moment.starts_at, relative_to=now)}{value}."
    )


def _current_value_text(value: BriefingCurrentValue) -> str:
    classification = (
        ""
        if value.fact_class.value == "observed"
        else f" ({value.fact_class.value})"
    )
    return f"{value.label} is {_quantity(value.value, value.unit)}{classification}"


def _event_timing(
    event: ReportedEventInput,
    *,
    now: datetime,
) -> ReportedEventTiming:
    return (
        ReportedEventTiming.UPCOMING
        if event.starts_at is not None and event.starts_at > now
        else ReportedEventTiming.ACTIVE
    )


def _local_time(value: datetime, *, relative_to: datetime) -> str:
    local = value.astimezone(_LONDON)
    reference = relative_to.astimezone(_LONDON)
    if local.date() == reference.date():
        return local.strftime("%H:%M")
    return local.strftime("%a %d %b, %H:%M")


def _quantity(value: float, unit: str) -> str:
    normalized = 0.0 if value == 0 else value
    text = f"{normalized:,.2f}".rstrip("0").rstrip(".")
    return f"{text}{unit}" if unit == "%" else f"{text} {unit}"


def _count(value: int, singular: str) -> str:
    return f"{value} {singular if value == 1 else singular + 's'}"


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _dedupe[T](
    values: list[T],
    *,
    identity,
    revision_key,
) -> list[T]:
    selected: dict[str, T] = {}
    seen_revisions: dict[tuple[str, object], T] = {}
    for value in values:
        key = identity(value)
        value_revision_key = revision_key(value)
        primary_revision = value_revision_key[0]
        duplicate_key = (key, primary_revision)
        duplicate = seen_revisions.get(duplicate_key)
        if duplicate is not None and duplicate != value:
            raise ValueError(
                f"conflicting duplicate revision {primary_revision} for {key}"
            )
        seen_revisions[duplicate_key] = value
        current = selected.get(key)
        if current is None or value_revision_key > revision_key(current):
            selected[key] = value
    return list(selected.values())
