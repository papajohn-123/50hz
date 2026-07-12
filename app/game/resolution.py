from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime, time
from hashlib import sha256
from math import isfinite
from zoneinfo import ZoneInfo

from app.game.models import (
    PredictionEvidenceCoverage,
    PredictionOutcome,
    PredictionResolution,
    PredictionResolutionState,
)
from app.game.service import prediction_definition_for_date
from app.persistence.reads import InterconnectorRead


LONDON = ZoneInfo("Europe/London")
NEAR_BALANCED_THRESHOLD_MW = 50.0
RESOLUTION_RULE = (
    "Choose the complete same-timestamp FUELINST connector snapshot nearest "
    "18:00 Europe/London within 17:55–18:05. Positive signed net flow is "
    "importing; negative is exporting. Absolute net flow at or below 50 MW is void."
)


def build_prediction_resolution(
    day: date,
    *,
    as_of: datetime,
    interconnectors: tuple[InterconnectorRead, ...] = (),
) -> PredictionResolution:
    instant = _aware_utc(as_of)
    definition = prediction_definition_for_date(day)
    target = datetime.combine(day, time(18, 0), tzinfo=LONDON).astimezone(UTC)

    if instant < definition.resolves_to:
        return _resolution(
            day=day,
            definition=definition,
            target=target,
            state=PredictionResolutionState.PENDING,
            as_of=instant,
            coverage=_coverage(0, 0),
            reason=(
                "The evidence window has not closed; 50Hz will not resolve the "
                "prediction early."
            ),
        )

    eligible = tuple(
        reading
        for reading in interconnectors
        if definition.resolves_from
        <= _aware_utc(reading.provenance.observed_at)
        <= definition.resolves_to
        and isfinite(reading.megawatts)
    )
    expected_connectors = {reading.connector_id for reading in eligible}
    if not expected_connectors:
        return _resolution(
            day=day,
            definition=definition,
            target=target,
            state=PredictionResolutionState.VOID,
            as_of=instant,
            coverage=_coverage(0, 0),
            reason="No compatible interconnector observations cover the evidence window.",
        )

    grouped: dict[tuple[datetime, str], dict[str, InterconnectorRead]] = defaultdict(dict)
    for reading in eligible:
        observed = _aware_utc(reading.provenance.observed_at)
        key = (observed, reading.provenance.source_id)
        existing = grouped[key].get(reading.connector_id)
        if existing is None or _reading_revision_key(reading) > _reading_revision_key(
            existing
        ):
            grouped[key][reading.connector_id] = reading

    best_key, best_readings = min(
        grouped.items(),
        key=lambda item: (
            -len(item[1]),
            abs((item[0][0] - target).total_seconds()),
            -item[0][0].timestamp(),
            item[0][1],
        ),
    )
    coverage = _coverage(len(expected_connectors), len(best_readings))
    if not coverage.complete:
        return _resolution(
            day=day,
            definition=definition,
            target=target,
            state=PredictionResolutionState.VOID,
            as_of=instant,
            coverage=coverage,
            reason=(
                "No same-timestamp source snapshot contains every connector seen "
                "in the evidence window."
            ),
        )

    chosen = tuple(best_readings[key] for key in sorted(best_readings))
    net_flow = sum(reading.megawatts for reading in chosen)
    observed_at, source_id = best_key
    source_record_ids = sorted(
        {
            reading.provenance.source_record_id
            for reading in chosen
            if reading.provenance.source_record_id
        }
    )
    source_revision_keys = sorted(
        f"{reading.provenance.source_id}:{reading.connector_id}:"
        f"{_aware_utc(reading.provenance.observed_at).isoformat()}:"
        f"r{reading.provenance.revision}"
        for reading in chosen
    )
    watermark = max(reading.provenance.retrieved_at for reading in chosen)
    common = {
        "day": day,
        "definition": definition,
        "target": target,
        "as_of": instant,
        "coverage": coverage,
        "observed_value_mw": round(net_flow, 3),
        "observed_at": observed_at,
        "source_ids": [source_id],
        "source_record_ids": source_record_ids,
        "source_revision_keys": source_revision_keys,
        "revision_watermark_at": watermark,
    }
    if abs(net_flow) <= NEAR_BALANCED_THRESHOLD_MW:
        return _resolution(
            **common,
            state=PredictionResolutionState.VOID,
            reason=(
                "The observed signed net position is inside the ±50 MW "
                "near-balanced void band."
            ),
        )
    outcome = (
        PredictionOutcome.IMPORTING
        if net_flow > 0
        else PredictionOutcome.EXPORTING
    )
    return _resolution(
        **common,
        state=PredictionResolutionState.RESOLVED,
        outcome=outcome,
        reason=(
            "The complete observed connector snapshot has a positive signed net "
            "flow into Britain."
            if outcome is PredictionOutcome.IMPORTING
            else (
                "The complete observed connector snapshot has a negative signed "
                "net flow from Britain."
            )
        ),
    )


def _resolution(
    *,
    day: date,
    definition,
    target: datetime,
    state: PredictionResolutionState,
    as_of: datetime,
    coverage: PredictionEvidenceCoverage,
    reason: str,
    outcome: PredictionOutcome | None = None,
    observed_value_mw: float | None = None,
    observed_at: datetime | None = None,
    source_ids: list[str] | None = None,
    source_record_ids: list[str] | None = None,
    source_revision_keys: list[str] | None = None,
    revision_watermark_at: datetime | None = None,
) -> PredictionResolution:
    evidence = {
        "predictionID": definition.prediction_id,
        "ruleVersion": definition.rule_version,
        "state": state.value,
        "outcome": outcome.value if outcome is not None else None,
        "observedValueMW": observed_value_mw,
        "observedAt": observed_at.isoformat() if observed_at is not None else None,
        "coverage": coverage.model_dump(mode="json", by_alias=True),
        "sourceIDs": source_ids or [],
        "sourceRecordIDs": source_record_ids or [],
        "sourceRevisionKeys": source_revision_keys or [],
    }
    checksum = sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PredictionResolution(
        prediction_id=definition.prediction_id,
        date=day,
        question=definition.question,
        choices=definition.choices,
        metric=definition.metric,
        rule_version=definition.rule_version,
        rule=RESOLUTION_RULE,
        locks_at=definition.locks_at,
        evidence_from=definition.resolves_from,
        evidence_to=definition.resolves_to,
        target_at=target,
        state=state,
        outcome=outcome,
        observed_value_mw=observed_value_mw,
        observed_at=observed_at,
        near_balanced_threshold_mw=NEAR_BALANCED_THRESHOLD_MW,
        coverage=coverage,
        source_ids=source_ids or [],
        source_record_ids=source_record_ids or [],
        source_revision_keys=source_revision_keys or [],
        revision_watermark_at=revision_watermark_at,
        evidence_checksum=checksum,
        computed_at=as_of,
        reason=reason,
    )


def _coverage(expected: int, observed: int) -> PredictionEvidenceCoverage:
    return PredictionEvidenceCoverage(
        expected_connector_count=expected,
        observed_connector_count=observed,
        coverage_fraction=observed / expected if expected else 0,
        complete=expected > 0 and observed == expected,
    )


def _reading_revision_key(
    reading: InterconnectorRead,
) -> tuple[int, datetime, str]:
    return (
        reading.provenance.revision,
        _aware_utc(reading.provenance.retrieved_at),
        reading.provenance.source_record_id or "",
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("prediction times must be timezone-aware")
    return value.astimezone(UTC)
