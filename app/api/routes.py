from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_grid_read_repository
from app.api.models import (
    GridSnapshotResponse,
    GridTimelineResponse,
    SourceMetadataResponse,
)
from app.api.presenter import GridDataUnavailableError, present_current, present_timeline
from app.game.models import DailyGame
from app.game.service import build_daily_game
from app.persistence import GridReadRepository


router = APIRouter(prefix="/v1")
Repository = Annotated[GridReadRepository, Depends(get_grid_read_repository)]


@router.get("/grid/current", response_model=GridSnapshotResponse, tags=["grid"])
async def current_grid(repository: Repository) -> GridSnapshotResponse:
    try:
        return present_current(await repository.get_current())
    except GridDataUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
            headers={"Retry-After": "60"},
        ) from error


@router.get("/grid/timeline", response_model=GridTimelineResponse, tags=["grid"])
async def grid_timeline(
    repository: Repository,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    resolution: int = Query(default=1_800, ge=60, le=7_200),
) -> GridTimelineResponse:
    now = datetime.now(UTC)
    window_start = _aware_utc(from_ or now - timedelta(hours=24), "from")
    window_end = _aware_utc(to or now, "to")
    if window_end <= window_start:
        raise HTTPException(status_code=422, detail="to must be after from")
    if window_end - window_start > timedelta(hours=96):
        raise HTTPException(status_code=422, detail="timeline window cannot exceed 96 hours")
    read = await repository.get_timeline(
        window_start=window_start,
        window_end=window_end,
        resolution_seconds=resolution,
    )
    return present_timeline(read, now_boundary=min(now, window_end))


@router.get("/sources", response_model=list[SourceMetadataResponse], tags=["system"])
async def sources(repository: Repository) -> list[SourceMetadataResponse]:
    return [
        SourceMetadataResponse(
            id=source.id,
            publisher=source.provider,
            dataset=source.dataset,
            documentation_url=source.documentation_url,
            licence_url=source.licence_url,
            attribution=source.attribution or f"Data supplied by {source.display_name}.",
            expected_cadence_seconds=source.expected_cadence_seconds,
        )
        for source in await repository.list_sources()
    ]


@router.get("/game/today", response_model=DailyGame, tags=["game"])
async def daily_game(repository: Repository) -> DailyGame:
    now = datetime.now(UTC)
    try:
        current = await repository.get_current(as_of=now)
        required = [
            *(reading.provenance.observed_at for reading in current.generation),
            *(value.provenance.observed_at for value in (current.demand, current.carbon) if value),
        ]
        source_fresh = bool(required) and now - min(required) <= timedelta(minutes=30)
    except Exception:
        source_fresh = False
    return build_daily_game(
        now=now,
        source_fresh=source_fresh,
        has_forecast=False,
        has_events=False,
    )


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail=f"{name} must include a timezone")
    return value.astimezone(UTC)

