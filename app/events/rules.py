from collections.abc import Mapping
from datetime import datetime

from app.events.models import (
    Confidence,
    EventCandidate,
    EvidenceClass,
    EvidenceFact,
    Severity,
)


def _fact(
    fact_id: str,
    metric: str,
    label: str,
    value: int | float | str | bool,
    unit: str | None,
    occurred_at: datetime,
    source_record_ids: list[str],
) -> EvidenceFact:
    return EvidenceFact(
        fact_id=fact_id,
        metric=metric,
        label=label,
        value=value,
        unit=unit,
        observed_at=occurred_at,
        source_record_ids=source_record_ids,
    )


def generation_leader_change(
    previous: Mapping[str, float],
    current: Mapping[str, float],
    occurred_at: datetime,
    source_record_ids: list[str],
    *,
    minimum_share: float = 0.20,
) -> EventCandidate | None:
    if not previous or not current:
        return None
    previous_leader = max(previous, key=previous.__getitem__)
    current_leader = max(current, key=current.__getitem__)
    total = sum(max(0.0, value) for value in current.values())
    if previous_leader == current_leader or total <= 0:
        return None
    share = current[current_leader] / total
    if share < minimum_share:
        return None
    bucket = occurred_at.replace(minute=0, second=0, microsecond=0).isoformat()
    return EventCandidate(
        rule_id="generation.leader_change",
        rule_version=1,
        event_type="generation_leader_change",
        subject_type="fuel",
        subject_id=current_leader,
        occurred_at=occurred_at,
        severity=Severity.NOTABLE,
        evidence_class=EvidenceClass.DERIVED,
        confidence=Confidence.HIGH,
        confidence_reasons=["Calculated from a complete generation-mix observation"],
        dedupe_key=f"generation.leader_change:{current_leader}:{bucket}",
        facts=[
            _fact("leader", "generation_leader", "largest generation source", current_leader, None, occurred_at, source_record_ids),
            _fact("output", f"generation.{current_leader}", "current output", round(current[current_leader], 1), "MW", occurred_at, source_record_ids),
            _fact("share", f"generation_share.{current_leader}", "share of generation", round(share * 100, 1), "%", occurred_at, source_record_ids),
        ],
    )


def interconnector_reversal(
    previous_net_mw: float,
    current_net_mw: float,
    occurred_at: datetime,
    source_record_ids: list[str],
    *,
    sustained_samples: int,
    minimum_magnitude_mw: float = 100.0,
) -> EventCandidate | None:
    crossed = (previous_net_mw < 0 < current_net_mw) or (previous_net_mw > 0 > current_net_mw)
    if not crossed or abs(current_net_mw) < minimum_magnitude_mw or sustained_samples < 2:
        return None
    position = "importing" if current_net_mw > 0 else "exporting"
    bucket = occurred_at.replace(minute=0, second=0, microsecond=0).isoformat()
    return EventCandidate(
        rule_id="interconnector.net_reversal",
        rule_version=1,
        event_type="energy_position_reversal",
        subject_type="system",
        subject_id="gb",
        occurred_at=occurred_at,
        severity=Severity.NOTABLE,
        evidence_class=EvidenceClass.DERIVED,
        confidence=Confidence.HIGH,
        confidence_reasons=["Direction persisted across at least two observations"],
        dedupe_key=f"interconnector.net_reversal:{position}:{bucket}",
        facts=[
            _fact("position", "energy_position", "new energy position", position, None, occurred_at, source_record_ids),
            _fact("net_flow", "interconnector.net_flow", "net interconnector flow", round(current_net_mw, 1), "MW", occurred_at, source_record_ids),
        ],
    )


