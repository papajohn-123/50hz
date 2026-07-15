from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DistributionIncidentCurrent,
    DistributionIncidentRevision,
    IngestionRun,
)
from app.domain.enums import IngestionRunStatus


SessionFactory = Callable[[], AsyncSession]
UKPN_SOURCE_ID = "ukpn.live-faults"
UKPN_JOB_ID = "ukpn.live_faults"
MAX_CURRENT_QUERY_ROWS = 501


@dataclass(frozen=True, slots=True)
class DistributionIncidentRead:
    incident_reference: str
    revision: int
    status: str
    status_id: int | None
    source_created_at: datetime | None
    observed_at: datetime
    retrieved_at: datetime
    incident_start: datetime | None
    restored_at: datetime | None
    estimated_restoration_at: datetime | None
    customers_affected: int
    calls_reported: int
    postcode_sectors: tuple[str, ...]
    outward_codes: tuple[str, ...]
    latitude: float | None
    longitude: float | None
    geography_precision: str
    operating_zone: str | None
    official_summary: str | None
    official_details: str | None
    restoration_window_text: str | None
    incident_category: str | None
    content_sha256: str
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class OutageSnapshotRead:
    incidents: tuple[DistributionIncidentRead, ...]
    matching_record_count: int
    snapshot_record_count: int
    last_successful_at: datetime | None


class OutageSnapshotRepository:
    """Read latest immutable revisions constrained to the latest source snapshot."""

    def __init__(self, session_factory: SessionFactory) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self._session_factory = session_factory

    async def load_current(
        self,
        *,
        include_restored: bool,
        hard_limit: int = MAX_CURRENT_QUERY_ROWS,
    ) -> OutageSnapshotRead:
        if not 1 <= hard_limit <= MAX_CURRENT_QUERY_ROWS:
            raise ValueError(
                f"hard_limit must be between 1 and {MAX_CURRENT_QUERY_ROWS}"
            )
        async with self._session_factory() as session:
            incident_rows = (
                await session.execute(
                    _current_incidents_statement(
                        include_restored=include_restored,
                        limit=hard_limit,
                    )
                )
            ).all()
            matching_count = int(
                (
                    await session.execute(
                        _current_count_statement(
                            include_restored=include_restored,
                        )
                    )
                ).scalar_one()
            )
            snapshot_count = int(
                (
                    await session.execute(
                        _current_count_statement(include_restored=True)
                    )
                ).scalar_one()
            )
            last_success = (
                await session.execute(_latest_success_statement())
            ).scalars().first()

        return OutageSnapshotRead(
            incidents=tuple(
                _map_incident(row, last_seen_at)
                for row, last_seen_at in incident_rows
            ),
            matching_record_count=matching_count,
            snapshot_record_count=snapshot_count,
            last_successful_at=(
                last_success.completed_at if last_success is not None else None
            ),
        )


def _latest_revision_subquery():
    return (
        select(
            DistributionIncidentRevision.id.label("revision_id"),
            func.row_number()
            .over(
                partition_by=(
                    DistributionIncidentRevision.source_id,
                    DistributionIncidentRevision.incident_reference,
                ),
                order_by=(
                    DistributionIncidentRevision.revision.desc(),
                    DistributionIncidentRevision.created_at.desc(),
                    DistributionIncidentRevision.id.desc(),
                ),
            )
            .label("revision_rank"),
        )
        .where(DistributionIncidentRevision.source_id == UKPN_SOURCE_ID)
        .subquery()
    )


def _current_incidents_statement(*, include_restored: bool, limit: int):
    latest = _latest_revision_subquery()
    statement = (
        select(
            DistributionIncidentRevision,
            DistributionIncidentCurrent.last_seen_at,
        )
        .join(latest, latest.c.revision_id == DistributionIncidentRevision.id)
        .join(
            DistributionIncidentCurrent,
            (
                DistributionIncidentCurrent.source_id
                == DistributionIncidentRevision.source_id
            )
            & (
                DistributionIncidentCurrent.incident_reference
                == DistributionIncidentRevision.incident_reference
            ),
        )
        .where(
            latest.c.revision_rank == 1,
            DistributionIncidentCurrent.present.is_(True),
            DistributionIncidentCurrent.source_id == UKPN_SOURCE_ID,
        )
    )
    if not include_restored:
        statement = statement.where(
            DistributionIncidentRevision.status.in_(("planned", "unplanned"))
        )
    return statement.order_by(
        case(
            (DistributionIncidentRevision.status == "unplanned", 0),
            (DistributionIncidentRevision.status == "planned", 1),
            else_=2,
        ),
        DistributionIncidentRevision.customers_affected.desc(),
        DistributionIncidentRevision.observed_at.desc(),
        DistributionIncidentRevision.incident_reference,
    ).limit(limit)


def _current_count_statement(*, include_restored: bool):
    statement = select(func.count(DistributionIncidentCurrent.id)).where(
        DistributionIncidentCurrent.source_id == UKPN_SOURCE_ID,
        DistributionIncidentCurrent.present.is_(True),
    )
    if not include_restored:
        statement = statement.where(
            DistributionIncidentCurrent.status.in_(("planned", "unplanned"))
        )
    return statement


def _latest_success_statement():
    return (
        select(IngestionRun)
        .where(
            IngestionRun.adapter == UKPN_JOB_ID,
            IngestionRun.status == IngestionRunStatus.SUCCEEDED,
            IngestionRun.completed_at.is_not(None),
        )
        .order_by(
            IngestionRun.completed_at.desc(),
            IngestionRun.started_at.desc(),
            IngestionRun.id.desc(),
        )
        .limit(1)
    )


def _map_incident(
    row: DistributionIncidentRevision,
    last_seen_at: datetime,
) -> DistributionIncidentRead:
    return DistributionIncidentRead(
        incident_reference=row.incident_reference,
        revision=row.revision,
        status=row.status,
        status_id=row.status_id,
        source_created_at=row.source_created_at,
        observed_at=row.observed_at,
        retrieved_at=row.retrieved_at,
        incident_start=row.incident_start,
        restored_at=row.restored_at,
        estimated_restoration_at=row.estimated_restoration_at,
        customers_affected=row.customers_affected,
        calls_reported=row.calls_reported,
        postcode_sectors=tuple(row.postcode_sectors or ()),
        outward_codes=tuple(row.outward_codes or ()),
        latitude=row.latitude,
        longitude=row.longitude,
        geography_precision=row.geography_precision,
        operating_zone=row.operating_zone,
        official_summary=row.official_summary,
        official_details=row.official_details,
        restoration_window_text=row.restoration_window_text,
        incident_category=row.incident_category,
        content_sha256=row.content_sha256,
        last_seen_at=last_seen_at,
    )
