from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_grid_read_repository, get_regional_carbon_provider
from app.api.briefing import present_today_briefing
from app.api.metrics import present_metric_registry
from app.api.local_windows import (
    LocalWindowsUnavailableError,
    LocalWindowsValidationError,
    present_local_windows,
)
from app.api.models import (
    GridEvent,
    EventHistoryResponse,
    MetricRegistryResponse,
    MobileFreshness,
    GridSnapshotResponse,
    GridTimelineResponse,
    LocalWindowsResponse,
    RegionResponse,
    SourceMetadataResponse,
)
from app.briefing import Briefing
from app.api.notices import present_event_history, present_reported_notices
from app.api.presenter import (
    GridDataUnavailableError,
    aggregate_generation,
    present_current,
    present_timeline,
)
from app.api.regional import present_region
from app.game.models import DailyGame
from app.game.service import build_daily_game
from app.events.identity import is_stable_event_id
from app.persistence import GridReadRepository
from app.regions import RegionalCarbonProvider, RegionalDataUnavailableError
from app.sources.exceptions import SourceError
from app.sources.neso_carbon import normalize_outward_postcode


router = APIRouter(prefix="/v1")
Repository = Annotated[GridReadRepository, Depends(get_grid_read_repository)]
RegionalProvider = Annotated[
    RegionalCarbonProvider,
    Depends(get_regional_carbon_provider),
]


@router.get("/grid/current", response_model=GridSnapshotResponse, tags=["grid"])
async def current_grid(repository: Repository) -> GridSnapshotResponse:
    try:
        read = await repository.get_current()
        previous_at = read.requested_at - timedelta(hours=1)
        previous_generation = aggregate_generation(
            await repository.get_latest_generation(as_of=previous_at)
        )
        previous_net_import = sum(
            flow.megawatts
            for flow in await repository.get_latest_interconnectors(as_of=previous_at)
        )
        if previous_net_import > 0:
            previous_generation["imports"] = previous_net_import
        events = present_reported_notices(
            await repository.get_active_notices(as_of=read.requested_at)
        )
        return present_current(
            read,
            active_event=events[0] if events else None,
            previous_generation_mw=previous_generation,
        )
    except GridDataUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
            headers={"Retry-After": "60"},
        ) from error


@router.get(
    "/briefing/today",
    response_model=Briefing,
    tags=["briefing"],
)
async def today_briefing(repository: Repository) -> Briefing:
    return await present_today_briefing(
        repository,
        # Match the endpoint's 60-second cache contract so two reads in the
        # same public cache window have a stable representation and ETag.
        as_of=datetime.now(UTC).replace(second=0, microsecond=0),
    )


@router.get("/events", response_model=list[GridEvent], tags=["events"])
async def events(
    repository: Repository,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[GridEvent]:
    return present_reported_notices(await repository.get_active_notices())[:limit]


@router.get(
    "/events/{event_id}/history",
    response_model=EventHistoryResponse,
    tags=["events"],
    summary="Get reported event revision history",
    responses={404: {"description": "Reported event history not found"}},
)
async def event_history(
    event_id: str,
    repository: Repository,
    limit: int = Query(
        default=100,
        ge=1,
        le=100,
        description="Newest immutable lifecycle revisions to return",
    ),
) -> EventHistoryResponse:
    # Invalid and unknown IDs intentionally share one public response. The
    # stable ID is non-reversible and the query never falls back to active-only
    # notices, so terminal or otherwise inactive events remain resolvable.
    if not is_stable_event_id(event_id):
        raise HTTPException(status_code=404, detail="Event history not found")
    history = await repository.get_event_lifecycle_history(event_id, limit=limit)
    if history is None:
        raise HTTPException(status_code=404, detail="Event history not found")
    return present_event_history(history)


@router.get("/events/{event_id}", response_model=GridEvent, tags=["events"])
async def event_detail(event_id: str, repository: Repository) -> GridEvent:
    active = present_reported_notices(await repository.get_active_notices())
    event = next((item for item in active if item.id == event_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail="Active grid event not found")
    return event


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


@router.get(
    "/metadata/metrics",
    response_model=MetricRegistryResponse,
    tags=["metadata"],
)
async def metric_registry() -> MetricRegistryResponse:
    """Return stable definitions and methodology versions for public metrics."""

    return present_metric_registry()


@router.get(
    "/regions/{postcode}/windows",
    response_model=LocalWindowsResponse,
    tags=["regions"],
)
async def local_windows(
    postcode: str,
    repository: Repository,
    duration_minutes: int = Query(
        ...,
        alias="durationMinutes",
        ge=30,
        le=720,
        description="Continuous use duration in whole 30-minute intervals",
    ),
    earliest: datetime | None = Query(default=None),
    latest: datetime | None = Query(default=None),
    continuous: bool = Query(default=True),
) -> LocalWindowsResponse:
    try:
        # Normalize before any repository call. The service repeats this at its
        # boundary so direct callers receive the same privacy guarantee.
        normalized = normalize_outward_postcode(postcode)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        return await present_local_windows(
            repository,
            postcode=normalized,
            now=datetime.now(UTC),
            duration_minutes=duration_minutes,
            earliest=earliest,
            latest=latest,
            continuous=continuous,
        )
    except LocalWindowsValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LocalWindowsUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
            headers={"Retry-After": "300"},
        ) from error


@router.get("/regions/{postcode}", response_model=RegionResponse, tags=["regions"])
async def region(
    postcode: str,
    repository: Repository,
    provider: RegionalProvider,
    charging_duration_minutes: int = Query(
        default=60,
        alias="chargingDurationMinutes",
        ge=30,
        le=720,
    ),
) -> RegionResponse:
    try:
        # Validate before touching the database or upstream service so malformed
        # user input is always a 422, never an apparent source failure.
        normalized = normalize_outward_postcode(postcode)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        return await present_region(
            repository,
            provider,
            postcode=normalized,
            now=datetime.now(UTC),
            charging_duration=timedelta(minutes=charging_duration_minutes),
        )
    except SourceError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Regional carbon source is temporarily unavailable",
        ) from error
    except RegionalDataUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
            headers={"Retry-After": "300"},
        ) from error


@router.get("/game/today", response_model=DailyGame, tags=["game"])
async def daily_game(repository: Repository) -> DailyGame:
    now = datetime.now(UTC)
    try:
        current = await repository.get_current(as_of=now)
        notices = await repository.get_active_notices(as_of=now)
        events = present_reported_notices(notices)
        snapshot = present_current(
            current,
            active_event=events[0] if events else None,
        )
        source_fresh = snapshot.freshness not in {
            MobileFreshness.STALE,
            MobileFreshness.OFFLINE,
        }
        forecasts = await repository.get_carbon_forecast(
            region_code="GB",
            window_start=now,
            window_end=now + timedelta(hours=48),
            issued_before=now,
        )
    except Exception:
        source_fresh = False
        events = []
        forecasts = ()
    return build_daily_game(
        now=now,
        source_fresh=source_fresh,
        has_forecast=bool(forecasts),
        has_events=bool(events),
    )


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail=f"{name} must include a timezone")
    return value.astimezone(UTC)
