from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import ValidationError

from app.config import get_settings
from app.db import DatabaseNotConfiguredError, get_session_factory
from app.exporting.models import (
    ExportFormat,
    ExportMetricSchema,
    ExportRequest,
    ExportResponse,
    ExportSchemaResponse,
)
from app.exporting.service import build_export, render_csv
from app.history.repository import (
    FUEL_TYPES,
    INTERCONNECTOR_NAMES,
    HistoryMetric,
    NormalizedHistoryRepository,
)


router = APIRouter(prefix="/v1")


@lru_cache(maxsize=1)
def get_export_history_repository() -> NormalizedHistoryRepository:
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


ExportRepository = Annotated[
    NormalizedHistoryRepository,
    Depends(get_export_history_repository),
]


@router.get(
    "/metadata/export-schema",
    response_model=ExportSchemaResponse,
    tags=["metadata"],
)
async def export_schema() -> ExportSchemaResponse:
    return ExportSchemaResponse(
        metrics=[
            ExportMetricSchema(
                metric=HistoryMetric.NATIONAL_CARBON,
                selector_required=False,
            ),
            ExportMetricSchema(
                metric=HistoryMetric.NATIONAL_DEMAND,
                selector_required=False,
            ),
            ExportMetricSchema(
                metric=HistoryMetric.GENERATION_FUEL,
                selector_required=True,
                allowed_selectors=sorted(FUEL_TYPES),
            ),
            ExportMetricSchema(
                metric=HistoryMetric.INTERCONNECTOR_FLOW,
                selector_required=True,
                allowed_selectors=sorted(INTERCONNECTOR_NAMES),
            ),
        ]
    )


@router.get("/export", response_model=None, tags=["export"])
async def export_data(
    repository: ExportRepository,
    metric: HistoryMetric,
    from_: datetime = Query(alias="from"),
    to: datetime = Query(),
    selector: str | None = Query(default=None),
    resolution: int = Query(default=1_800),
    format_: ExportFormat = Query(default=ExportFormat.JSON, alias="format"),
) -> ExportResponse | Response:
    try:
        request = ExportRequest(
            metric=metric,
            start=from_,
            end=to,
            selector=selector,
            resolution_seconds=resolution,
            format=format_,
        )
        result = await build_export(repository, request)
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {
                    "loc": list(item["loc"]),
                    "msg": item["msg"],
                    "type": item["type"],
                }
                for item in error.errors()
            ],
        ) from error

    if format_ is ExportFormat.JSON:
        return result

    metric_slug = result.metric_id.replace(".", "-")
    body = render_csv(result).encode("utf-8")
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="50hz-{metric_slug}.csv"',
            "X-50Hz-Expected-Rows": str(result.coverage.expected_interval_count),
            "X-50Hz-Missing-Rows": str(result.coverage.missing_interval_count),
        },
    )
