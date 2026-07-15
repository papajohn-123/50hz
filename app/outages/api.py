from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db import DatabaseNotConfiguredError, get_session_factory
from app.outages.models import (
    CurrentOutagesResponse,
    DistributionIncidentResponse,
    OutageCheckRequest,
    OutageCheckResponse,
    OutageDeliveryState,
    OutageLocation,
    OutageSourceStatus,
)
from app.outages.repository import (
    DistributionIncidentRead,
    OutageSnapshotRead,
    OutageSnapshotRepository,
)


router = APIRouter(prefix="/v1/outages", tags=["outages"])
_DISCLAIMER = (
    "This feed reports aggregated incidents in UK Power Networks' licence areas. "
    "A postcode-district match does not confirm whether any particular property "
    "is affected. The near-real-time source may be partially complete."
)


@lru_cache(maxsize=1)
def get_outage_repository() -> OutageSnapshotRepository:
    try:
        return OutageSnapshotRepository(get_session_factory())
    except DatabaseNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Grid database is not configured",
        ) from error


Repository = Annotated[
    OutageSnapshotRepository,
    Depends(get_outage_repository),
]


@router.get(
    "/current",
    response_model=CurrentOutagesResponse,
    summary="List bounded current UKPN distribution incidents",
)
async def current_outages(
    repository: Repository,
    include_restored: bool = Query(default=False, alias="includeRestored"),
    limit: int = Query(default=50, ge=1, le=100),
) -> CurrentOutagesResponse:
    evaluated_at = datetime.now(UTC)
    snapshot = await repository.load_current(
        include_restored=include_restored,
        hard_limit=limit,
    )
    incidents = [_present_incident(item) for item in snapshot.incidents]
    return CurrentOutagesResponse(
        evaluated_at=evaluated_at,
        source_status=_source_status(snapshot, evaluated_at=evaluated_at),
        total_count=snapshot.matching_record_count,
        returned_count=len(incidents),
        is_truncated=snapshot.matching_record_count > len(incidents),
        incidents=incidents,
        disclaimer=_DISCLAIMER,
    )


@router.post(
    "/check",
    response_model=OutageCheckResponse,
    summary="Check reported incidents in an outward postcode district",
)
async def check_outward_code(
    request: OutageCheckRequest,
    repository: Repository,
) -> OutageCheckResponse:
    evaluated_at = datetime.now(UTC)
    snapshot = await repository.load_current(
        include_restored=request.include_restored,
    )
    matches = [
        incident
        for incident in snapshot.incidents
        if request.outward_code in incident.outward_codes
    ]
    presented = [_present_incident(item) for item in matches[: request.limit]]
    total_count = len(matches)
    if total_count:
        match_statement = (
            f"UK Power Networks reports {total_count} incident"
            f"{'s' if total_count != 1 else ''} covering one or more postcode "
            f"sectors in {request.outward_code}. Household impact remains unknown."
        )
    else:
        match_statement = (
            f"No incident in the latest UK Power Networks snapshot lists a "
            f"postcode sector in {request.outward_code}. This is not confirmation "
            "that a property has power."
        )
    return OutageCheckResponse(
        evaluated_at=evaluated_at,
        outward_code=request.outward_code,
        district_has_reported_incidents=bool(total_count),
        match_statement=match_statement,
        source_status=_source_status(snapshot, evaluated_at=evaluated_at),
        total_count=total_count,
        returned_count=len(presented),
        is_truncated=total_count > len(presented),
        incidents=presented,
        disclaimer=_DISCLAIMER,
    )


def _source_status(
    snapshot: OutageSnapshotRead,
    *,
    evaluated_at: datetime,
) -> OutageSourceStatus:
    last_success = snapshot.last_successful_at
    if last_success is None:
        state = OutageDeliveryState.UNAVAILABLE
        age = None
    else:
        if last_success.tzinfo is None or last_success.utcoffset() is None:
            raise ValueError("outage source delivery time must be timezone-aware")
        age = max(
            0,
            int(
                (
                    evaluated_at - last_success.astimezone(UTC)
                ).total_seconds()
            ),
        )
        if age <= 600:
            state = OutageDeliveryState.HEALTHY
        elif age < 1_800:
            state = OutageDeliveryState.DELAYED
        else:
            state = OutageDeliveryState.STALE
    return OutageSourceStatus(
        delivery_state=state,
        evaluated_at=evaluated_at,
        last_successful_at=last_success,
        delivery_age_seconds=age,
        records_in_latest_snapshot=snapshot.snapshot_record_count,
        empty_snapshot=(
            last_success is not None and snapshot.snapshot_record_count == 0
        ),
    )


def _present_incident(
    incident: DistributionIncidentRead,
) -> DistributionIncidentResponse:
    location = (
        OutageLocation(
            latitude=incident.latitude,
            longitude=incident.longitude,
            precision="aggregated_incident_point",
        )
        if incident.latitude is not None and incident.longitude is not None
        else None
    )
    return DistributionIncidentResponse(
        id=_public_incident_id(incident.incident_reference),
        incident_reference=incident.incident_reference,
        revision=incident.revision,
        status=incident.status,
        lifecycle_status=(
            "restored" if incident.status == "restored" else "active"
        ),
        customers_affected=incident.customers_affected,
        calls_reported=incident.calls_reported,
        postcode_sectors=list(incident.postcode_sectors),
        geography_precision=incident.geography_precision,
        operating_zone=incident.operating_zone,
        location=location,
        official_message=incident.official_summary,
        official_details=incident.official_details,
        restoration_window_text=incident.restoration_window_text,
        incident_category=incident.incident_category,
        source_created_at=incident.source_created_at,
        observed_at=incident.observed_at,
        first_retrieved_at=incident.retrieved_at,
        last_seen_at=incident.last_seen_at,
        incident_start=incident.incident_start,
        restored_at=incident.restored_at,
        estimated_restoration_at=incident.estimated_restoration_at,
    )


def _public_incident_id(reference: str) -> str:
    digest = hashlib.sha256(f"ukpn:{reference}".encode()).hexdigest()[:20]
    return f"dno_{digest}"
