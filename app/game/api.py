"""Public API for auditable daily-prediction resolution."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import get_grid_read_repository
from app.config import get_settings
from app.db import DatabaseNotConfiguredError, get_session_factory
from app.game.connectors import connector_registry_for_date
from app.game.models import PredictionResolution, PredictionResolutionState
from app.game.resolution import build_prediction_resolution
from app.game.service import prediction_definition_for_date
from app.persistence import (
    GridReadRepository,
    PostgresPredictionResolutionLedger,
    PredictionResolutionLedger,
)


LONDON = ZoneInfo("Europe/London")
MAX_RESOLUTION_LOOKBACK_DAYS = 31

router = APIRouter(prefix="/v1/game")


@lru_cache(maxsize=1)
def get_prediction_resolution_ledger() -> PredictionResolutionLedger:
    if not get_settings().database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        )
    try:
        return PostgresPredictionResolutionLedger(get_session_factory())
    except DatabaseNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        ) from error


GridRepository = Annotated[GridReadRepository, Depends(get_grid_read_repository)]
ResolutionLedger = Annotated[
    PredictionResolutionLedger,
    Depends(get_prediction_resolution_ledger),
]


@router.get(
    "/{date}/resolution",
    response_model=PredictionResolution,
    tags=["game"],
    summary="Resolve one bounded daily grid prediction",
)
async def prediction_resolution(
    day: Annotated[date, Path(alias="date")],
    repository: GridRepository,
    ledger: ResolutionLedger,
) -> PredictionResolution:
    instant = _resolution_now()
    _validate_requested_date(day, as_of=instant)
    try:
        return await present_prediction_resolution(
            day,
            as_of=instant,
            repository=repository,
            ledger=ledger,
        )
    except (SQLAlchemyError, ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prediction evidence is temporarily unavailable",
            headers={"Retry-After": "60"},
        ) from error


async def present_prediction_resolution(
    day: date,
    *,
    as_of: datetime,
    repository: GridReadRepository,
    ledger: PredictionResolutionLedger,
) -> PredictionResolution:
    instant = _aware_utc(as_of)
    definition = prediction_definition_for_date(day)
    observations = ()
    if instant >= definition.resolves_to:
        observations = await repository.get_interconnector_observations(
            window_start=definition.resolves_from,
            window_end=definition.resolves_to,
            retrieved_before=instant,
            source_id="elexon.fuelinst",
        )
    candidate = build_prediction_resolution(
        day,
        as_of=instant,
        interconnectors=observations,
        connector_registry=connector_registry_for_date(day),
    )
    if candidate.state is PredictionResolutionState.PENDING:
        return candidate
    return await ledger.persist(candidate)


def _validate_requested_date(day: date, *, as_of: datetime) -> None:
    local_today = _aware_utc(as_of).astimezone(LONDON).date()
    oldest = local_today - timedelta(days=MAX_RESOLUTION_LOOKBACK_DAYS)
    if not oldest <= day <= local_today:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Prediction date must be between "
                f"{oldest.isoformat()} and {local_today.isoformat()}"
            ),
        )


def _resolution_now() -> datetime:
    # Match the endpoint's 60-second public cache contract so pending responses
    # do not churn their computed timestamp within a cache window.
    return datetime.now(UTC).replace(second=0, microsecond=0)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("prediction as_of must include a timezone")
    return value.astimezone(UTC)
