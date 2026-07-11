from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    CarbonObservation,
    DemandObservation,
    FrequencyObservation,
    GenerationObservation,
    InterconnectorObservation,
    SourceMetadata,
)
from app.sources.types import as_utc


SessionFactory = Callable[[], AsyncSession]
ObservationRow = TypeVar("ObservationRow")


@dataclass(frozen=True, slots=True)
class ReadProvenance:
    source_id: str
    source_record_id: str | None
    observed_at: datetime
    published_at: datetime | None
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationRead:
    series_key: str
    fuel_type: str
    megawatts: float
    provenance: ReadProvenance


@dataclass(frozen=True, slots=True)
class DemandRead:
    series_key: str
    demand_type: str
    megawatts: float
    provenance: ReadProvenance


@dataclass(frozen=True, slots=True)
class FrequencyRead:
    series_key: str
    hertz: float
    provenance: ReadProvenance


@dataclass(frozen=True, slots=True)
class InterconnectorRead:
    connector_id: str
    display_name: str
    counterparty: str
    megawatts: float
    provenance: ReadProvenance


@dataclass(frozen=True, slots=True)
class CarbonRead:
    region_code: str
    intensity_gco2_kwh: float
    index_label: str | None
    generation_mix: tuple[dict[str, Any], ...]
    provenance: ReadProvenance


@dataclass(frozen=True, slots=True)
class SourceMetadataRead:
    id: str
    provider: str
    dataset: str
    display_name: str
    documentation_url: str | None
    licence_url: str | None
    attribution: str | None
    expected_cadence_seconds: int


@dataclass(frozen=True, slots=True)
class CurrentGridRead:
    requested_at: datetime
    generation: tuple[GenerationRead, ...]
    demand: DemandRead | None
    frequency: FrequencyRead | None
    interconnectors: tuple[InterconnectorRead, ...]
    carbon: CarbonRead | None
    sources: tuple[SourceMetadataRead, ...]

    @property
    def effective_at(self) -> datetime | None:
        observed_times = [
            reading.provenance.observed_at for reading in self._all_readings()
        ]
        return max(observed_times, default=None)

    @property
    def retrieved_at(self) -> datetime | None:
        retrieved_times = [
            reading.provenance.retrieved_at for reading in self._all_readings()
        ]
        return max(retrieved_times, default=None)

    def _all_readings(self) -> tuple[Any, ...]:
        optional = tuple(
            value
            for value in (self.demand, self.frequency, self.carbon)
            if value is not None
        )
        return (*self.generation, *self.interconnectors, *optional)


@dataclass(frozen=True, slots=True)
class GridTimelineRead:
    window_start: datetime
    window_end: datetime
    resolution_seconds: int
    generation: tuple[GenerationRead, ...]
    demand: tuple[DemandRead, ...]
    frequency: tuple[FrequencyRead, ...]
    interconnectors: tuple[InterconnectorRead, ...]
    carbon: tuple[CarbonRead, ...]
    sources: tuple[SourceMetadataRead, ...]


