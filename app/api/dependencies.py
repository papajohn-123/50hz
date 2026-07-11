from functools import lru_cache

from fastapi import HTTPException, status

from app.config import get_settings
from app.db import DatabaseNotConfiguredError, get_session_factory
from app.persistence import GridReadRepository


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

