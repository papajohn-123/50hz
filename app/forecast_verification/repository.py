"""Bounded reads for reviewed forecast verification inputs and public results."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    CarbonObservation,
    DemandObservation,
    ForecastObservation,
    ForecastVerificationResult,
    GenerationObservation,
)
from app.domain.enums import FactQuality
from app.forecast_contract import CAPTURE_TIME_ISSUE_BASIS
from app.forecast_verification.core import (
    VERIFICATION_METHODOLOGY_VERSION,
    VERIFICATION_REGISTRY_VERSION,
    ForecastEvidence,
    OutturnEvidence,
    VerificationMetric,
    VerificationTarget,
)


SessionFactory = Callable[[], AsyncSession]
MAX_FORECAST_INPUT_ROWS = 250_000
MAX_OUTTURN_INPUT_ROWS = 60_000
MAX_VERIFICATION_INPUT_WINDOW = timedelta(days=32)


class VerificationInputLimitError(RuntimeError):
    """Raised instead of silently truncating a bounded operator calculation."""


class ForecastVerificationInputRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self.session_factory = session_factory

    async def load(
        self,
        target: VerificationTarget,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[tuple[ForecastEvidence, ...], tuple[OutturnEvidence, ...]]:
        start = _aware(window_start, "window_start")
        end = _aware(window_end, "window_end")
        if end <= start or end - start > MAX_VERIFICATION_INPUT_WINDOW:
            raise ValueError("verification input window must be positive and bounded")
        async with self.session_factory() as session:
            forecast_rows = list(
                (
                    await session.execute(
                        _forecast_statement(target, start=start, end=end)
                    )
                )
                .scalars()
                .all()
            )
            if len(forecast_rows) > MAX_FORECAST_INPUT_ROWS:
                raise VerificationInputLimitError(
                    "forecast verification input exceeded its reviewed row bound"
                )
            outturn_rows = list(
                (
                    await session.execute(
                        _outturn_statement(target, start=start, end=end)
                    )
                )
                .scalars()
                .all()
            )
            if len(outturn_rows) > MAX_OUTTURN_INPUT_ROWS:
                raise VerificationInputLimitError(
                    "forecast outturn input exceeded its reviewed row bound"
                )
        forecasts = tuple(
            _map_forecast(row)
            for row in forecast_rows
            if _compatible_forecast(row, target)
        )
        outturns = tuple(
            _map_outturn(row, target)
            for row in outturn_rows
            if _compatible_outturn(row, target)
        )
        return forecasts, outturns


class ForecastVerificationReadRepository:
    """Read only the newest current-contract result for each public bucket."""

    def __init__(self, session_factory: SessionFactory) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self.session_factory = session_factory

    async def latest(
        self,
        metric: VerificationMetric | None = None,
    ) -> tuple[ForecastVerificationResult, ...]:
        model = ForecastVerificationResult
        rank = func.row_number().over(
            partition_by=(model.metric_id, model.horizon_bucket),
            order_by=(
                model.window_end.desc(),
                model.computed_at.desc(),
                model.revision.desc(),
                model.id.desc(),
            ),
        ).label("verification_rank")
        conditions = (
            model.registry_version == VERIFICATION_REGISTRY_VERSION,
            model.verification_methodology_version
            == VERIFICATION_METHODOLOGY_VERSION,
        )
        if metric is not None:
            conditions += (model.metric_id == metric.value,)
        ranked = select(model, rank).where(*conditions).subquery()
        latest = aliased(model, ranked)
        statement = (
            select(latest)
            .where(ranked.c.verification_rank == 1)
            .order_by(latest.metric_id.asc(), latest.horizon_bucket.asc())
            .limit(12)
        )
        async with self.session_factory() as session:
            return tuple((await session.execute(statement)).scalars().all())


def _forecast_statement(
    target: VerificationTarget,
    *,
    start: datetime,
    end: datetime,
) -> Select[Any]:
    model = ForecastObservation
    metric_type, series_key = {
        VerificationMetric.NATIONAL_DEMAND: ("demand", "n"),
        VerificationMetric.WIND_GENERATION: ("generation", "wind"),
        VerificationMetric.NATIONAL_CARBON_INTENSITY: ("carbon_intensity", "GB"),
    }[target.metric]
    return (
        select(model)
        .where(
            model.source_id == target.forecast_source_id,
            model.metric_type == metric_type,
            func.lower(model.series_key) == series_key.lower(),
            model.variant == "point",
            model.valid_from >= start,
            model.valid_from < end,
            model.issued_at >= start - timedelta(hours=48),
            model.issued_at <= end,
        )
        .order_by(
            model.valid_from.asc(),
            model.issued_at.asc(),
            model.revision.asc(),
            model.id.asc(),
        )
        .limit(MAX_FORECAST_INPUT_ROWS + 1)
    )


def _outturn_statement(
    target: VerificationTarget,
    *,
    start: datetime,
    end: datetime,
) -> Select[Any]:
    if target.metric is VerificationMetric.NATIONAL_DEMAND:
        model = DemandObservation
        return (
            select(model)
            .where(
                model.source_id == target.outturn_source_id,
                model.series_key == "gb",
                model.demand_type == "indo",
                model.quality.in_((FactQuality.VALIDATED, FactQuality.PROVISIONAL)),
                model.observed_at >= start,
                model.observed_at < end,
            )
            .order_by(model.observed_at, model.revision, model.id)
            .limit(MAX_OUTTURN_INPUT_ROWS + 1)
        )
    if target.metric is VerificationMetric.WIND_GENERATION:
        model = GenerationObservation
        return (
            select(model)
            .where(
                model.source_id == target.outturn_source_id,
                model.series_key == "WIND",
                model.fuel_type == "wind",
                model.quality.in_((FactQuality.VALIDATED, FactQuality.PROVISIONAL)),
                model.observed_at >= start,
                model.observed_at < end,
            )
            .order_by(model.observed_at, model.revision, model.id)
            .limit(MAX_OUTTURN_INPUT_ROWS + 1)
        )
    if target.metric is VerificationMetric.NATIONAL_CARBON_INTENSITY:
        model = CarbonObservation
        return (
            select(model)
            .where(
                model.source_id == target.outturn_source_id,
                model.region_code == "GB",
                model.quality == FactQuality.ESTIMATED,
                model.observed_at >= start,
                model.observed_at < end,
            )
            .order_by(model.observed_at, model.revision, model.id)
            .limit(MAX_OUTTURN_INPUT_ROWS + 1)
        )
    raise ValueError("unreviewed verification target")


def _compatible_forecast(row: Any, target: VerificationTarget) -> bool:
    if not isinstance(row, ForecastObservation):
        return False
    attributes = row.attributes if isinstance(row.attributes, dict) else {}
    common = (
        row.source_id == target.forecast_source_id
        and row.variant == "point"
        and str(attributes.get("classification", "")).casefold() == "forecast"
        and row.revision >= 0
        and math.isfinite(row.value)
        and row.value >= 0
    )
    if not common:
        return False
    if target.metric is VerificationMetric.NATIONAL_DEMAND:
        return (
            row.metric_type == "demand"
            and row.series_key.casefold() == "n"
            and row.unit.casefold() == "mw"
            and str(attributes.get("dataset", "")).casefold() == "ndf"
            and str(attributes.get("boundary", "")).casefold() == "n"
            and row.published_at is not None
            and row.published_at == row.issued_at
        )
    if target.metric is VerificationMetric.WIND_GENERATION:
        return (
            row.metric_type == "generation"
            and row.series_key.casefold() == "wind"
            and row.unit.casefold() == "mw"
            and str(attributes.get("dataset", "")).casefold() == "windfor"
            and str(attributes.get("fuelType", "")).casefold() == "wind"
            and row.published_at is not None
            and row.published_at == row.issued_at
        )
    return (
        row.metric_type == "carbon_intensity"
        and row.series_key.casefold() == "gb"
        and row.unit.casefold().replace("₂", "2") == "gco2/kwh"
        and row.valid_to is not None
        and row.valid_to > row.valid_from
        and str(attributes.get("dataset", "")).casefold()
        == "carbon_intensity_national"
        and str(attributes.get("issueTimeBasis", "")).casefold()
        == CAPTURE_TIME_ISSUE_BASIS
        and row.published_at is None
        and row.issued_at == row.retrieved_at
    )


def _compatible_outturn(row: Any, target: VerificationTarget) -> bool:
    value: float | None = None
    compatible = False
    if target.metric is VerificationMetric.NATIONAL_DEMAND:
        compatible = (
            isinstance(row, DemandObservation)
            and row.source_id == target.outturn_source_id
            and row.series_key == "gb"
            and row.demand_type == "indo"
            and row.quality in (FactQuality.VALIDATED, FactQuality.PROVISIONAL)
        )
        value = row.demand_mw if compatible else None
    elif target.metric is VerificationMetric.WIND_GENERATION:
        compatible = (
            isinstance(row, GenerationObservation)
            and row.source_id == target.outturn_source_id
            and row.series_key == "WIND"
            and row.fuel_type == "wind"
            and row.quality in (FactQuality.VALIDATED, FactQuality.PROVISIONAL)
        )
        value = row.generation_mw if compatible else None
    elif target.metric is VerificationMetric.NATIONAL_CARBON_INTENSITY:
        compatible = (
            isinstance(row, CarbonObservation)
            and row.source_id == target.outturn_source_id
            and row.region_code == "GB"
            and row.quality == FactQuality.ESTIMATED
        )
        value = row.intensity_gco2_kwh if compatible else None
    return bool(
        compatible
        and row.revision >= 0
        and value is not None
        and math.isfinite(value)
        and value >= 0
    )


def _map_forecast(row: ForecastObservation) -> ForecastEvidence:
    issued_at = _aware(row.issued_at, "issued_at")
    captured_at = _aware(row.retrieved_at, "retrieved_at")
    return ForecastEvidence(
        observation_id=row.id,
        valid_from=_aware(row.valid_from, "valid_from"),
        issued_at=issued_at,
        captured_at=captured_at,
        value=float(row.value),
        revision=row.revision,
    )


def _map_outturn(row: Any, target: VerificationTarget) -> OutturnEvidence:
    value = {
        VerificationMetric.NATIONAL_DEMAND: lambda item: item.demand_mw,
        VerificationMetric.WIND_GENERATION: lambda item: item.generation_mw,
        VerificationMetric.NATIONAL_CARBON_INTENSITY: lambda item: item.intensity_gco2_kwh,
    }[target.metric](row)
    return OutturnEvidence(
        observation_id=row.id,
        observed_at=_aware(row.observed_at, "observed_at"),
        retrieved_at=_aware(row.retrieved_at, "retrieved_at"),
        value=float(value),
        revision=row.revision,
    )


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(UTC)
