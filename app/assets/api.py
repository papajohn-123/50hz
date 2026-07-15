"""Public HTTP routes for source-located electricity generation sites."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.assets.api_models import (
    AssetDetailResponse,
    AssetLifecycle,
    AssetMapResponse,
)
from app.assets.repository import AssetCatalogRepository
from app.assets.service import AssetMapService, AssetNotFoundError
from app.db import DatabaseNotConfiguredError, get_session_factory


router = APIRouter(prefix="/v1/assets", tags=["assets"])


@lru_cache(maxsize=1)
def get_asset_map_service() -> AssetMapService:
    try:
        return AssetMapService(AssetCatalogRepository(get_session_factory()))
    except DatabaseNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        ) from error


Service = Annotated[AssetMapService, Depends(get_asset_map_service)]


@router.get(
    "/map",
    response_model=AssetMapResponse,
    summary="List bounded source-located REPD energy sites",
)
async def asset_map(
    service: Service,
    lifecycle: AssetLifecycle = Query(default=AssetLifecycle.OPERATIONAL),
    limit: int = Query(default=5_000, ge=1, le=5_000),
) -> AssetMapResponse:
    return await service.map_assets(lifecycle=lifecycle, limit=limit)


@router.get(
    "/{public_id}",
    response_model=AssetDetailResponse,
    summary="Inspect one source-located REPD site and linked Elexon evidence",
    responses={404: {"description": "Located energy site not found"}},
)
async def asset_detail(
    service: Service,
    public_id: str,
) -> AssetDetailResponse:
    try:
        return await service.asset_detail(public_id)
    except AssetNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Located energy site not found",
        ) from error