def renewable_share_milestone(
    generation_mw: Mapping[str, float],
    occurred_at: datetime,
    source_record_ids: list[str],
    *,
    threshold: float = 0.50,
) -> EventCandidate | None:
    total = sum(max(0.0, value) for value in generation_mw.values())
    renewables = sum(max(0.0, generation_mw.get(fuel, 0.0)) for fuel in ("wind", "solar", "hydro"))
    if total <= 0 or renewables / total < threshold:
        return None
    share = renewables / total
    day = occurred_at.date().isoformat()
    return EventCandidate(
        rule_id="generation.renewable_share",
        rule_version=1,
        event_type="renewable_share_milestone",
        subject_type="system",
        subject_id="gb",
        occurred_at=occurred_at,
        severity=Severity.NOTABLE,
        evidence_class=EvidenceClass.DERIVED,
        confidence=Confidence.HIGH,
        confidence_reasons=["Calculated from a complete generation-mix observation"],
        dedupe_key=f"generation.renewable_share:{threshold:.2f}:{day}",
        facts=[
            _fact("share", "renewable_share", "renewable share of generation", round(share * 100, 1), "%", occurred_at, source_record_ids),
            _fact("output", "renewable_output", "renewable output", round(renewables, 1), "MW", occurred_at, source_record_ids),
        ],
    )


def frequency_excursion(
    frequency_hz: float,
    occurred_at: datetime,
    source_record_ids: list[str],
    *,
    lower_hz: float = 49.8,
    upper_hz: float = 50.2,
) -> EventCandidate | None:
    if lower_hz <= frequency_hz <= upper_hz:
        return None
    direction = "low" if frequency_hz < lower_hz else "high"
    bucket = occurred_at.replace(minute=occurred_at.minute // 5 * 5, second=0, microsecond=0).isoformat()
    return EventCandidate(
        rule_id="frequency.excursion",
        rule_version=1,
        event_type="frequency_excursion",
        subject_type="system",
        subject_id="gb",
        occurred_at=occurred_at,
        severity=Severity.IMPORTANT,
        evidence_class=EvidenceClass.OBSERVED,
        confidence=Confidence.HIGH,
        confidence_reasons=["Direct frequency observation outside the configured normal band"],
        dedupe_key=f"frequency.excursion:{direction}:{bucket}",
        facts=[
            _fact("frequency", "frequency", "observed grid frequency", round(frequency_hz, 3), "Hz", occurred_at, source_record_ids),
            _fact("direction", "frequency_direction", "excursion direction", direction, None, occurred_at, source_record_ids),
        ],
    )


def reported_unavailability(
    *,
    asset_id: str,
    asset_name: str,
    unavailable_mw: float,
    planned: bool,
    occurred_at: datetime,
    source_record_ids: list[str],
    reported_cause: str | None = None,
) -> EventCandidate:
    bucket = occurred_at.replace(minute=0, second=0, microsecond=0).isoformat()
    facts = [
        _fact("asset", "unavailable_asset", "reported unavailable asset", asset_name, None, occurred_at, source_record_ids),
        _fact("capacity", "unavailable_capacity", "reported unavailable capacity", round(unavailable_mw, 1), "MW", occurred_at, source_record_ids),
        _fact("planned", "unavailability_planned", "planned unavailability", planned, None, occurred_at, source_record_ids),
    ]
    if reported_cause:
        facts.append(_fact("cause", "reported_cause", "reported cause", reported_cause, None, occurred_at, source_record_ids))
    return EventCandidate(
        rule_id="remit.reported_unavailability",
        rule_version=1,
        event_type="reported_unit_unavailability",
        subject_type="asset",
        subject_id=asset_id,
        occurred_at=occurred_at,
        severity=Severity.IMPORTANT if unavailable_mw >= 500 else Severity.NOTABLE,
        evidence_class=EvidenceClass.REPORTED,
        confidence=Confidence.HIGH,
        confidence_reasons=["Published in an authoritative REMIT notice"],
        dedupe_key=f"remit.reported_unavailability:{asset_id}:{bucket}",
        facts=facts,
        cause_reported=reported_cause is not None,
    )

