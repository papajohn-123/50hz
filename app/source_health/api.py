from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_grid_read_repository
from app.api.status import present_data_status
from app.config import get_settings
from app.db import DatabaseNotConfiguredError, get_session_factory
from app.persistence import GridReadRepository
from app.source_health.models import SourceHealthResponse
from app.source_health.repository import SourceHealthRepository
from app.source_health.service import build_source_health


router = APIRouter(prefix="/v1")


@lru_cache(maxsize=1)
def get_source_health_repository() -> SourceHealthRepository:
    if not get_settings().database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        )
    try:
        return SourceHealthRepository(get_session_factory())
    except DatabaseNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        ) from error


HealthRepository = Annotated[
    SourceHealthRepository,
    Depends(get_source_health_repository),
]
GridRepository = Annotated[GridReadRepository, Depends(get_grid_read_repository)]


@router.get(
    "/sources/status",
    response_model=SourceHealthResponse,
    tags=["system"],
)
async def source_status(
    health_repository: HealthRepository,
    grid_repository: GridRepository,
) -> SourceHealthResponse:
    now = datetime.now(UTC)
    sources, runs = await health_repository.load()
    fact_statuses = []
    try:
        fact_statuses = present_data_status(
            await grid_repository.get_current(as_of=now)
        )
    except Exception:
        # Source delivery remains inspectable when current fact composition is
        # unavailable; affected fact states are explicitly marked unavailable.
        pass
    return build_source_health(
        sources,
        runs,
        evaluated_at=now,
        current_fact_statuses=fact_statuses,
    )
