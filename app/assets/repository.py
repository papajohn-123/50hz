"""Bounded reads for the source-backed generator catalogue and evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Asset,
    B1610SettledEnergyRevision,
    IngestionRun,
    PhysicalNotificationSegmentCurrent,
)
from app.domain.enums import IngestionRunStatus


SessionFactory = Callable[[], AsyncSession]
REPD_SOURCE_ID = "desnz.repd"
BM_UNIT_REFERENCE_SOURCE_ID = "elexon.bm-unit-reference"
ASSET_SOURCE_IDS = (REPD_SOURCE_ID, BM_UNIT_REFERENCE_SOURCE_ID)
ASSET_JOB_IDS = (
    REPD_SOURCE_ID,
    BM_UNIT_REFERENCE_SOURCE_ID,
    "elexon.pn",
    "elexon.b1610",
)


@dataclass(frozen=True, slots=True)
class StoredAssetRead:
    id: UUID
    source_id: str
    external_id: str
    asset_type: str
    display_name: str
    fuel_type: str | None
    region_code: str | None
    counterparty: str | None
    capacity_mw: float | None
    latitude: float | None
    longitude: float | None
    active: bool
    attributes: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssetCatalogRead:
    repd_sites: tuple[StoredAssetRead, ...]
    bm_units: tuple[StoredAssetRead, ...]
    latest_successes: Mapping[str, datetime]


@dataclass(frozen=True, slots=True)
class PlannedSegmentRead:
    asset_id: UUID
    national_grid_bm_unit: str | None
    elexon_bm_unit: str | None
    settlement_date: str
    settlement_period: int
    segment_start: datetime
    segment_end: datetime
    level_from_mw: float
    level_to_mw: float
    retrieved_at: datetime

    def level_at(self, instant: datetime) -> float | None:
        instant = _utc(instant, "instant")
        if not self.segment_start <= instant < self.segment_end:
            return None
        duration = (self.segment_end - self.segment_start).total_seconds()
        if duration <= 0:
            return None
        fraction = (instant - self.segment_start).total_seconds() / duration
        return self.level_from_mw + fraction * (
            self.level_to_mw - self.level_from_mw
        )


@dataclass(frozen=True, slots=True)
class SettledEnergyRead:
    asset_id: UUID
    national_grid_bm_unit: str | None
    elexon_bm_unit: str | None
    settlement_date: str
    settlement_period: int
    interval_start: datetime
    interval_end: datetime
    energy_mwh: float
    average_mw: float
    psr_type: str | None
    retrieved_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class AssetEvidenceRead:
    plans: tuple[PlannedSegmentRead, ...]
    settled: tuple[SettledEnergyRead, ...]


class AssetCatalogRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self._session_factory = session_factory

    async def load_catalog(self) -> AssetCatalogRead:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Asset)
                    .where(
                        Asset.source_id.in_(ASSET_SOURCE_IDS),
                        Asset.active.is_(True),
                    )
                    .order_by(Asset.source_id, Asset.external_id)
                )
            ).scalars().all()
            success_rows = (await session.execute(_latest_successes_statement())).all()

        mapped = tuple(_map_asset(row) for row in rows)
        return AssetCatalogRead(
            repd_sites=tuple(
                row
                for row in mapped
                if row.source_id == REPD_SOURCE_ID and row.asset_type == "repd_site"
            ),
            bm_units=tuple(
                row
                for row in mapped
                if row.source_id == BM_UNIT_REFERENCE_SOURCE_ID
                and row.asset_type == "bm_unit"
            ),
            latest_successes={
                str(adapter): completed_at
                for adapter, completed_at in success_rows
                if completed_at is not None
            },
        )

    async def load_evidence(
        self,
        national_grid_bm_units: Sequence[str] = (),
        *,
        asset_ids: Sequence[UUID] = (),
        elexon_bm_units: Sequence[str] = (),
        evaluated_at: datetime,
        settled_per_unit: int,
    ) -> AssetEvidenceRead:
        evaluated_at = _utc(evaluated_at, "evaluated_at")
        if not 1 <= settled_per_unit <= 48:
            raise ValueError("settled_per_unit must be between 1 and 48")
        national_ids = _normalized_text_ids(
            national_grid_bm_units,
            field_name="National Grid BM-unit IDs",
        )
        elexon_ids = _normalized_text_ids(
            elexon_bm_units,
            field_name="Elexon BM-unit IDs",
        )
        if any(not isinstance(value, UUID) for value in asset_ids):
            raise TypeError("asset IDs must be UUID values")
        normalized_asset_ids = tuple(dict.fromkeys(asset_ids))
        if not normalized_asset_ids and not national_ids and not elexon_ids:
            return AssetEvidenceRead(plans=(), settled=())
        if len(normalized_asset_ids) + len(national_ids) + len(elexon_ids) > 500:
            raise ValueError("asset evidence query cannot exceed 500 BM units")

        plan_scope = _evidence_scope(
            PhysicalNotificationSegmentCurrent,
            asset_ids=normalized_asset_ids,
            national_grid_bm_units=national_ids,
            elexon_bm_units=elexon_ids,
        )

        async with self._session_factory() as session:
            plan_rows = (
                await session.execute(
                    select(PhysicalNotificationSegmentCurrent)
                    .where(
                        plan_scope,
                        PhysicalNotificationSegmentCurrent.segment_start
                        <= evaluated_at,
                        PhysicalNotificationSegmentCurrent.segment_end > evaluated_at,
                    )
                    .order_by(
                        PhysicalNotificationSegmentCurrent.asset_id,
                        PhysicalNotificationSegmentCurrent.retrieved_at.desc(),
                        PhysicalNotificationSegmentCurrent.segment_start.desc(),
                    )
                )
            ).scalars().all()
            settled_rows = (
                await session.execute(
                    _latest_settled_statement(
                        asset_ids=normalized_asset_ids,
                        national_grid_bm_units=national_ids,
                        elexon_bm_units=elexon_ids,
                        settled_per_unit=settled_per_unit,
                    )
                )
            ).scalars().all()

        return AssetEvidenceRead(
            plans=tuple(_map_plan(row) for row in plan_rows),
            settled=tuple(_map_settled(row) for row in settled_rows),
        )


def _latest_successes_statement():
    ranked = (
        select(
            IngestionRun.adapter.label("adapter"),
            IngestionRun.completed_at.label("completed_at"),
            func.row_number()
            .over(
                partition_by=IngestionRun.adapter,
                order_by=(
                    IngestionRun.completed_at.desc(),
                    IngestionRun.started_at.desc(),
                    IngestionRun.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(
            IngestionRun.adapter.in_(ASSET_JOB_IDS),
            IngestionRun.status == IngestionRunStatus.SUCCEEDED,
            IngestionRun.completed_at.is_not(None),
        )
        .subquery()
    )
    return (
        select(ranked.c.adapter, ranked.c.completed_at)
        .where(ranked.c.rank == 1)
        .order_by(ranked.c.adapter)
    )


def _latest_settled_statement(
    *,
    asset_ids: tuple[UUID, ...] = (),
    national_grid_bm_units: tuple[str, ...] = (),
    elexon_bm_units: tuple[str, ...] = (),
    settled_per_unit: int,
):
    scope = _evidence_scope(
        B1610SettledEnergyRevision,
        asset_ids=asset_ids,
        national_grid_bm_units=national_grid_bm_units,
        elexon_bm_units=elexon_bm_units,
    )
    latest_revision = (
        select(
            B1610SettledEnergyRevision.id.label("row_id"),
            B1610SettledEnergyRevision.asset_id.label("asset_id"),
            B1610SettledEnergyRevision.interval_end.label("interval_end"),
            func.row_number()
            .over(
                partition_by=(
                    B1610SettledEnergyRevision.source_id,
                    B1610SettledEnergyRevision.asset_id,
                    B1610SettledEnergyRevision.settlement_date,
                    B1610SettledEnergyRevision.settlement_period,
                ),
                order_by=(
                    B1610SettledEnergyRevision.revision.desc(),
                    B1610SettledEnergyRevision.retrieved_at.desc(),
                    B1610SettledEnergyRevision.id.desc(),
                ),
            )
            .label("revision_rank"),
        )
        .where(scope)
        .subquery()
    )
    ranked_intervals = (
        select(
            latest_revision.c.row_id,
            func.row_number()
            .over(
                partition_by=latest_revision.c.asset_id,
                order_by=(
                    latest_revision.c.interval_end.desc(),
                    latest_revision.c.row_id.desc(),
                ),
            )
            .label("interval_rank"),
        )
        .where(latest_revision.c.revision_rank == 1)
        .subquery()
    )
    return (
        select(B1610SettledEnergyRevision)
        .join(
            ranked_intervals,
            ranked_intervals.c.row_id == B1610SettledEnergyRevision.id,
        )
        .where(ranked_intervals.c.interval_rank <= settled_per_unit)
        .order_by(
            B1610SettledEnergyRevision.asset_id,
            B1610SettledEnergyRevision.interval_end.desc(),
            B1610SettledEnergyRevision.revision.desc(),
        )
    )


def _map_asset(row: Asset) -> StoredAssetRead:
    return StoredAssetRead(
        id=row.id,
        source_id=row.source_id,
        external_id=row.external_id,
        asset_type=row.asset_type,
        display_name=row.display_name,
        fuel_type=row.fuel_type,
        region_code=row.region_code,
        counterparty=row.counterparty,
        capacity_mw=row.capacity_mw,
        latitude=row.latitude,
        longitude=row.longitude,
        active=row.active,
        attributes=dict(row.attributes or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _map_plan(row: PhysicalNotificationSegmentCurrent) -> PlannedSegmentRead:
    return PlannedSegmentRead(
        asset_id=row.asset_id,
        national_grid_bm_unit=row.national_grid_bm_unit,
        elexon_bm_unit=row.elexon_bm_unit,
        settlement_date=row.settlement_date.isoformat(),
        settlement_period=row.settlement_period,
        segment_start=_utc(row.segment_start, "segment_start"),
        segment_end=_utc(row.segment_end, "segment_end"),
        level_from_mw=row.level_from_mw,
        level_to_mw=row.level_to_mw,
        retrieved_at=_utc(row.retrieved_at, "retrieved_at"),
    )


def _map_settled(row: B1610SettledEnergyRevision) -> SettledEnergyRead:
    return SettledEnergyRead(
        asset_id=row.asset_id,
        national_grid_bm_unit=row.national_grid_bm_unit,
        elexon_bm_unit=row.elexon_bm_unit,
        settlement_date=row.settlement_date.isoformat(),
        settlement_period=row.settlement_period,
        interval_start=_utc(row.interval_start, "interval_start"),
        interval_end=_utc(row.interval_end, "interval_end"),
        energy_mwh=row.energy_mwh,
        average_mw=row.average_mw,
        psr_type=row.psr_type,
        retrieved_at=_utc(row.retrieved_at, "retrieved_at"),
        revision=row.revision,
    )


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalized_text_ids(
    values: Sequence[str],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if any(not isinstance(value, str) for value in values):
        raise TypeError(f"{field_name} must be strings")
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _evidence_scope(
    model: type[PhysicalNotificationSegmentCurrent]
    | type[B1610SettledEnergyRevision],
    *,
    asset_ids: tuple[UUID, ...],
    national_grid_bm_units: tuple[str, ...],
    elexon_bm_units: tuple[str, ...],
):
    clauses = []
    if asset_ids:
        clauses.append(model.asset_id.in_(asset_ids))
    if national_grid_bm_units:
        clauses.append(model.national_grid_bm_unit.in_(national_grid_bm_units))
    if elexon_bm_units:
        clauses.append(model.elexon_bm_unit.in_(elexon_bm_units))
    if not clauses:
        raise ValueError("at least one BM-unit evidence identifier is required")
    return or_(*clauses)
