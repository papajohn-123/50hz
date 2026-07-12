"""Cutoff-safe normalized evidence loading and detected-event persistence."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DetectedEvent,
    FrequencyObservation,
    GenerationObservation,
    InterconnectorObservation,
)
from app.domain.enums import (
    EventSeverity,
    EventStatus,
    EvidenceConfidence,
)
from app.events.models import Confidence, Severity
from app.events.observed import (
    EvidenceComponentKind,
    ObservedEventDraft,
    ObservedEventEvaluation,
    ObservedEvidenceBatch,
    ObservedEvidenceComponent,
    RULE_VERSION_PREFIX,
)
from app.events.processor import GridObservationWindow
from app.game.connectors import connector_registry_for_date


SessionFactory = Callable[[], AsyncSession]

_EVENT_NAMESPACE = uuid.UUID("595e863f-510f-5402-9080-b13261e77560")
_GENERATION_SOURCE_ID = "elexon.fuelinst"
# Generation and interconnector facts are two projections of the same official
# FUELINST dataset and therefore share its dataset-level provenance key.
_INTERCONNECTOR_SOURCE_ID = "elexon.fuelinst"
_FREQUENCY_SOURCE_ID = "elexon.freq"
_LOOKBACK = timedelta(minutes=45)
_MAX_ROWS_PER_FAMILY = 512
_MAX_DRAFTS_PER_EVALUATION = 4
_MAX_RESOLUTION_SCOPES_PER_EVALUATION = 4
_MAX_LIFECYCLE_TRANSITIONS_PER_SCOPE = 256
_MIN_GENERATION_SERIES = 4
_MAX_GENERATION_AGE = timedelta(minutes=20)
_MAX_INTERCONNECTOR_AGE = timedelta(minutes=20)
_MAX_FREQUENCY_AGE = timedelta(minutes=5)
_MAX_SNAPSHOT_GAP = timedelta(minutes=10)
_REVERSAL_MAGNITUDE_MW = 100.0

_EXPIRY_BY_EVENT_TYPE = {
    "frequency_excursion": timedelta(minutes=10),
    "renewable_share_milestone": timedelta(minutes=20),
    "generation_leader_change": timedelta(minutes=30),
    "energy_position_reversal": timedelta(minutes=30),
}

_SEVERITY = {
    Severity.INFO: EventSeverity.INFO,
    Severity.NOTABLE: EventSeverity.NOTABLE,
    Severity.IMPORTANT: EventSeverity.MATERIAL,
}
_CONFIDENCE = {
    Confidence.LOW: EvidenceConfidence.LOW,
    Confidence.MEDIUM: EvidenceConfidence.MEDIUM,
    Confidence.HIGH: EvidenceConfidence.HIGH,
}


@dataclass(frozen=True, slots=True)
class ObservedEventPersistenceOutcome:
    inserted: int = 0
    revised: int = 0
    unchanged: int = 0
    resolved: int = 0
    expired: int = 0


class PostgresObservedEvidenceLoader:
    """Load three small immutable-revision windows at a single wall-clock cutoff."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        lookback: timedelta = _LOOKBACK,
    ) -> None:
        if lookback <= timedelta(0) or lookback > timedelta(hours=2):
            raise ValueError("observed-event lookback must be in (0, 2 hours]")
        self._session_factory = session_factory
        self._lookback = lookback

    async def load(self, *, cutoff_at: datetime) -> ObservedEvidenceBatch:
        cutoff = _aware_utc(cutoff_at, "cutoff_at")
        window_start = cutoff - self._lookback
        async with self._session_factory() as session:
            generation_result = await session.execute(
                _latest_rows_statement(
                    GenerationObservation,
                    source_id=_GENERATION_SOURCE_ID,
                    cutoff_at=cutoff,
                    window_start=window_start,
                    identity_fields=("source_id", "series_key", "observed_at"),
                    value_fields=("series_key", "fuel_type", "generation_mw"),
                )
            )
            interconnector_result = await session.execute(
                _latest_rows_statement(
                    InterconnectorObservation,
                    source_id=_INTERCONNECTOR_SOURCE_ID,
                    cutoff_at=cutoff,
                    window_start=window_start,
                    identity_fields=(
                        "source_id",
                        "connector_code",
                        "observed_at",
                    ),
                    value_fields=("connector_code", "counterparty", "flow_mw"),
                )
            )
            frequency_result = await session.execute(
                _latest_rows_statement(
                    FrequencyObservation,
                    source_id=_FREQUENCY_SOURCE_ID,
                    cutoff_at=cutoff,
                    window_start=window_start,
                    identity_fields=("source_id", "series_key", "observed_at"),
                    value_fields=("series_key", "frequency_hz"),
                )
            )

        generation_rows = _latest_visible_rows(
            generation_result.mappings().all(),
            identity_fields=("source_id", "series_key", "observed_at"),
            cutoff_at=cutoff,
            window_start=window_start,
        )
        interconnector_rows = _latest_visible_rows(
            interconnector_result.mappings().all(),
            identity_fields=("source_id", "connector_code", "observed_at"),
            cutoff_at=cutoff,
            window_start=window_start,
        )
        frequency_rows = _latest_visible_rows(
            frequency_result.mappings().all(),
            identity_fields=("source_id", "series_key", "observed_at"),
            cutoff_at=cutoff,
            window_start=window_start,
        )

        components = tuple(
            component
            for component in (
                _generation_component(generation_rows, cutoff_at=cutoff),
                _interconnector_component(interconnector_rows, cutoff_at=cutoff),
                _frequency_component(frequency_rows, cutoff_at=cutoff),
            )
            if component is not None
        )
        return ObservedEvidenceBatch(cutoff_at=cutoff, components=components)


