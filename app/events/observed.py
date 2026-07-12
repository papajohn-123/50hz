"""Deterministic event drafts built only from normalized observations.

This module is deliberately database-agnostic.  It keeps rule evaluation,
public copy and evidence hashing replayable so the worker can safely retry a
maintenance pass without asking an LLM to classify measurements.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.events.models import EventCandidate
from app.events.processor import EventProcessor, GridObservationWindow


EVIDENCE_SCHEMA_VERSION = "50hz.observed-event-evidence.v1"
RULE_VERSION_PREFIX = "observed."
OBSERVATION_WINDOW_METHOD_VERSION = "window-v1"


class EvidenceComponentKind(StrEnum):
    GENERATION = "generation"
    INTERCONNECTORS = "interconnectors"
    FREQUENCY = "frequency"


@dataclass(frozen=True, slots=True)
class ObservedEvidenceComponent:
    """One internally coherent input window for one family of pure rules."""

    kind: EvidenceComponentKind
    window: GridObservationWindow
    source_ids: tuple[str, ...]
    evidence_record_ids: tuple[str, ...]
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        _aware_utc(self.window.observed_at, "window.observed_at")
        if not self.source_ids:
            raise ValueError("an evidence component requires a source")
        if not self.evidence_record_ids:
            raise ValueError("an evidence component requires source records")
        if len(self.evidence_fingerprint) != 64:
            raise ValueError("evidence_fingerprint must be a SHA-256 digest")


@dataclass(frozen=True, slots=True)
class ObservedEvidenceBatch:
    """A cutoff-safe collection of independently coherent rule windows."""

    cutoff_at: datetime
    components: tuple[ObservedEvidenceComponent, ...]

    def __post_init__(self) -> None:
        cutoff = _aware_utc(self.cutoff_at, "cutoff_at")
        object.__setattr__(self, "cutoff_at", cutoff)
        kinds = [component.kind for component in self.components]
        if len(kinds) != len(set(kinds)):
            raise ValueError("an evidence batch may contain only one component per kind")
        if any(component.window.observed_at > cutoff for component in self.components):
            raise ValueError("evidence cannot be observed after the evaluation cutoff")

    @property
    def evaluation_key(self) -> str:
        material = [
            {
                "kind": component.kind.value,
                "observedAt": component.window.observed_at.astimezone(UTC).isoformat(),
                "fingerprint": component.evidence_fingerprint,
            }
            for component in sorted(self.components, key=lambda item: item.kind.value)
        ]
        return _checksum(material)


@dataclass(frozen=True, slots=True)
class ObservedEventDraft:
    candidate: EventCandidate
    component_kind: EvidenceComponentKind
    rule_version: str
    title: str
    summary: str
    evidence_checksum: str
    evidence: dict[str, object]
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StatefulEventScope:
    """A rule family whose absence is meaningful for this evidence component."""

    event_type: str
    observed_at: datetime
    active_deterministic_keys: tuple[str, ...]
    same_timestamp_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            _aware_utc(self.observed_at, "observed_at"),
        )


@dataclass(frozen=True, slots=True)
class ObservedEventEvaluation:
    cutoff_at: datetime
    evaluation_key: str
    drafts: tuple[ObservedEventDraft, ...]
    stateful_scopes: tuple[StatefulEventScope, ...]


class ObservedEventEvaluator:
    """Evaluate each source family separately, never at a synthetic timestamp."""

    def __init__(self, processor: EventProcessor | None = None) -> None:
        self._processor = processor or EventProcessor()

    def evaluate(self, batch: ObservedEvidenceBatch) -> ObservedEventEvaluation:
        drafts: list[ObservedEventDraft] = []
        scopes: list[StatefulEventScope] = []
        for component in batch.components:
            candidates = self._processor.evaluate(component.window)
            component_drafts = [
                _event_draft(candidate, component) for candidate in candidates
            ]
            drafts.extend(component_drafts)

            stateful_type = _stateful_event_type(component.kind)
            if stateful_type is not None:
                scopes.append(
                    StatefulEventScope(
                        event_type=stateful_type,
                        observed_at=component.window.observed_at,
                        active_deterministic_keys=tuple(
                            sorted(
                                draft.candidate.dedupe_key
                                for draft in component_drafts
                                if draft.candidate.event_type == stateful_type
                            )
                        ),
                    )
                )
            momentary_type = _momentary_event_type(component.kind)
            if momentary_type is not None:
                scopes.append(
                    StatefulEventScope(
                        event_type=momentary_type,
                        observed_at=component.window.observed_at,
                        active_deterministic_keys=tuple(
                            sorted(
                                draft.candidate.dedupe_key
                                for draft in component_drafts
                                if draft.candidate.event_type == momentary_type
                            )
                        ),
                        # A later snapshot does not erase a real transition.
                        # This exact-time scope only retracts evidence removed by
                        # an immutable source correction at the same timestamp.
                        same_timestamp_only=True,
                    )
                )

        return ObservedEventEvaluation(
            cutoff_at=batch.cutoff_at,
            evaluation_key=batch.evaluation_key,
            drafts=tuple(
                sorted(
                    drafts,
                    key=lambda draft: (
                        draft.candidate.occurred_at,
                        draft.candidate.dedupe_key,
                    ),
                )
            ),
            stateful_scopes=tuple(scopes),
        )


def _event_draft(
    candidate: EventCandidate,
    component: ObservedEvidenceComponent,
) -> ObservedEventDraft:
    title, summary = _safe_copy(candidate)
    evidence: dict[str, object] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "observation_window_method_version": OBSERVATION_WINDOW_METHOD_VERSION,
        "evidence_class": candidate.evidence_class.value,
        "cause_reported": False,
        "candidate": candidate.model_dump(mode="json"),
        "component": component.kind.value,
        "source_ids": list(component.source_ids),
        "source_record_ids": list(component.evidence_record_ids),
        "unknowns": ["Measurements do not identify a cause."],
        "permitted_comparisons": [],
    }
    return ObservedEventDraft(
        candidate=candidate,
        component_kind=component.kind,
        rule_version=(
            f"{RULE_VERSION_PREFIX}{OBSERVATION_WINDOW_METHOD_VERSION}."
            f"{candidate.rule_id}.v{candidate.rule_version}"
        ),
        title=title,
        summary=summary,
        evidence_checksum=_checksum(evidence),
        evidence=evidence,
        source_ids=component.source_ids,
    )


def _safe_copy(candidate: EventCandidate) -> tuple[str, str]:
    facts = {fact.fact_id: fact for fact in candidate.facts}
    if candidate.event_type == "generation_leader_change":
        leader = _text_value(facts, "leader").replace("_", " ")
        output = _number_value(facts, "output")
        share = _number_value(facts, "share")
        return (
            f"{leader.capitalize()} became the largest observed generation category",
            (
                f"The coherent generation snapshot measured {leader} at "
                f"{_format_number(output)} MW, {share:.1f}% of observed generation."
            ),
        )
    if candidate.event_type == "renewable_share_milestone":
        share = _number_value(facts, "share")
        output = _number_value(facts, "output")
        return (
            f"Renewables reached {share:.1f}% of observed generation",
            (
                "The coherent generation snapshot measured wind, solar and hydro "
                f"at {_format_number(output)} MW, {share:.1f}% of the observed mix."
            ),
        )
    if candidate.event_type == "energy_position_reversal":
        position = _text_value(facts, "position")
        flow = _number_value(facts, "net_flow")
        direction = "into Britain" if flow > 0 else "from Britain"
        return (
            f"Net interconnector flow switched to {position}",
            (
                "The direction persisted across two complete connector snapshots; "
                f"signed net flow was {_format_number(abs(flow))} MW {direction}."
            ),
        )
    if candidate.event_type == "frequency_excursion":
        frequency = _number_value(facts, "frequency")
        direction = _text_value(facts, "direction")
        relative = "below 49.8 Hz" if direction == "low" else "above 50.2 Hz"
        return (
            f"Observed grid frequency was {direction}",
            f"The measured frequency was {frequency:.3f} Hz, {relative}.",
        )
    raise ValueError(f"unsupported observed event type: {candidate.event_type}")


def _stateful_event_type(kind: EvidenceComponentKind) -> str | None:
    if kind is EvidenceComponentKind.GENERATION:
        return "renewable_share_milestone"
    if kind is EvidenceComponentKind.FREQUENCY:
        return "frequency_excursion"
    return None


def _momentary_event_type(kind: EvidenceComponentKind) -> str | None:
    if kind is EvidenceComponentKind.GENERATION:
        return "generation_leader_change"
    if kind is EvidenceComponentKind.INTERCONNECTORS:
        return "energy_position_reversal"
    return None


def _text_value(facts: dict[str, object], fact_id: str) -> str:
    fact = facts.get(fact_id)
    value = getattr(fact, "value", None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"event fact {fact_id!r} must be non-empty text")
    return value


def _number_value(facts: dict[str, object], fact_id: str) -> float:
    fact = facts.get(fact_id)
    value = getattr(fact, "value", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"event fact {fact_id!r} must be numeric")
    return float(value)


def _format_number(value: float) -> str:
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.1f}"


def _checksum(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
