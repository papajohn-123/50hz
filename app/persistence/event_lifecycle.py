"""Materialize immutable reported-notice lifecycle revisions.

The source notice remains the authoritative evidence record.  This module
projects that evidence into the deterministic lifecycle contract used for
ranking and later event history presentation.  It does not infer causes or
derive source status from coincident grid observations.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EventLifecycleDelta, EventLifecycleRevision
from app.events.identity import reported_notice_event_id
from app.events.models import EventStatus
from app.events.revisions import (
    DELTA_MODEL_VERSION,
    REVISION_MODEL_VERSION,
    EventAuthority,
    EventRevisionDelta,
    ReportedEventRevision,
    diff_revisions,
)
from app.sources.types import as_utc


_REVISION_NAMESPACE = uuid.UUID("fa64dc38-c711-5aba-8e96-e437637101ba")
_DELTA_NAMESPACE = uuid.UUID("73f4e9f4-f423-5f2b-9445-f18c47ce5940")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_TERMINAL_STATUSES = {
    EventStatus.RESOLVED,
    EventStatus.SUPERSEDED,
    EventStatus.WITHDRAWN,
}
_WITHDRAWN_SOURCE_STATUSES = {
    "cancelled",
    "canceled",
    "dismissed",
    "inactive",
    "withdrawn",
}
_RESOLVED_SOURCE_STATUSES = {
    "closed",
    "complete",
    "completed",
    "ended",
    "resolved",
}
_SUPERSEDED_SOURCE_STATUSES = {"replaced", "superseded"}


@dataclass(frozen=True, slots=True)
class EventLifecycleMaterializationOutcome:
    revisions: int = 0
    deltas: int = 0
    unchanged: int = 0
    skipped: int = 0


class _LifecycleEvidenceGap(ValueError):
    """The source publication cannot extend the audited lifecycle safely."""


async def materialize_reported_notice_rows(
    session: AsyncSession,
    rows: Sequence[Mapping[str, Any]],
) -> EventLifecycleMaterializationOutcome:
    """Append unseen notice evidence to the immutable lifecycle ledger.

    The caller owns the transaction.  Ingestion invokes this only after the
    normalized ``reported_notices`` rows have been written, so source evidence,
    lifecycle state and its delta commit or roll back together.
    """

    if not rows:
        return EventLifecycleMaterializationOutcome()

    revisions_written = 0
    deltas_written = 0
    unchanged = 0
    skipped = 0
    grouped: dict[
        str,
        list[tuple[tuple[Any, ...], str, Mapping[str, Any]]],
    ] = defaultdict(list)
    for row in rows:
        try:
            event_id = _event_id(row)
            checksum = _checksum(row)
            order = _source_order(row)
        except (KeyError, TypeError, ValueError):
            # The normalized source row remains authoritative and commits with
            # the ingestion transaction. One row that cannot satisfy the
            # stricter lifecycle projection must not roll back its entire
            # publication window.
            skipped += 1
            continue
        grouped[event_id].append((order, checksum, row))

    if not grouped:
        return EventLifecycleMaterializationOutcome(skipped=skipped)

    existing_result = await session.execute(
        select(EventLifecycleRevision)
        .where(EventLifecycleRevision.event_id.in_(tuple(sorted(grouped))))
        .order_by(
            EventLifecycleRevision.event_id,
            EventLifecycleRevision.revision_number,
        )
    )
    existing_rows = tuple(existing_result.scalars().all())
    histories: dict[str, list[ReportedEventRevision]] = defaultdict(list)
    seen_checksums: dict[str, set[str]] = defaultdict(set)
    latest_source_order: dict[str, tuple[Any, ...]] = {}
    for stored in existing_rows:
        revision = _domain_revision(stored)
        histories[stored.event_id].append(revision)
        seen_checksums[stored.event_id].add(revision.evidence_checksum)
        order = _stored_source_order(stored.payload)
        if order is not None:
            latest_source_order[stored.event_id] = max(
                order,
                latest_source_order.get(stored.event_id, order),
            )

    for event_id in sorted(grouped):
        notices = sorted(grouped[event_id], key=lambda item: item[0])
        history = histories[event_id]
        for order, checksum, notice in notices:
            if checksum in seen_checksums[event_id]:
                unchanged += 1
                continue

            if order < latest_source_order.get(event_id, order):
                # An older, previously unseen publication cannot be inserted
                # into an already immutable sequence. Its normalized source
                # record remains retained for audit/backfill.
                skipped += 1
                continue
            previous = history[-1] if history else None
            if previous is not None and previous.status in _TERMINAL_STATUSES:
                skipped += 1
                continue
            if (
                previous is not None
                and _aware_time(notice, "published_at") < previous.published_at
            ):
                skipped += 1
                continue
            try:
                revision = build_reported_notice_revision(
                    notice,
                    revision_number=len(history) + 1,
                    previous=previous,
                )
                delta = diff_revisions(previous, revision) if previous else None
            except ValueError:
                # Keep source ingestion available when an isolated notice is
                # malformed or lacks the evidence required by the stricter
                # lifecycle contract. SQL/database failures still abort.
                skipped += 1
                continue

            revision_id = _revision_id(event_id, revision.revision)
            await session.execute(
                _revision_insert(
                    revision_id=revision_id,
                    revision=revision,
                    notice=notice,
                )
            )
            revisions_written += 1
            if delta is not None:
                await session.execute(
                    _delta_insert(
                        revision_id=revision_id,
                        delta=delta,
                    )
                )
                deltas_written += 1

            history.append(revision)
            seen_checksums[event_id].add(checksum)
            latest_source_order[event_id] = order

    return EventLifecycleMaterializationOutcome(
        revisions=revisions_written,
        deltas=deltas_written,
        unchanged=unchanged,
        skipped=skipped,
    )


def build_reported_notice_revision(
    notice: Mapping[str, Any],
    *,
    revision_number: int,
    previous: ReportedEventRevision | None = None,
) -> ReportedEventRevision:
    """Map one normalized source publication without adding inferred facts."""

    if revision_number < 1:
        raise ValueError("revision_number must be positive")
    if (previous is None) != (revision_number == 1):
        raise ValueError("revision_number must continue the supplied lifecycle")
    event_id = _event_id(notice)
    if previous is not None:
        if previous.event_id != event_id:
            raise ValueError("notice does not belong to the supplied lifecycle")
        if previous.revision + 1 != revision_number:
            raise ValueError("revision_number must be sequential")

    status, superseded_by = _lifecycle_status(notice, previous=previous)
    if previous is None and status is not EventStatus.OPEN:
        raise _LifecycleEvidenceGap(
            "a terminal notice requires an earlier open publication"
        )

    notice_kind = _required_text(notice, "notice_kind")
    asset_id = _text(notice.get("asset_id")) or _text(
        notice.get("affected_unit_eic")
    )
    return ReportedEventRevision(
        event_id=event_id,
        revision=revision_number,
        status=status,
        published_at=_aware_time(notice, "published_at"),
        effective_start=_optional_aware_time(notice, "event_start"),
        effective_end=_optional_aware_time(notice, "event_end"),
        authority=(
            EventAuthority.SYSTEM_WARNING
            if notice_kind == "system_warning"
            else EventAuthority.AUTHORITATIVE_NOTICE
        ),
        asset_id=asset_id,
        asset_name=_text(notice.get("affected_unit")),
        asset_identity_reliable=asset_id is not None,
        unavailable_mw=_optional_nonnegative_number(
            notice,
            "unavailable_capacity_mw",
        ),
        normal_capacity_mw=_optional_positive_number(
            notice,
            "normal_capacity_mw",
        ),
        planned=_reported_planning_state(notice.get("unavailability_type")),
        reported_cause=_text(notice.get("reported_cause")),
        evidence_checksum=_checksum(notice),
        material_reason=(
            _material_reason(previous, notice, status)
            if previous is not None
            else None
        ),
        superseded_by_event_id=superseded_by,
        source_record_ids=(_required_text(notice, "source_record_id"),),
    )


def _revision_insert(
    *,
    revision_id: uuid.UUID,
    revision: ReportedEventRevision,
    notice: Mapping[str, Any],
):
    statement = pg_insert(EventLifecycleRevision).values(
        id=revision_id,
        event_id=revision.event_id,
        event_kind="reported",
        revision_number=revision.revision,
        status=revision.status,
        authority=revision.authority.value,
        evidence_class="reported",
        published_at=revision.published_at,
        effective_start=revision.effective_start,
        effective_end=revision.effective_end,
        asset_id=revision.asset_id,
        asset_name=revision.asset_name,
        asset_identity_reliable=revision.asset_identity_reliable,
        unavailable_mw=revision.unavailable_mw,
        normal_capacity_mw=revision.normal_capacity_mw,
        planned=revision.planned,
        reported_cause=revision.reported_cause,
        evidence_checksum=revision.evidence_checksum,
        material_reason=revision.material_reason,
        superseded_by_event_id=revision.superseded_by_event_id,
        source_ids=[_required_text(notice, "source_id")],
        source_record_ids=list(revision.source_record_ids),
        model_version=REVISION_MODEL_VERSION,
        payload={
            "revision": revision.model_dump(mode="json"),
            "sourceNotice": _source_notice_payload(notice),
        },
    )
    return statement.on_conflict_do_nothing(
        index_elements=[
            EventLifecycleRevision.event_id,
            EventLifecycleRevision.revision_number,
        ]
    )


def _delta_insert(
    *,
    revision_id: uuid.UUID,
    delta: EventRevisionDelta,
):
    statement = pg_insert(EventLifecycleDelta).values(
        id=uuid.uuid5(
            _DELTA_NAMESPACE,
            f"{delta.event_id}:{delta.from_revision}:{delta.to_revision}",
        ),
        event_revision_id=revision_id,
        event_id=delta.event_id,
        from_revision=delta.from_revision,
        to_revision=delta.to_revision,
        model_version=DELTA_MODEL_VERSION,
        changes=delta.model_dump(mode="json")["changes"],
    )
    return statement.on_conflict_do_nothing(
        index_elements=[
            EventLifecycleDelta.event_id,
            EventLifecycleDelta.from_revision,
            EventLifecycleDelta.to_revision,
        ]
    )


def _domain_revision(row: EventLifecycleRevision) -> ReportedEventRevision:
    payload = row.payload.get("revision", row.payload)
    return ReportedEventRevision.model_validate(payload)


def _event_id(notice: Mapping[str, Any]) -> str:
    return reported_notice_event_id(
        source_id=_required_text(notice, "source_id"),
        notice_kind=_required_text(notice, "notice_kind"),
        external_id=_required_text(notice, "external_id"),
    )


def _revision_id(event_id: str, revision_number: int) -> uuid.UUID:
    return uuid.uuid5(_REVISION_NAMESPACE, f"{event_id}:{revision_number}")


def _lifecycle_status(
    notice: Mapping[str, Any],
    *,
    previous: ReportedEventRevision | None,
) -> tuple[EventStatus, str | None]:
    normalized = _normalized_status(notice.get("event_status"))
    if normalized in _WITHDRAWN_SOURCE_STATUSES:
        return EventStatus.WITHDRAWN, None
    if normalized in _RESOLVED_SOURCE_STATUSES:
        return EventStatus.RESOLVED, None
    if normalized in _SUPERSEDED_SOURCE_STATUSES:
        replacement = _reported_replacement_event_id(notice)
        if replacement is None:
            raise _LifecycleEvidenceGap(
                "superseded notice requires a reported replacement"
            )
        return EventStatus.SUPERSEDED, replacement
    return (EventStatus.UPDATED if previous is not None else EventStatus.OPEN), None


def _reported_replacement_event_id(notice: Mapping[str, Any]) -> str | None:
    evidence = notice.get("evidence")
    if not isinstance(evidence, Mapping):
        return None
    explicit_event_id = _text(evidence.get("supersededByEventId"))
    if explicit_event_id is not None:
        return explicit_event_id
    replacement_external_id = _text(evidence.get("supersededByExternalId"))
    if replacement_external_id is None:
        return None
    return reported_notice_event_id(
        source_id=_required_text(notice, "source_id"),
        notice_kind=_required_text(notice, "notice_kind"),
        external_id=replacement_external_id,
    )


def _material_reason(
    previous: ReportedEventRevision,
    notice: Mapping[str, Any],
    status: EventStatus,
) -> str:
    if status in _TERMINAL_STATUSES:
        return f"Source marked the notice {status.value}"

    changed: list[str] = []
    comparisons = (
        (
            previous.unavailable_mw,
            notice.get("unavailable_capacity_mw"),
            "capacity",
        ),
        (
            previous.normal_capacity_mw,
            notice.get("normal_capacity_mw"),
            "normal capacity",
        ),
        (previous.effective_start, notice.get("event_start"), "start time"),
        (previous.effective_end, notice.get("event_end"), "end time"),
        (
            previous.reported_cause,
            _text(notice.get("reported_cause")),
            "reported cause",
        ),
        (
            previous.planned,
            _reported_planning_state(notice.get("unavailability_type")),
            "planning classification",
        ),
    )
    for before, after, label in comparisons:
        if before != after:
            changed.append(label)
    if not changed:
        return "Source published a corrected notice revision"
    return f"Source revised {', '.join(changed)}"


def _source_notice_payload(notice: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sourceId": _required_text(notice, "source_id"),
        "sourceRecordId": _required_text(notice, "source_record_id"),
        "noticeKind": _required_text(notice, "notice_kind"),
        "externalId": _required_text(notice, "external_id"),
        "revisionKey": _required_text(notice, "revision_key"),
        "sourceRevisionNumber": notice.get("revision_number"),
        "sourceEventStatus": _text(notice.get("event_status")),
        "sourceRetrievedAt": _aware_time(notice, "retrieved_at").isoformat(),
        "unavailabilityType": _text(notice.get("unavailability_type")),
        "affectedUnitEIC": _text(notice.get("affected_unit_eic")),
        "availableCapacityMW": notice.get("available_capacity_mw"),
        "heading": _text(notice.get("heading")),
        "warningType": _text(notice.get("warning_type")),
        "warningText": _text(notice.get("warning_text")),
        "reportedEvidence": notice.get("evidence", {}),
    }


def _source_order(notice: Mapping[str, Any]) -> tuple[Any, ...]:
    source_revision = notice.get("revision_number")
    published_at = _aware_time(notice, "published_at")
    retrieved_at = _aware_time(notice, "retrieved_at")
    revision_key = _required_text(notice, "revision_key")
    checksum = _checksum(notice)
    if isinstance(source_revision, int):
        return (0, source_revision, published_at, retrieved_at, revision_key, checksum)
    return (1, 0, published_at, retrieved_at, revision_key, checksum)


def _stored_source_order(payload: Mapping[str, Any]) -> tuple[Any, ...] | None:
    source = payload.get("sourceNotice")
    revision_payload = payload.get("revision")
    if not isinstance(source, Mapping) or not isinstance(revision_payload, Mapping):
        return None
    try:
        published_at = datetime.fromisoformat(str(revision_payload["published_at"]))
        retrieved_at = datetime.fromisoformat(str(source["sourceRetrievedAt"]))
        source_revision = source.get("sourceRevisionNumber")
        prefix = (0, source_revision) if isinstance(source_revision, int) else (1, 0)
        return (
            *prefix,
            published_at,
            retrieved_at,
            str(source["revisionKey"]),
            str(revision_payload["evidence_checksum"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _reported_planning_state(value: Any) -> bool | None:
    normalized = _normalized_status(value)
    if normalized == "planned":
        return True
    if normalized == "unplanned":
        return False
    return None


def _normalized_status(value: Any) -> str:
    text = _text(value)
    if text is None:
        return ""
    return "_".join(text.casefold().replace("-", " ").split())


def _checksum(notice: Mapping[str, Any]) -> str:
    value = _required_text(notice, "content_sha256").casefold()
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("content_sha256 must be a SHA-256 hex digest")
    return value


def _required_text(notice: Mapping[str, Any], field: str) -> str:
    value = _text(notice.get(field))
    if value is None:
        raise ValueError(f"{field} cannot be blank")
    return value


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _aware_time(notice: Mapping[str, Any], field: str) -> datetime:
    value = notice.get(field)
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    return as_utc(value, field_name=field)


def _optional_aware_time(
    notice: Mapping[str, Any],
    field: str,
) -> datetime | None:
    return _aware_time(notice, field) if notice.get(field) is not None else None


def _optional_nonnegative_number(
    notice: Mapping[str, Any],
    field: str,
) -> float | None:
    value = notice.get(field)
    if value is None:
        return None
    number = float(value)
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def _optional_positive_number(
    notice: Mapping[str, Any],
    field: str,
) -> float | None:
    value = notice.get(field)
    if value is None:
        return None
    number = float(value)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number
