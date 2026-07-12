"""Public-safe, bounded forecast-verification contract."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import DatabaseNotConfiguredError, get_session_factory
from app.forecast_verification.core import (
    HORIZON_BUCKETS,
    MINIMUM_COVERAGE_FRACTION,
    MINIMUM_VERIFIED_SAMPLES,
    TARGET_BY_METRIC,
    VERIFICATION_METHODOLOGY_VERSION,
    VERIFICATION_REGISTRY_VERSION,
    HorizonBucket,
    VerificationMetric,
)
from app.forecast_verification.models import (
    ForecastVerificationItem,
    ForecastVerificationResponse,
    HorizonDefinition,
    PublicVerificationStatus,
    VerificationSource,
    VerificationWindow,
)
from app.forecast_verification.repository import ForecastVerificationReadRepository


router = APIRouter(prefix="/v1/forecasts")


@lru_cache(maxsize=1)
def get_forecast_verification_repository() -> ForecastVerificationReadRepository:
    if not get_settings().database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        )
    try:
        return ForecastVerificationReadRepository(get_session_factory())
    except DatabaseNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        ) from error


Repository = Annotated[
    ForecastVerificationReadRepository,
    Depends(get_forecast_verification_repository),
]


@router.get(
    "/verification",
    response_model=ForecastVerificationResponse,
    tags=["forecasts"],
    summary="Get evidence-qualified national forecast verification",
)
async def forecast_verification(
    repository: Repository,
    metric: VerificationMetric | None = Query(default=None),
) -> ForecastVerificationResponse:
    try:
        rows = await repository.latest(metric)
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Forecast verification is temporarily unavailable",
            headers={"Retry-After": "300"},
        ) from error
    return present_forecast_verification(rows, metric=metric)


def present_forecast_verification(
    rows: tuple[Any, ...],
    *,
    metric: VerificationMetric | None = None,
) -> ForecastVerificationResponse:
    selected_metrics = (metric,) if metric is not None else tuple(VerificationMetric)
    safe_rows = {}
    for row in rows[:12]:
        key = _safe_key(row)
        if key is not None and key[0] in selected_metrics and key not in safe_rows:
            safe_rows[key] = row

    results = []
    computed = []
    for selected_metric in selected_metrics:
        target = TARGET_BY_METRIC[selected_metric]
        for horizon in HORIZON_BUCKETS:
            row = safe_rows.get((selected_metric, horizon))
            results.append(_present_item(target, horizon, row))
            if row is not None:
                computed.append(row.computed_at)
    return ForecastVerificationResponse(
        generated_at=max(computed) if computed else None,
        results=results,
    )


def _safe_key(row: Any) -> tuple[VerificationMetric, HorizonBucket] | None:
    try:
        metric = VerificationMetric(row.metric_id)
        horizon = HorizonBucket(row.horizon_bucket)
        target = TARGET_BY_METRIC[metric]
        expected_coverage = (
            row.verified_sample_count / row.expected_sample_count
            if row.expected_sample_count
            else 0.0
        )
        valid = (
            row.registry_version == VERIFICATION_REGISTRY_VERSION
            and row.verification_methodology_version
            == VERIFICATION_METHODOLOGY_VERSION
            and row.forecast_source_id == target.forecast_source_id
            and row.outturn_source_id == target.outturn_source_id
            and row.forecast_methodology_version
            == target.forecast_methodology_version
            and row.outturn_methodology_version == target.outturn_methodology_version
            and row.issue_time_basis == target.issue_time_basis
            and row.effective_vintage_time_basis
            == target.effective_vintage_time_basis
            and row.unit == target.unit
            and row.revision >= 0
            and row.expected_sample_count >= row.verified_sample_count >= 0
            and 0 <= row.coverage_fraction <= 1
            and math.isclose(row.coverage_fraction, expected_coverage)
            and len(row.evidence_checksum) == 64
            and row.window_end > row.window_start
            and row.status in {
                PublicVerificationStatus.AVAILABLE.value,
                PublicVerificationStatus.INSUFFICIENT_DATA.value,
            }
            and row.reason in _PUBLIC_REASONS
            and _aware_datetime(row.window_start)
            and _aware_datetime(row.window_end)
            and _aware_datetime(row.computed_at)
            and (
                row.source_watermark_at is None
                or _aware_datetime(row.source_watermark_at)
            )
            and (
                row.status != PublicVerificationStatus.AVAILABLE.value
                or (
                    row.mae is not None
                    and row.bias is not None
                    and row.verified_sample_count >= MINIMUM_VERIFIED_SAMPLES
                    and row.coverage_fraction >= MINIMUM_COVERAGE_FRACTION
                )
            )
        )
        return (metric, horizon) if valid else None
    except (AttributeError, TypeError, ValueError):
        return None


def _present_item(
    target: Any,
    horizon: HorizonBucket,
    row: Any | None,
) -> ForecastVerificationItem:
    forecast = VerificationSource(
        source_id=target.forecast_source_id,
        dataset=target.forecast_dataset,
        methodology_version=target.forecast_methodology_version,
        fact_class="forecast",
    )
    outturn = VerificationSource(
        source_id=target.outturn_source_id,
        dataset=target.outturn_dataset,
        methodology_version=target.outturn_methodology_version,
        fact_class=(
            "estimated"
            if target.metric is VerificationMetric.NATIONAL_CARBON_INTENSITY
            else "observed"
        ),
    )
    base = {
        "metric": target.metric,
        "display_name": target.display_name,
        "unit": target.unit,
        "expected_interval_minutes": target.expected_interval_minutes,
        "horizon": HorizonDefinition(
            id=horizon.value,
            minimum_hours=horizon.minimum_hours,
            maximum_hours=horizon.maximum_hours,
        ),
        "issue_time_basis": target.issue_time_basis,
        "effective_vintage_time_basis": target.effective_vintage_time_basis,
        "forecast": forecast,
        "outturn": outturn,
        "registry_version": VERIFICATION_REGISTRY_VERSION,
        "verification_methodology_version": VERIFICATION_METHODOLOGY_VERSION,
    }
    if row is None:
        return ForecastVerificationItem(
            **base,
            status=PublicVerificationStatus.INSUFFICIENT_DATA,
            reason="not_computed",
            verified_samples=0,
            expected_samples=0,
            coverage=0,
        )
    eligible = (
        row.status == PublicVerificationStatus.AVAILABLE.value
        and row.verified_sample_count >= MINIMUM_VERIFIED_SAMPLES
        and row.coverage_fraction >= MINIMUM_COVERAGE_FRACTION
    )
    return ForecastVerificationItem(
        **base,
        status=(
            PublicVerificationStatus.AVAILABLE
            if eligible
            else PublicVerificationStatus.INSUFFICIENT_DATA
        ),
        reason=row.reason,
        mae=row.mae if eligible else None,
        bias=row.bias if eligible else None,
        wape_percent=row.wape_percent if eligible else None,
        verified_samples=row.verified_sample_count,
        expected_samples=row.expected_sample_count,
        coverage=row.coverage_fraction,
        verification_window=VerificationWindow(
            start=row.window_start,
            end=row.window_end,
        ),
        evidence_checksum=row.evidence_checksum,
        revision=row.revision,
        computed_at=row.computed_at,
        source_watermark_at=row.source_watermark_at,
    )


_PUBLIC_REASONS = {
    "eligible",
    "no_forecasts",
    "no_compatible_outturns",
    "sample_and_coverage_thresholds_not_met",
    "fewer_than_100_verified_samples",
    "coverage_below_90_percent",
}


def _aware_datetime(value: Any) -> bool:
    return bool(
        value is not None
        and getattr(value, "tzinfo", None) is not None
        and value.utcoffset() is not None
    )
