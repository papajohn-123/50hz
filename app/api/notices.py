"""Presentation of authoritatively reported notices as mobile grid events.

This module deliberately does not infer an outage or a cause.  REMIT entries and
system warnings remain labelled as reported facts all the way to the API.
"""

from __future__ import annotations

from app.api.models import GridEvent
from app.events.identity import reported_notice_event_id as _reported_notice_event_id
from app.persistence.reads import ReportedNoticeRead


_SEVERITY_RANK = {"important": 2, "notable": 1, "info": 0}


def reported_notice_event_id(notice: ReportedNoticeRead) -> str:
    """Return a revision-independent event ID for one source notice identity."""

    return _reported_notice_event_id(
        source_id=notice.source_id,
        notice_kind=notice.notice_kind,
        external_id=notice.external_id,
    )


def reported_notice_to_grid_event(notice: ReportedNoticeRead) -> GridEvent:
    """Map a normalized notice without upgrading reported facts to observed ones."""

    if notice.notice_kind == "system_warning":
        title = _text(notice.warning_type) or "System warning"
        summary = _text(notice.warning_text) or "NESO published a system warning."
        severity = "important"
    else:
        subject = _text(notice.affected_unit) or _text(notice.asset_id) or "Generating unit"
        heading = _text(notice.heading)
        title = (
            f"{subject}: reported unavailability"
            if _is_generic_remit_heading(heading)
            else heading or f"{subject}: reported unavailability"
        )
        severity = _remit_severity(notice.unavailable_capacity_mw)
        if (
            notice.unavailable_capacity_mw is not None
            and notice.unavailable_capacity_mw > 0
        ):
            capacity = _format_megawatts(notice.unavailable_capacity_mw)
            summary = f"{subject} has a reported unavailability of {capacity} MW."
        elif notice.unavailable_capacity_mw is not None:
            summary = (
                f"{subject} has a reported unavailability notice. "
                "Its capacity fields do not state a positive unavailable amount."
            )
        else:
            summary = f"{subject} has a reported unavailability."
        if cause := _text(notice.reported_cause):
            summary += f" Reported cause: {cause}"

    return GridEvent(
        id=reported_notice_event_id(notice),
        title=title,
        summary=summary,
        severity=severity,
        evidence_class="reported",
        started_at=notice.event_start or notice.published_at,
        source_ids=[notice.source_id],
        is_authoritatively_reported=True,
    )


def present_reported_notices(notices: tuple[ReportedNoticeRead, ...]) -> list[GridEvent]:
    events = [reported_notice_to_grid_event(notice) for notice in notices]
    return sorted(
        events,
        key=lambda event: (
            -_SEVERITY_RANK.get(event.severity, 0),
            -event.started_at.timestamp(),
            event.id,
        ),
    )


def _remit_severity(unavailable_capacity_mw: float | None) -> str:
    if unavailable_capacity_mw is None:
        return "notable"
    if unavailable_capacity_mw <= 0:
        return "info"
    if unavailable_capacity_mw >= 500:
        return "important"
    if unavailable_capacity_mw >= 100:
        return "notable"
    return "info"


def _format_megawatts(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def _text(value: str | None) -> str | None:
    stripped = value.strip() if value else ""
    return stripped or None


def _is_generic_remit_heading(value: str | None) -> bool:
    if value is None:
        return True
    normalized = " ".join(value.casefold().replace("-", " ").split())
    return normalized in {
        "remit",
        "remit information",
        "unavailability",
        "unavailability information",
    }