class GridReadRepository:
    """Source-neutral query service over normalized observation tables."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def get_current(
        self,
        *,
        as_of: datetime | None = None,
        carbon_region: str = "GB",
    ) -> CurrentGridRead:
        cutoff = as_utc(as_of or self._clock(), field_name="as_of")
        async with self._session_factory() as session:
            generation_rows = list(
                (
                    await session.execute(_latest_generation_statement(cutoff))
                ).scalars().all()
            )
            demand_row = (
                await session.execute(_latest_demand_statement(cutoff))
            ).scalar_one_or_none()
            frequency_row = (
                await session.execute(_latest_frequency_statement(cutoff))
            ).scalar_one_or_none()
            interconnector_rows = list(
                (
                    await session.execute(_latest_interconnector_statement(cutoff))
                ).scalars().all()
            )
            carbon_row = (
                await session.execute(
                    _latest_carbon_statement(cutoff, carbon_region=carbon_region)
                )
            ).scalar_one_or_none()
            source_ids = {
                row.source_id
                for row in (
                    *generation_rows,
                    *interconnector_rows,
                    *(value for value in (demand_row, frequency_row, carbon_row) if value),
                )
            }
            source_rows = await _read_source_rows(session, source_ids)

        return CurrentGridRead(
            requested_at=cutoff,
            generation=tuple(map_generation_read(row) for row in generation_rows),
            demand=map_demand_read(demand_row) if demand_row is not None else None,
            frequency=(
                map_frequency_read(frequency_row) if frequency_row is not None else None
            ),
            interconnectors=tuple(
                map_interconnector_read(row) for row in interconnector_rows
            ),
            carbon=map_carbon_read(carbon_row) if carbon_row is not None else None,
            sources=tuple(map_source_metadata_read(row) for row in source_rows),
        )

    async def get_latest_generation(
        self, *, as_of: datetime | None = None
    ) -> tuple[GenerationRead, ...]:
        cutoff = as_utc(as_of or self._clock(), field_name="as_of")
        async with self._session_factory() as session:
            rows = (
                await session.execute(_latest_generation_statement(cutoff))
            ).scalars().all()
        return tuple(map_generation_read(row) for row in rows)

    async def get_latest_demand(
        self, *, as_of: datetime | None = None
    ) -> DemandRead | None:
        cutoff = as_utc(as_of or self._clock(), field_name="as_of")
        async with self._session_factory() as session:
            row = (
                await session.execute(_latest_demand_statement(cutoff))
            ).scalar_one_or_none()
        return map_demand_read(row) if row is not None else None

    async def get_latest_frequency(
        self, *, as_of: datetime | None = None
    ) -> FrequencyRead | None:
        cutoff = as_utc(as_of or self._clock(), field_name="as_of")
        async with self._session_factory() as session:
            row = (
                await session.execute(_latest_frequency_statement(cutoff))
            ).scalar_one_or_none()
        return map_frequency_read(row) if row is not None else None

    async def get_latest_interconnectors(
        self, *, as_of: datetime | None = None
    ) -> tuple[InterconnectorRead, ...]:
        cutoff = as_utc(as_of or self._clock(), field_name="as_of")
        async with self._session_factory() as session:
            rows = (
                await session.execute(_latest_interconnector_statement(cutoff))
            ).scalars().all()
        return tuple(map_interconnector_read(row) for row in rows)

    async def get_latest_carbon(
        self,
        *,
        as_of: datetime | None = None,
        carbon_region: str = "GB",
    ) -> CarbonRead | None:
        cutoff = as_utc(as_of or self._clock(), field_name="as_of")
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    _latest_carbon_statement(cutoff, carbon_region=carbon_region)
                )
            ).scalar_one_or_none()
        return map_carbon_read(row) if row is not None else None

    async def get_timeline(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        resolution_seconds: int = 60,
        carbon_region: str = "GB",
    ) -> GridTimelineRead:
        start = as_utc(window_start, field_name="window_start")
        end = as_utc(window_end, field_name="window_end")
        if start >= end:
            raise ValueError("timeline window_start must precede window_end")
        if resolution_seconds <= 0:
            raise ValueError("resolution_seconds must be positive")

        async with self._session_factory() as session:
            generation_rows = list(
                (
                    await session.execute(
                        _between_statement(GenerationObservation, start, end)
                    )
                ).scalars().all()
            )
            demand_rows = list(
                (
                    await session.execute(
                        _between_statement(DemandObservation, start, end)
                    )
                ).scalars().all()
            )
            frequency_rows = list(
                (
                    await session.execute(
                        _between_statement(FrequencyObservation, start, end)
                    )
                ).scalars().all()
            )
            interconnector_rows = list(
                (
                    await session.execute(
                        _between_statement(InterconnectorObservation, start, end)
                    )
                ).scalars().all()
            )
            carbon_statement = _between_statement(CarbonObservation, start, end).where(
                func.lower(CarbonObservation.region_code) == carbon_region.lower()
            )
            carbon_rows = list(
                (await session.execute(carbon_statement)).scalars().all()
            )

            generation_rows = _downsample(
                generation_rows,
                resolution_seconds,
                series_key=lambda row: (row.source_id, row.series_key),
            )
            demand_rows = _downsample(
                demand_rows,
                resolution_seconds,
                series_key=lambda row: (
                    row.source_id,
                    row.series_key,
                    row.demand_type,
                ),
            )
            frequency_rows = _downsample(
                frequency_rows,
                resolution_seconds,
                series_key=lambda row: (row.source_id, row.series_key),
            )
            interconnector_rows = _downsample(
                interconnector_rows,
                resolution_seconds,
                series_key=lambda row: (row.source_id, row.connector_code),
            )
            carbon_rows = _downsample(
                carbon_rows,
                resolution_seconds,
                series_key=lambda row: (row.source_id, row.region_code),
            )
            source_ids = {
                row.source_id
                for row in (
                    *generation_rows,
                    *demand_rows,
                    *frequency_rows,
                    *interconnector_rows,
                    *carbon_rows,
                )
            }
            source_rows = await _read_source_rows(session, source_ids)

        return GridTimelineRead(
            window_start=start,
            window_end=end,
            resolution_seconds=resolution_seconds,
            generation=tuple(map_generation_read(row) for row in generation_rows),
            demand=tuple(map_demand_read(row) for row in demand_rows),
            frequency=tuple(map_frequency_read(row) for row in frequency_rows),
            interconnectors=tuple(
                map_interconnector_read(row) for row in interconnector_rows
            ),
            carbon=tuple(map_carbon_read(row) for row in carbon_rows),
            sources=tuple(map_source_metadata_read(row) for row in source_rows),
        )

    async def list_sources(self) -> tuple[SourceMetadataRead, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SourceMetadata)
                    .where(SourceMetadata.active.is_(True))
                    .order_by(SourceMetadata.id)
                )
            ).scalars().all()
        return tuple(map_source_metadata_read(row) for row in rows)


def map_generation_read(row: GenerationObservation) -> GenerationRead:
    return GenerationRead(
        series_key=row.series_key,
        fuel_type=row.fuel_type,
        megawatts=float(row.generation_mw),
        provenance=_provenance(row),
    )


def map_demand_read(row: DemandObservation) -> DemandRead:
    return DemandRead(
        series_key=row.series_key,
        demand_type=row.demand_type,
        megawatts=float(row.demand_mw),
        provenance=_provenance(row),
    )


def map_frequency_read(row: FrequencyObservation) -> FrequencyRead:
    return FrequencyRead(
        series_key=row.series_key,
        hertz=float(row.frequency_hz),
        provenance=_provenance(row),
    )


def map_interconnector_read(
    row: InterconnectorObservation,
) -> InterconnectorRead:
    attributes = row.attributes or {}
    return InterconnectorRead(
        connector_id=row.connector_code,
        display_name=str(attributes.get("displayName") or row.connector_code),
        counterparty=row.counterparty,
        megawatts=float(row.flow_mw),
        provenance=_provenance(row),
    )


def map_carbon_read(row: CarbonObservation) -> CarbonRead:
    return CarbonRead(
        region_code=row.region_code,
        intensity_gco2_kwh=float(row.intensity_gco2_kwh),
        index_label=row.index_label,
        generation_mix=tuple(row.generation_mix or ()),
        provenance=_provenance(row),
    )


def map_source_metadata_read(row: SourceMetadata) -> SourceMetadataRead:
    return SourceMetadataRead(
        id=row.id,
        provider=row.provider,
        dataset=row.dataset,
        display_name=row.display_name,
        documentation_url=row.documentation_url,
        licence_url=row.licence_url,
        attribution=row.attribution,
        expected_cadence_seconds=row.expected_cadence_seconds,
    )


def _provenance(row: Any) -> ReadProvenance:
    return ReadProvenance(
        source_id=row.source_id,
        source_record_id=row.source_record_id,
        observed_at=row.observed_at,
        published_at=row.published_at,
        retrieved_at=row.retrieved_at,
    )


def _latest_generation_statement(as_of: datetime) -> Select:
    latest_time = (
        select(func.max(GenerationObservation.observed_at))
        .where(GenerationObservation.observed_at <= as_of)
        .scalar_subquery()
    )
    return (
        select(GenerationObservation)
        .where(GenerationObservation.observed_at == latest_time)
        .order_by(GenerationObservation.fuel_type, GenerationObservation.series_key)
    )


def _latest_demand_statement(as_of: datetime) -> Select:
    return (
        select(DemandObservation)
        .where(DemandObservation.observed_at <= as_of)
        .order_by(
            DemandObservation.observed_at.desc(),
            DemandObservation.revision.desc(),
            DemandObservation.retrieved_at.desc(),
        )
        .limit(1)
    )


def _latest_frequency_statement(as_of: datetime) -> Select:
    return (
        select(FrequencyObservation)
        .where(FrequencyObservation.observed_at <= as_of)
        .order_by(
            FrequencyObservation.observed_at.desc(),
            FrequencyObservation.revision.desc(),
            FrequencyObservation.retrieved_at.desc(),
        )
        .limit(1)
    )


def _latest_interconnector_statement(as_of: datetime) -> Select:
    rank = func.row_number().over(
        partition_by=InterconnectorObservation.connector_code,
        order_by=(
            InterconnectorObservation.observed_at.desc(),
            InterconnectorObservation.revision.desc(),
            InterconnectorObservation.retrieved_at.desc(),
        ),
    ).label("observation_rank")
    ranked = (
        select(InterconnectorObservation, rank)
        .where(InterconnectorObservation.observed_at <= as_of)
        .subquery()
    )
    latest = aliased(InterconnectorObservation, ranked)
    return (
        select(latest)
        .where(ranked.c.observation_rank == 1)
        .order_by(latest.connector_code)
    )


def _latest_carbon_statement(as_of: datetime, *, carbon_region: str) -> Select:
    return (
        select(CarbonObservation)
        .where(
            CarbonObservation.observed_at <= as_of,
            func.lower(CarbonObservation.region_code) == carbon_region.lower(),
        )
        .order_by(
            CarbonObservation.observed_at.desc(),
            CarbonObservation.revision.desc(),
            CarbonObservation.retrieved_at.desc(),
        )
        .limit(1)
    )


def _between_statement(model: type, start: datetime, end: datetime) -> Select:
    return (
        select(model)
        .where(model.observed_at >= start, model.observed_at < end)
        .order_by(model.observed_at, model.revision, model.source_id)
    )


async def _read_source_rows(
    session: AsyncSession, source_ids: Iterable[str]
) -> list[SourceMetadata]:
    ids = sorted(set(source_ids))
    if not ids:
        return []
    return list(
        (
            await session.execute(
                select(SourceMetadata)
                .where(SourceMetadata.id.in_(ids))
                .order_by(SourceMetadata.id)
            )
        ).scalars().all()
    )


def _downsample(
    rows: Sequence[ObservationRow],
    resolution_seconds: int,
    *,
    series_key: Callable[[ObservationRow], tuple[Any, ...]],
) -> list[ObservationRow]:
    latest_by_bucket: dict[tuple[Any, ...], ObservationRow] = {}
    for row in rows:
        bucket = int(row.observed_at.timestamp()) // resolution_seconds
        latest_by_bucket[(*series_key(row), bucket)] = row
    return sorted(latest_by_bucket.values(), key=lambda row: row.observed_at)

