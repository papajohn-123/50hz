from __future__ import annotations

from datetime import UTC, datetime

from app.api.models import DataFamilyStatus, FactState, MetricFamily
from app.db.models import SourceMetadata
from app.source_health.models import (
    SourceDeliveryState,
    SourceFactState,
    SourceHealthResponse,
    SourceHealthStatus,
)
from app.source_health.repository import SourceRunSummary


CURRENT_SOURCE_FAMILIES: dict[str, tuple[MetricFamily, ...]] = {
    "elexon.fuelinst": (
        MetricFamily.GENERATION,
        MetricFamily.INTERCONNECTORS,
    ),
    "elexon.indo": (MetricFamily.DEMAND,),
    "elexon.freq": (MetricFamily.FREQUENCY,),
    "neso.carbon-intensity-national": (MetricFamily.CARBON,),
}


def build_source_health(
    sources: tuple[SourceMetadata, ...],
    runs: dict[str, SourceRunSummary],
    *,
    evaluated_at: datetime,
    current_fact_statuses: list[DataFamilyStatus] | None = None,
) -> SourceHealthResponse:
    instant = _aware_utc(evaluated_at)
    statuses = current_fact_statuses or []
    presented = [
        _present_source(
            source,
            runs.get(source.id),
            evaluated_at=instant,
            current_fact_statuses=statuses,
        )
        for source in sources
        if source.active
    ]
    return SourceHealthResponse(
        evaluated_at=instant,
        source_count=len(presented),
        sources=presented,
    )


def _present_source(
    source: SourceMetadata,
    run: SourceRunSummary | None,
    *,
    evaluated_at: datetime,
    current_fact_statuses: list[DataFamilyStatus],
) -> SourceHealthStatus:
    last_succeeded = run.last_succeeded_at if run is not None else None
    lag = (
        _age(evaluated_at, last_succeeded)
        if last_succeeded is not None
        else None
    )
    families = CURRENT_SOURCE_FAMILIES.get(source.id, ())
    relevant = [
        status for status in current_fact_statuses if status.family in families
    ]
    fact_state, observed_at, valid_to, fact_age = _fact_summary(
        relevant,
        applicable=bool(families),
    )
    return SourceHealthStatus(
        source_id=source.id,
        publisher=source.provider,
        dataset=source.dataset,
        display_name=source.display_name,
        documentation_url=source.documentation_url,
        licence_url=source.licence_url,
        attribution=source.attribution or f"Data supplied by {source.display_name}.",
        expected_fact_cadence_seconds=source.expected_cadence_seconds,
        delivery_state=_delivery_state(source.expected_cadence_seconds, lag),
        delivery_lag_seconds=lag,
        last_attempted_at=(run.last_attempted_at if run is not None else None),
        last_attempt_state=(run.last_attempt_state if run is not None else None),
        last_succeeded_at=last_succeeded,
        fact_state=fact_state,
        fact_families=list(families),
        observed_at=observed_at,
        valid_to=valid_to,
        fact_age_seconds=fact_age,
        note=(
            "Delivery health and present-fact validity are evaluated separately."
            if families
            else "This source does not directly supply a fact in the current grid view."
        ),
    )


def _delivery_state(
    cadence_seconds: int,
    lag_seconds: int | None,
) -> SourceDeliveryState:
    if lag_seconds is None:
        return SourceDeliveryState.UNAVAILABLE
    healthy_threshold = max(cadence_seconds * 2, 60)
    stale_threshold = max(cadence_seconds * 4, 300)
    if lag_seconds <= healthy_threshold:
        return SourceDeliveryState.HEALTHY
    if lag_seconds < stale_threshold:
        return SourceDeliveryState.DELAYED
    return SourceDeliveryState.STALE


def _fact_summary(
    statuses: list[DataFamilyStatus],
    *,
    applicable: bool,
) -> tuple[SourceFactState, datetime | None, datetime | None, int | None]:
    if not applicable:
        return SourceFactState.NOT_APPLICABLE, None, None, None
    if not statuses:
        return SourceFactState.UNAVAILABLE, None, None, None

    rank = {
        FactState.LIVE: 0,
        FactState.DELAYED: 1,
        FactState.STALE: 2,
        FactState.UNAVAILABLE: 3,
    }
    worst = max(statuses, key=lambda item: rank[item.fact_state])
    observed = min(
        (item.observed_at for item in statuses if item.observed_at is not None),
        default=None,
    )
    valid_to = min(
        (item.valid_to for item in statuses if item.valid_to is not None),
        default=None,
    )
    age = max(
        (
            item.observation_age_seconds
            for item in statuses
            if item.observation_age_seconds is not None
        ),
        default=None,
    )
    return SourceFactState(worst.fact_state.value), observed, valid_to, age


def _age(evaluated_at: datetime, value: datetime) -> int:
    return max(0, int((evaluated_at - _aware_utc(value)).total_seconds()))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("source-health times must be timezone-aware")
    return value.astimezone(UTC)
