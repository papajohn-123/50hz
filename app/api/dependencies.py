from functools import lru_cache

from fastapi import HTTPException, status

from app.config import get_settings
from app.db import DatabaseNotConfiguredError, get_session_factory
from app.history.repository import NormalizedHistoryRepository
from app.persistence import GridReadRepository
from app.regions import OnDemandRegionalCarbonProvider, RegionalCarbonProvider


@lru_cache(maxsize=1)
def get_grid_read_repository() -> GridReadRepository:
    if not get_settings().database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        )
    try:
        return GridReadRepository(get_session_factory())
    except DatabaseNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        ) from error


@lru_cache(maxsize=1)
def get_history_repository() -> NormalizedHistoryRepository:
    if not get_settings().database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        )
    try:
        return NormalizedHistoryRepository(get_session_factory())
    except DatabaseNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        ) from error


@lru_cache(maxsize=1)
def get_regional_carbon_provider() -> RegionalCarbonProvider:
    settings = get_settings()
    return OnDemandRegionalCarbonProvider(
        base_url=settings.carbon_intensity_base_url,
        timeout_seconds=5.0,
    )
