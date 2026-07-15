"""Source-neutral presentation of current data-family health and supply accounting."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Protocol

from app.api.models import (
    DataFamilyStatus,
    DeliveryState,
    FactState,
    FreshnessSummary,
    FreshnessSummaryState,
    SupplyAccounting,
)
from app.metrics import CURRENT_FAMILY_POLICIES, MetricFamily, MetricFreshnessPolicy
from app.persistence.reads import CurrentGridRead, ReadProvenance


class _Provenanced(Protocol):
    provenance: ReadProvenance


def present_data_status(read: CurrentGridRead) -> list[DataFamilyStatus]:
    families: dict[MetricFamily, tuple[_Provenanced, ...]] = {
        MetricFamily.GENERATION: tuple(read.generation),
        MetricFamily.DEMAND: (read.demand,) if read.demand is not None else (),
        MetricFamily.FREQUENCY: (
            (read.frequency,) if read.frequency is not None else ()
        ),
        MetricFamily.INTERCONNECTORS: tuple(read.interconnectors),
        MetricFamily.CARBON: (read.carbon,) if read.carbon is not None else (),
    }
    return [
        _family_status(
            policy,
            families.get(policy.family, ()),
            evaluated_at=read.requested_at,
        )
        for policy in CURRENT_FAMILY_POLICIES
    ]


def present_freshness_summary(
    statuses: Iterable[DataFamilyStatus],
    *,
    critical: bool = False,
) -> FreshnessSummary:
    """Summarize required families while retaining their independent clocks."""

    required = tuple(status for status in statuses if status.required_for_snapshot)
    if not required:
        raise ValueError("Freshness summary requires at least one required family")

    counts = {
        "current": 0,
        "delayed": 0,
        "stale": 0,
        "unavailable": 0,
    }
    for status in required:
        counts[_combined_family_state(status)] += 1

    observed_times = tuple(
        status.observed_at for status in required if status.observed_at is not None
    )
    oldest = min(observed_times) if observed_times else None
    newest = max(observed_times) if observed_times else None
    spread = int((newest - oldest).total_seconds()) if oldest and newest else None
    mixed_cadences = len({status.expected_cadence_seconds for status in required}) > 1

    if critical:
        state = FreshnessSummaryState.CRITICAL
        label = "Critical reported event"
        detail = (
            "A publisher has reported a critical event. Reading times remain "
            "independent and are shown by data family."
        )
    elif counts["unavailable"]:
        state = FreshnessSummaryState.UNAVAILABLE
        label = "Core reading unavailable"
        detail = "At least one required data family has no usable current reading."
    elif counts["stale"]:
        state = FreshnessSummaryState.STALE
        label = "Some core readings are stale"
        detail = "At least one required reading is beyond its source-specific stale threshold."
    elif counts["delayed"]:
        state = FreshnessSummaryState.DELAYED
        label = "Some core readings are delayed"
        detail = (
            "The source facts or their delivery are behind the expected timing "
            "for at least one required data family."
        )
    elif mixed_cadences or (spread or 0) > 0:
        state = FreshnessSummaryState.MIXED
        label = "Current readings, mixed update times"
        detail = (
            "Generation, demand and carbon update on independent source cadences; "
            "this response is not a single synchronized measurement."
        )
    else:
        state = FreshnessSummaryState.CURRENT
        label = "Core readings current"
        detail = (
            "All required families are within their source-specific current "
            "thresholds, but remain independently measured facts."
        )

    return FreshnessSummary(
        state=state,
        label=label,
        detail=detail,
        evaluated_at=required[0].evaluated_at,
        required_family_count=len(required),
        current_family_count=counts["current"],
        delayed_family_count=counts["delayed"],
        stale_family_count=counts["stale"],
        unavailable_family_count=counts["unavailable"],
        oldest_required_observed_at=oldest,
        newest_required_observed_at=newest,
        observation_spread_seconds=spread,
    )


def present_supply_accounting(read: CurrentGridRead) -> SupplyAccounting:
    domestic_generation = sum(
        max(0.0, item.megawatts) for item in read.generation
    )
    storage_generation = sum(
        max(0.0, item.megawatts)
        for item in read.generation
        if item.fuel_type == "pumped_storage"
    )
    gross_imports = sum(max(0.0, item.megawatts) for item in read.interconnectors)
    gross_exports = sum(max(0.0, -item.megawatts) for item in read.interconnectors)
    net_imports = gross_imports - gross_exports
    legacy_displayed = domestic_generation + max(0.0, net_imports)

    return SupplyAccounting(
        boundary=(
            "Transmission-visible positive FUELINST generation and the "
            "interconnectors represented in the current source response."
        ),
        is_complete=False,
        generation_data_available=bool(read.generation),
        interconnector_data_available=bool(read.interconnectors),
        domestic_generation_mw=round(domestic_generation, 1),
        gross_imports_mw=round(gross_imports, 1),
        gross_exports_mw=round(gross_exports, 1),
        net_imports_mw=round(net_imports, 1),
        storage_generation_mw=round(storage_generation, 1),
        storage_charging_mw=None,
        legacy_displayed_generation_mw=round(legacy_displayed, 1),
        legacy_mix_basis=(
            "positive transmission-visible generation plus positive net imports"
        ),
        note=(
            "This is not a complete Great Britain supply balance: FUELINST omits "
            "embedded and unmetered generation. FUELINST pumped-storage values "
            "represent generating output and do not provide a complete charging "
            "measure, so storageChargingMW is unavailable. The existing generation "
            "array is retained for compatibility; its optional imports category is "
            "positive net imports, and net exports are not subtracted."
        ),
    )


def _family_status(
    policy: MetricFreshnessPolicy,
    readings: Iterable[_Provenanced],
    *,
    evaluated_at: datetime,
) -> DataFamilyStatus:
    values = tuple(readings)
    common = {
        "family": policy.family,
        "metric_ids": list(policy.metric_ids),
        "required_for_snapshot": policy.required_for_snapshot,
        "evaluated_at": evaluated_at,
        "expected_cadence_seconds": policy.expected_cadence_seconds,
        "delivery_healthy_seconds": policy.delivery_healthy_seconds,
        "delivery_stale_seconds": policy.delivery_stale_seconds,
        "fact_live_seconds": policy.fact_live_seconds,
        "fact_stale_seconds": policy.fact_stale_seconds,
    }
    if not values:
        return DataFamilyStatus(
            **common,
            delivery_state=DeliveryState.UNAVAILABLE,
            fact_state=FactState.UNAVAILABLE,
            series_count=0,
        )

    provenances = tuple(item.provenance for item in values)
    observed_at = min(item.observed_at for item in provenances)
    retrieved_at = min(item.retrieved_at for item in provenances)
    published_times = tuple(
        item.published_at for item in provenances if item.published_at is not None
    )
    observation_age = _age_seconds(evaluated_at, observed_at)
    retrieval_age = _age_seconds(evaluated_at, retrieved_at)
    valid_to = (
        observed_at + timedelta(seconds=policy.valid_interval_seconds)
        if policy.valid_interval_seconds is not None
        else None
    )
    return DataFamilyStatus(
        **common,
        source_ids=sorted({item.source_id for item in provenances}),
        source_record_ids=sorted(
            {
                item.source_record_id
                for item in provenances
                if item.source_record_id is not None
            }
        ),
        delivery_state=_delivery_state(policy, retrieval_age),
        fact_state=_fact_state(policy, observation_age),
        observed_at=observed_at,
        published_at=min(published_times) if published_times else None,
        retrieved_at=retrieved_at,
        valid_to=valid_to,
        observation_age_seconds=observation_age,
        retrieval_age_seconds=retrieval_age,
        series_count=len(values),
    )


def _delivery_state(
    policy: MetricFreshnessPolicy,
    age_seconds: int,
) -> DeliveryState:
    if age_seconds <= policy.delivery_healthy_seconds:
        return DeliveryState.HEALTHY
    if age_seconds < policy.delivery_stale_seconds:
        return DeliveryState.DELAYED
    return DeliveryState.STALE


def _combined_family_state(status: DataFamilyStatus) -> str:
    if (
        status.delivery_state is DeliveryState.UNAVAILABLE
        or status.fact_state is FactState.UNAVAILABLE
    ):
        return "unavailable"
    if (
        status.delivery_state is DeliveryState.STALE
        or status.fact_state is FactState.STALE
    ):
        return "stale"
    if (
        status.delivery_state is DeliveryState.DELAYED
        or status.fact_state is FactState.DELAYED
    ):
        return "delayed"
    return "current"


def _fact_state(policy: MetricFreshnessPolicy, age_seconds: int) -> FactState:
    if age_seconds <= policy.fact_live_seconds:
        return FactState.LIVE
    if age_seconds < policy.fact_stale_seconds:
        return FactState.DELAYED
    return FactState.STALE


def _age_seconds(evaluated_at: datetime, source_time: datetime) -> int:
    return max(0, int((evaluated_at - source_time).total_seconds()))