class PostgresObservedEventRepository:
    """Apply a bounded deterministic evaluation under the worker action lock."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def apply(
        self,
        evaluation: ObservedEventEvaluation,
    ) -> ObservedEventPersistenceOutcome:
        if len(evaluation.drafts) > _MAX_DRAFTS_PER_EVALUATION:
            raise ValueError("observed-event evaluation contains too many drafts")
        if len(evaluation.stateful_scopes) > _MAX_RESOLUTION_SCOPES_PER_EVALUATION:
            raise ValueError("observed-event evaluation contains too many scopes")
        inserted = 0
        revised = 0
        unchanged = 0
        resolved = 0
        keys = tuple(sorted({draft.candidate.dedupe_key for draft in evaluation.drafts}))

        async with self._session_factory() as session:
            async with session.begin():
                existing_by_key: dict[str, DetectedEvent] = {}
                if keys:
                    existing_result = await session.execute(
                        select(DetectedEvent)
                        .where(DetectedEvent.deterministic_key.in_(keys))
                        .with_for_update()
                    )
                    existing_by_key = {
                        row.deterministic_key: row
                        for row in existing_result.scalars().all()
                    }

                for draft in evaluation.drafts:
                    existing = existing_by_key.get(draft.candidate.dedupe_key)
                    if existing is None:
                        insert_result = await session.execute(
                            _insert_event_statement(
                                draft,
                                detected_at=evaluation.cutoff_at,
                            )
                        )
                        if insert_result.scalar_one_or_none() is None:
                            unchanged += 1
                        else:
                            inserted += 1
                        continue
                    if existing.evidence_checksum == draft.evidence_checksum:
                        unchanged += 1
                        continue
                    if draft.candidate.occurred_at < existing.last_observed_at:
                        # A replay at an older cutoff must never regress newer evidence.
                        unchanged += 1
                        continue
                    update_result = await session.execute(
                        update(DetectedEvent)
                        .where(DetectedEvent.id == existing.id)
                        .values(
                            event_type=draft.candidate.event_type,
                            status=EventStatus.UPDATED,
                            severity=_SEVERITY[draft.candidate.severity],
                            confidence=_CONFIDENCE[draft.candidate.confidence],
                            title=draft.title,
                            deterministic_summary=draft.summary,
                            rule_version=draft.rule_version,
                            evidence_version=existing.evidence_version + 1,
                            evidence_checksum=draft.evidence_checksum,
                            evidence=draft.evidence,
                            source_ids=list(draft.source_ids),
                            last_observed_at=draft.candidate.occurred_at,
                            resolved_at=None,
                            updated_at=evaluation.cutoff_at,
                        )
                    )
                    revised += max(0, update_result.rowcount or 0)

                for scope in evaluation.stateful_scopes:
                    conditions = [
                        DetectedEvent.event_type == scope.event_type,
                        DetectedEvent.rule_version.like(f"{RULE_VERSION_PREFIX}%"),
                        DetectedEvent.status.in_((EventStatus.OPEN, EventStatus.UPDATED)),
                        (
                            DetectedEvent.last_observed_at == scope.observed_at
                            if scope.same_timestamp_only
                            else DetectedEvent.last_observed_at <= scope.observed_at
                        ),
                    ]
                    if scope.active_deterministic_keys:
                        conditions.append(
                            DetectedEvent.deterministic_key.not_in(
                                scope.active_deterministic_keys
                            )
                        )
                    resolved += await _resolve_matching_events(
                        session,
                        conditions=tuple(conditions),
                        resolved_at=evaluation.cutoff_at,
                    )

        return ObservedEventPersistenceOutcome(
            inserted=inserted,
            revised=revised,
            unchanged=unchanged,
            resolved=resolved,
        )

    async def expire(self, *, as_of: datetime) -> ObservedEventPersistenceOutcome:
        cutoff = _aware_utc(as_of, "as_of")
        expiry_conditions = tuple(
            and_(
                DetectedEvent.event_type == event_type,
                DetectedEvent.last_observed_at < cutoff - lifetime,
            )
            for event_type, lifetime in _EXPIRY_BY_EVENT_TYPE.items()
        )
        async with self._session_factory() as session:
            async with session.begin():
                expired = await _resolve_matching_events(
                    session,
                    conditions=(
                        DetectedEvent.rule_version.like(f"{RULE_VERSION_PREFIX}%"),
                        DetectedEvent.status.in_((EventStatus.OPEN, EventStatus.UPDATED)),
                        or_(*expiry_conditions),
                    ),
                    resolved_at=cutoff,
                )
        return ObservedEventPersistenceOutcome(expired=expired)


async def _resolve_matching_events(
    session: AsyncSession,
    *,
    conditions: tuple[Any, ...],
    resolved_at: datetime,
) -> int:
    """Lock and transition one hard-bounded batch without an update per row."""

    ids_result = await session.execute(
        select(DetectedEvent.id)
        .where(*conditions)
        .order_by(DetectedEvent.last_observed_at, DetectedEvent.id)
        .limit(_MAX_LIFECYCLE_TRANSITIONS_PER_SCOPE)
        .with_for_update()
    )
    event_ids = tuple(ids_result.scalars().all())
    if not event_ids:
        return 0
    update_result = await session.execute(
        update(DetectedEvent)
        .where(DetectedEvent.id.in_(event_ids))
        .values(
            status=EventStatus.RESOLVED,
            resolved_at=resolved_at,
            updated_at=resolved_at,
        )
    )
    return max(0, update_result.rowcount or 0)


def _latest_rows_statement(
    model: type,
    *,
    source_id: str,
    cutoff_at: datetime,
    window_start: datetime,
    identity_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
):
    """Rank immutable corrections only after enforcing the evidence cutoff."""

    common_fields = (
        "id",
        "source_id",
        "source_record_id",
        "observed_at",
        "published_at",
        "retrieved_at",
        "revision",
    )
    selected_fields = tuple(dict.fromkeys((*common_fields, *value_fields)))
    rank = func.row_number().over(
        partition_by=tuple(getattr(model, field) for field in identity_fields),
        order_by=(
            model.revision.desc(),
            model.retrieved_at.desc(),
            model.id.desc(),
        ),
    ).label("revision_rank")
    ranked = (
        select(
            *(getattr(model, field).label(field) for field in selected_fields),
            rank,
        )
        .where(
            model.source_id == source_id,
            model.observed_at >= window_start,
            model.observed_at <= cutoff_at,
            model.retrieved_at <= cutoff_at,
            or_(model.published_at.is_(None), model.published_at <= cutoff_at),
        )
        .subquery()
    )
    return (
        select(*(ranked.c[field] for field in selected_fields))
        .where(ranked.c.revision_rank == 1)
        .order_by(ranked.c.observed_at.desc(), *(
            ranked.c[field] for field in identity_fields if field != "observed_at"
        ))
        .limit(_MAX_ROWS_PER_FAMILY)
    )


def _latest_visible_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    identity_fields: tuple[str, ...],
    cutoff_at: datetime,
    window_start: datetime,
) -> tuple[Mapping[str, Any], ...]:
    """Defence in depth for custom repositories and deterministic unit replay."""

    visible: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for row in rows[:_MAX_ROWS_PER_FAMILY]:
        observed_at = _aware_utc(_row_time(row, "observed_at"), "observed_at")
        retrieved_at = _aware_utc(_row_time(row, "retrieved_at"), "retrieved_at")
        published_at = row.get("published_at")
        if observed_at < window_start or observed_at > cutoff_at:
            continue
        if retrieved_at > cutoff_at:
            continue
        if published_at is not None and _aware_utc(published_at, "published_at") > cutoff_at:
            continue
        identity = tuple(row[field] for field in identity_fields)
        current = visible.get(identity)
        if current is None or _revision_order(row) > _revision_order(current):
            visible[identity] = row
    return tuple(
        sorted(
            visible.values(),
            key=lambda row: (
                _aware_utc(_row_time(row, "observed_at"), "observed_at"),
                tuple(str(row[field]) for field in identity_fields),
            ),
            reverse=True,
        )
    )


def _generation_component(
    rows: Sequence[Mapping[str, Any]],
    *,
    cutoff_at: datetime,
) -> ObservedEvidenceComponent | None:
    grouped = _group_by_observed_at(rows)
    timestamps = sorted(grouped, reverse=True)
    if len(timestamps) < 2:
        return None
    current_at, previous_at = timestamps[:2]
    if cutoff_at - current_at > _MAX_GENERATION_AGE:
        return None
    if current_at - previous_at > _MAX_SNAPSHOT_GAP:
        return None
    current_rows = grouped[current_at]
    previous_rows = grouped[previous_at]
    if any(
        not math.isfinite(float(row["generation_mw"]))
        for row in (*previous_rows, *current_rows)
    ):
        return None
    current_series = {str(row["series_key"]): str(row["fuel_type"]) for row in current_rows}
    previous_series = {str(row["series_key"]): str(row["fuel_type"]) for row in previous_rows}
    if (
        len(current_series) < _MIN_GENERATION_SERIES
        or current_series != previous_series
        or "unknown" in current_series.values()
    ):
        return None

    previous = _aggregate_generation(previous_rows)
    current = _aggregate_generation(current_rows)
    evidence_rows = (*previous_rows, *current_rows)
    return ObservedEvidenceComponent(
        kind=EvidenceComponentKind.GENERATION,
        window=GridObservationWindow(
            observed_at=current_at,
            previous_generation_mw=previous,
            current_generation_mw=current,
            previous_net_import_mw=None,
            current_net_import_mw=None,
            net_flow_sustained_samples=0,
            frequency_hz=None,
            generation_source_record_ids=_record_ids(evidence_rows),
            interconnector_source_record_ids=[],
            frequency_source_record_ids=[],
        ),
        source_ids=tuple(sorted({str(row["source_id"]) for row in evidence_rows})),
        evidence_record_ids=tuple(_record_ids(evidence_rows)),
        evidence_fingerprint=_rows_fingerprint(
            EvidenceComponentKind.GENERATION,
            evidence_rows,
            value_fields=("series_key", "fuel_type", "generation_mw"),
        ),
    )


def _interconnector_component(
    rows: Sequence[Mapping[str, Any]],
    *,
    cutoff_at: datetime,
) -> ObservedEvidenceComponent | None:
    grouped = _group_by_observed_at(rows)
    timestamps = sorted(grouped, reverse=True)
    if len(timestamps) < 3:
        return None
    current_at, sustained_at, previous_at = timestamps[:3]
    if cutoff_at - current_at > _MAX_INTERCONNECTOR_AGE:
        return None
    if (
        current_at - sustained_at > _MAX_SNAPSHOT_GAP
        or sustained_at - previous_at > _MAX_SNAPSHOT_GAP
    ):
        return None
    try:
        registry = connector_registry_for_date(current_at.date())
    except ValueError:
        return None
    expected = set(registry.expected_connector_ids)
    snapshot_rows = (grouped[previous_at], grouped[sustained_at], grouped[current_at])
    if any(
        {str(row["connector_code"]).upper() for row in item} != expected
        for item in snapshot_rows
    ):
        return None
    if any(
        not math.isfinite(float(row["flow_mw"]))
        for snapshot in snapshot_rows
        for row in snapshot
    ):
        return None

    previous_net, sustained_net, current_net = (
        sum(float(row["flow_mw"]) for row in item) for item in snapshot_rows
    )
    current_sign = _sign(current_net)
    sustained_samples = sum(
        1
        for value in (sustained_net, current_net)
        if _sign(value) == current_sign and abs(value) >= _REVERSAL_MAGNITUDE_MW
    )
    evidence_rows = tuple(row for snapshot in snapshot_rows for row in snapshot)
    return ObservedEvidenceComponent(
        kind=EvidenceComponentKind.INTERCONNECTORS,
        window=GridObservationWindow(
            observed_at=current_at,
            previous_generation_mw={},
            current_generation_mw={},
            previous_net_import_mw=previous_net,
            current_net_import_mw=current_net,
            net_flow_sustained_samples=sustained_samples,
            frequency_hz=None,
            generation_source_record_ids=[],
            interconnector_source_record_ids=_record_ids(evidence_rows),
            frequency_source_record_ids=[],
        ),
        source_ids=tuple(sorted({str(row["source_id"]) for row in evidence_rows})),
        evidence_record_ids=tuple(_record_ids(evidence_rows)),
        evidence_fingerprint=_rows_fingerprint(
            EvidenceComponentKind.INTERCONNECTORS,
            evidence_rows,
            value_fields=("connector_code", "flow_mw"),
            extra={"connectorRegistryVersion": registry.version},
        ),
    )


def _frequency_component(
    rows: Sequence[Mapping[str, Any]],
    *,
    cutoff_at: datetime,
) -> ObservedEvidenceComponent | None:
    gb_rows = [row for row in rows if str(row["series_key"]).lower() == "gb"]
    if not gb_rows:
        return None
    current = max(
        gb_rows,
        key=lambda row: _aware_utc(_row_time(row, "observed_at"), "observed_at"),
    )
    observed_at = _aware_utc(_row_time(current, "observed_at"), "observed_at")
    if cutoff_at - observed_at > _MAX_FREQUENCY_AGE:
        return None
    frequency_hz = float(current["frequency_hz"])
    if not math.isfinite(frequency_hz) or not 40.0 <= frequency_hz <= 60.0:
        return None
    record_ids = _record_ids((current,))
    return ObservedEvidenceComponent(
        kind=EvidenceComponentKind.FREQUENCY,
        window=GridObservationWindow(
            observed_at=observed_at,
            previous_generation_mw={},
            current_generation_mw={},
            previous_net_import_mw=None,
            current_net_import_mw=None,
            net_flow_sustained_samples=0,
            frequency_hz=frequency_hz,
            generation_source_record_ids=[],
            interconnector_source_record_ids=[],
            frequency_source_record_ids=record_ids,
        ),
        source_ids=(str(current["source_id"]),),
        evidence_record_ids=tuple(record_ids),
        evidence_fingerprint=_rows_fingerprint(
            EvidenceComponentKind.FREQUENCY,
            (current,),
            value_fields=("series_key", "frequency_hz"),
        ),
    )


def _insert_event_statement(draft: ObservedEventDraft, *, detected_at: datetime):
    candidate = draft.candidate
    return (
        pg_insert(DetectedEvent)
        .values(
            id=uuid.uuid5(_EVENT_NAMESPACE, candidate.dedupe_key),
            deterministic_key=candidate.dedupe_key,
            event_type=candidate.event_type,
            status=EventStatus.OPEN,
            severity=_SEVERITY[candidate.severity],
            confidence=_CONFIDENCE[candidate.confidence],
            title=draft.title,
            deterministic_summary=draft.summary,
            rule_version=draft.rule_version,
            evidence_version=1,
            evidence_checksum=draft.evidence_checksum,
            evidence=draft.evidence,
            source_ids=list(draft.source_ids),
            related_asset_ids=[],
            event_started_at=candidate.occurred_at,
            first_detected_at=detected_at,
            last_observed_at=candidate.occurred_at,
            resolved_at=None,
            created_at=detected_at,
            updated_at=detected_at,
        )
        .on_conflict_do_nothing(index_elements=[DetectedEvent.deterministic_key])
        .returning(DetectedEvent.id)
    )


def _group_by_observed_at(
    rows: Sequence[Mapping[str, Any]],
) -> dict[datetime, list[Mapping[str, Any]]]:
    grouped: dict[datetime, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_aware_utc(_row_time(row, "observed_at"), "observed_at")].append(row)
    return grouped


def _aggregate_generation(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    values: dict[str, float] = defaultdict(float)
    for row in rows:
        values[str(row["fuel_type"])] += float(row["generation_mw"])
    return dict(values)


def _record_ids(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    values: list[str] = []
    for row in rows:
        source_record_id = row.get("source_record_id")
        values.append(
            str(source_record_id)
            if source_record_id
            else (
                f"{row['source_id']}:{row.get('series_key') or row.get('connector_code')}:"
                f"{_aware_utc(_row_time(row, 'observed_at'), 'observed_at').isoformat()}:"
                f"r{int(row.get('revision') or 0)}"
            )
        )
    return list(dict.fromkeys(values))


def _rows_fingerprint(
    kind: EvidenceComponentKind,
    rows: Sequence[Mapping[str, Any]],
    *,
    value_fields: tuple[str, ...],
    extra: Mapping[str, Any] | None = None,
) -> str:
    records = [
        {
            "sourceId": str(row["source_id"]),
            "recordId": _record_ids((row,))[0],
            "observedAt": _aware_utc(
                _row_time(row, "observed_at"), "observed_at"
            ).isoformat(),
            "revision": int(row.get("revision") or 0),
            "values": {field: row[field] for field in value_fields},
        }
        for row in rows
    ]
    records.sort(
        key=lambda item: (
            item["observedAt"],
            item["sourceId"],
            item["recordId"],
        )
    )
    payload = {"component": kind.value, "records": records, **dict(extra or {})}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _revision_order(row: Mapping[str, Any]) -> tuple[int, datetime, str]:
    return (
        int(row.get("revision") or 0),
        _aware_utc(_row_time(row, "retrieved_at"), "retrieved_at"),
        str(row.get("id") or ""),
    )


def _row_time(row: Mapping[str, Any], field: str) -> datetime:
    value = row[field]
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    return value


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
