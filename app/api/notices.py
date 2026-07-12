"""Presentation of authoritatively reported notices as mobile grid events.

This module deliberately does not infer an outage or a cause.  REMIT entries and
system warnings remain labelled as reported facts all the way to the API.
"""

from __future__ import annotations

from app.api.models import (
    EventHistoryChangedField,
    EventHistoryEffectiveWindow,
    EventHistoryFieldChange,
    EventHistoryReportedAsset,
    EventHistoryReportedCapacity,
    EventHistoryResponse,
    EventHistoryRevision,
    GridEvent,
)
from app.events.identity import reported_notice_event_id as _reported_notice_event_id
from app.events.revisions import RevisionField
from app.persistence.reads import (
    EventLifecycleHistoryRead,
    EventLifecycleRevisionRead,
    ReportedNoticeRead,
)


_SEVERITY_RANK = {"important": 2, "notable": 1, "info": 0}
_PUBLIC_CHANGED_FIELDS = {
    RevisionField.UNAVAILABLE_MW: EventHistoryChangedField.UNAVAILABLE_MW,
    RevisionField.NORMAL_CAPACITY_MW: EventHistoryChangedField.NORMAL_CAPACITY_MW,
    RevisionField.EFFECTIVE_START: EventHistoryChangedField.EFFECTIVE_START,
    RevisionField.EFFECTIVE_END: EventHistoryChangedField.EFFECTIVE_END,
    RevisionField.STATUS: EventHistoryChangedField.STATUS,
    RevisionField.REPORTED_CAUSE: EventHistoryChangedField.REPORTED_CAUSE,
    RevisionField.EVIDENCE_CHECKSUM: EventHistoryChangedField.EVIDENCE_CHECKSUM,
    RevisionField.MATERIAL_REASON: EventHistoryChangedField.MATERIAL_REASON,
}


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


def present_event_history(history: EventLifecycleHistoryRead) -> EventHistoryResponse:
    """Present only the audited, public-safe event lifecycle projection."""

    revisions = [_present_event_history_revision(item) for item in history.revisions]
    return EventHistoryResponse(
        event_id=history.event_id,
        lifecycle_status=history.current.status,
        revision_count=history.total_revision_count,
        returned_revision_count=len(revisions),
        is_truncated=history.is_truncated,
        first_published_at=history.first_published_at,
        latest_published_at=history.latest_published_at,
        revisions=revisions,
    )


def _present_event_history_revision(
    revision: EventLifecycleRevisionRead,
) -> EventHistoryRevision:
    effective_window = (
        EventHistoryEffectiveWindow(
            start=revision.effective_start,
            end=revision.effective_end,
        )
        if revision.effective_start is not None or revision.effective_end is not None
        else None
    )
    reported_asset = (
        EventHistoryReportedAsset(
            asset_id=_public_text(revision.asset_id, maximum=200),
            name=_public_text(revision.asset_name, maximum=300),
            identity_reliable=revision.asset_identity_reliable,
        )
        if revision.asset_id is not None or revision.asset_name is not None
        else None
    )
    reported_capacity = (
        EventHistoryReportedCapacity(
            unavailable_mw=revision.unavailable_mw,
            normal_capacity_mw=revision.normal_capacity_mw,
        )
        if revision.unavailable_mw is not None or revision.normal_capacity_mw is not None
        else None
    )
    changes = [
        EventHistoryFieldChange(
            field=_PUBLIC_CHANGED_FIELDS[change.field],
            before=_public_delta_value(change.before),
            after=_public_delta_value(change.after),
        )
        for change in revision.changes
    ]
    return EventHistoryRevision(
        revision_number=revision.revision_number,
        status=revision.status,
        authority=revision.authority,
        published_at=revision.published_at,
        effective_window=effective_window,
        reported_asset=reported_asset,
        reported_capacity=reported_capacity,
        planned=revision.planned,
        reported_cause=_public_text(revision.reported_cause, maximum=1_000),
        material_reason=_public_text(revision.material_reason, maximum=500),
        superseded_by_event_id=revision.superseded_by_event_id,
        source_ids=list(revision.source_ids),
        source_record_ids=list(revision.source_record_ids),
        evidence_checksum=revision.evidence_checksum,
        changes=changes,
    )


def _public_delta_value(value):
    # Lifecycle text originates in a public publisher record. Bound it at the
    # presentation edge so one anomalous revision cannot inflate this
    # synchronous mobile response; numeric, timestamp and enum values retain
    # their exact typed representation.
    if isinstance(value, str) and len(value) > 1_000:
        return value[:999] + "…"
    return value


def _public_text(value: str | None, *, maximum: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized if len(normalized) <= maximum else normalized[: maximum - 1] + "…"


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
