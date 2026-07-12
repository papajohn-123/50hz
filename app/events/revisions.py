from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.events.models import EventStatus


REVISION_MODEL_VERSION = "50hz.reported-event-revision.v1"
DELTA_MODEL_VERSION = "50hz.reported-event-delta.v1"


class EventAuthority(StrEnum):
    SYSTEM_WARNING = "system_warning"
    AUTHORITATIVE_NOTICE = "authoritative_notice"
    OTHER_REPORTED = "other_reported"


class RevisionField(StrEnum):
    UNAVAILABLE_MW = "unavailable_mw"
    NORMAL_CAPACITY_MW = "normal_capacity_mw"
    EFFECTIVE_START = "effective_start"
    EFFECTIVE_END = "effective_end"
    STATUS = "status"
    REPORTED_CAUSE = "reported_cause"
    EVIDENCE_CHECKSUM = "evidence_checksum"
    MATERIAL_REASON = "material_reason"


DeltaValue = float | datetime | EventStatus | str | None


class ReportedEventRevision(BaseModel):
    """Immutable source-reported state at one revision.

    Nullable fields remain unknown.  They must never be filled from coincident
    grid observations or inferred by the relevance scorer.
    """

    model_config = ConfigDict(frozen=True)

    model_version: Literal["50hz.reported-event-revision.v1"] = (
        REVISION_MODEL_VERSION
    )
    event_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: EventStatus
    published_at: AwareDatetime
    effective_start: AwareDatetime | None = None
    effective_end: AwareDatetime | None = None
    authority: EventAuthority
    asset_id: str | None = None
    asset_name: str | None = None
    asset_identity_reliable: bool = False
    unavailable_mw: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    normal_capacity_mw: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
    planned: bool | None = None
    reported_cause: str | None = Field(default=None, min_length=1)
    evidence_checksum: str = Field(min_length=1)
    material_reason: str | None = Field(default=None, min_length=1)
    superseded_by_event_id: str | None = Field(default=None, min_length=1)
    source_record_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_reported_state(self) -> "ReportedEventRevision":
        if (
            self.effective_start is not None
            and self.effective_end is not None
            and self.effective_end < self.effective_start
        ):
            raise ValueError("effective_end cannot precede effective_start")
        if self.asset_identity_reliable and not self.asset_id:
            raise ValueError("reliable asset identity requires asset_id")
        if self.revision == 1 and self.status != EventStatus.OPEN:
            raise ValueError("the first revision must be open")
        if self.revision > 1 and self.status == EventStatus.OPEN:
            raise ValueError("later revisions cannot return to open")
        if self.revision > 1 and self.material_reason is None:
            raise ValueError("later revisions require a material reason")
        if self.status == EventStatus.SUPERSEDED:
            if self.superseded_by_event_id is None:
                raise ValueError("superseded status requires superseded_by_event_id")
        elif self.superseded_by_event_id is not None:
            raise ValueError("superseded_by_event_id is only valid when superseded")
        return self


class RevisionFieldDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: RevisionField
    before: DeltaValue
    after: DeltaValue


class EventRevisionDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_version: Literal["50hz.reported-event-delta.v1"] = DELTA_MODEL_VERSION
    event_id: str
    from_revision: int = Field(ge=1)
    to_revision: int = Field(ge=2)
    changes: tuple[RevisionFieldDelta, ...] = Field(min_length=1)

    @property
    def changed_fields(self) -> tuple[RevisionField, ...]:
        return tuple(change.field for change in self.changes)


class EventLifecycleHistory(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(min_length=1)
    revisions: tuple[ReportedEventRevision, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_revision_chain(self) -> "EventLifecycleHistory":
        if any(revision.event_id != self.event_id for revision in self.revisions):
            raise ValueError("all revisions must belong to the lifecycle event")
        if self.revisions[0].revision != 1:
            raise ValueError("a lifecycle must begin at revision one")
        for previous, current in zip(self.revisions, self.revisions[1:]):
            diff_revisions(previous, current)
        return self

    @property
    def current(self) -> ReportedEventRevision:
        return self.revisions[-1]


class RevisionAppendResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    lifecycle: EventLifecycleHistory
    delta: EventRevisionDelta


def diff_revisions(
    previous: ReportedEventRevision,
    current: ReportedEventRevision,
) -> EventRevisionDelta:
    if previous.event_id != current.event_id:
        raise ValueError("revisions must belong to the same event")
    if current.revision != previous.revision + 1:
        raise ValueError("revisions must be sequential")
    if current.published_at < previous.published_at:
        raise ValueError("revision publication time cannot move backwards")
    _validate_status_transition(previous.status, current.status)

    fields: tuple[tuple[RevisionField, str], ...] = (
        (RevisionField.UNAVAILABLE_MW, "unavailable_mw"),
        (RevisionField.NORMAL_CAPACITY_MW, "normal_capacity_mw"),
        (RevisionField.EFFECTIVE_START, "effective_start"),
        (RevisionField.EFFECTIVE_END, "effective_end"),
        (RevisionField.STATUS, "status"),
        (RevisionField.REPORTED_CAUSE, "reported_cause"),
        (RevisionField.EVIDENCE_CHECKSUM, "evidence_checksum"),
        (RevisionField.MATERIAL_REASON, "material_reason"),
    )
    changes = tuple(
        RevisionFieldDelta(
            field=field,
            before=_delta_value(getattr(previous, attribute)),
            after=_delta_value(getattr(current, attribute)),
        )
        for field, attribute in fields
        if getattr(previous, attribute) != getattr(current, attribute)
    )
    return EventRevisionDelta(
        event_id=current.event_id,
        from_revision=previous.revision,
        to_revision=current.revision,
        changes=changes,
    )


def append_revision(
    lifecycle: EventLifecycleHistory,
    revision: ReportedEventRevision,
) -> RevisionAppendResult:
    delta = diff_revisions(lifecycle.current, revision)
    updated = EventLifecycleHistory(
        event_id=lifecycle.event_id,
        revisions=(*lifecycle.revisions, revision),
    )
    return RevisionAppendResult(lifecycle=updated, delta=delta)


def _validate_status_transition(previous: EventStatus, current: EventStatus) -> None:
    terminal = {
        EventStatus.RESOLVED,
        EventStatus.SUPERSEDED,
        EventStatus.WITHDRAWN,
    }
    if previous in terminal:
        raise ValueError("terminal event states cannot be revised")
    allowed = {
        EventStatus.UPDATED,
        EventStatus.RESOLVED,
        EventStatus.SUPERSEDED,
        EventStatus.WITHDRAWN,
    }
    if current not in allowed:
        raise ValueError(f"invalid event status transition: {previous} -> {current}")


def _delta_value(value: Any) -> DeltaValue:
    if isinstance(value, (float, datetime, EventStatus, str)) or value is None:
        return value
    raise TypeError(f"unsupported revision delta type: {type(value).__name__}")
