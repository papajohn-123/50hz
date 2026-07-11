from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.game.models import (
    DailyGame,
    MissionDefinition,
    MissionKind,
    PredictionChoice,
    PredictionDefinition,
)


LONDON = ZoneInfo("Europe/London")


def build_daily_game(*, now: datetime, source_fresh: bool, has_forecast: bool, has_events: bool) -> DailyGame:
    local_now = now.astimezone(LONDON)
    day = local_now.date()
    reason = None if source_fresh else "Live data is currently stale"
    missions = [
        MissionDefinition(
            mission_id=f"{day}:clean-window",
            kind=MissionKind.FIND_CLEAN_WINDOW,
            title="Find tonight's cleanest half-hour",
            available=source_fresh and has_forecast,
            unavailable_reason=reason or (None if has_forecast else "Carbon forecast unavailable"),
        ),
        MissionDefinition(
            mission_id=f"{day}:largest-source",
            kind=MissionKind.IDENTIFY_LARGEST_SOURCE,
            title="Identify Britain's largest source",
            available=source_fresh,
            unavailable_reason=reason,
        ),
        MissionDefinition(
            mission_id=f"{day}:event-evidence",
            kind=MissionKind.OPEN_EVENT_EVIDENCE,
            title="Open the evidence for a grid event",
            available=source_fresh and has_events,
            unavailable_reason=reason or (None if has_events else "No qualifying event yet today"),
        ),
    ]

    lock_local = datetime.combine(day, time(17, 45), tzinfo=LONDON)
    resolve_local = datetime.combine(day, time(18, 0), tzinfo=LONDON)
    prediction = None
    if source_fresh and local_now < lock_local:
        prediction = PredictionDefinition(
            prediction_id=f"{day}:energy-position-1800",
            question="Will Britain be importing or exporting at 18:00?",
            choices=[PredictionChoice.IMPORTING, PredictionChoice.EXPORTING],
            locks_at=lock_local.astimezone(UTC),
            metric="net_interconnector_flow_mw",
            resolves_from=(resolve_local - timedelta(minutes=5)).astimezone(UTC),
            resolves_to=(resolve_local + timedelta(minutes=5)).astimezone(UTC),
        )

    return DailyGame(date=day.isoformat(), missions=missions, prediction=prediction, source_fresh=source_fresh)

