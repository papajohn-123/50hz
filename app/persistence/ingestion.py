from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, delete, func, literal_column, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.assets.models import (
    AssetReference,
    PlannedProfileSegment,
    SettledMeteredEnergy,
)
from app.db.models import (
    Asset,
    B1610SettledEnergyRevision,
    CarbonObservation,
    DemandObservation,
    DistributionIncidentCurrent,
    DistributionIncidentRevision,
    ForecastObservation,
    FrequencyObservation,
    GenerationObservation,
    IngestionRun,
    InterconnectorObservation,
    PhysicalNotificationSegmentCurrent,
    RawPayload,
    ReportedNotice,
    SourceMetadata,
)
from app.domain.enums import IngestionRunStatus
from app.geography.records import (
    REPD_ASSET_TYPE,
    REPD_SOURCE_ID,
    map_repd_site,
    repd_snapshot_membership,
    repd_source_metadata_values,
)
from app.geography.repd import REPDSite
from app.persistence.event_lifecycle import materialize_reported_notice_rows
from app.persistence.records import (
    BM_UNIT_REFERENCE_SOURCE_ID,
    bm_unit_reference_source_metadata_values,
    job_source_metadata_values,
    map_carbon_actual_record,
    map_carbon_forecast_record,
    map_b1610_settled_energy,
    map_asset_reference,
    map_demand_forecast_record,
    map_demand_record,
    map_distribution_incident_record,
    map_frequency_record,
    map_generation_record,
    map_interconnector_record,
    map_physical_notification_segment,
    map_remit_notice_record,
    map_system_warning_record,
    map_wind_forecast_record,
    source_metadata_values,
)
from app.sources.types import (
    AdapterResult,
    CarbonIntensityRecord,
    DataClassification as SourceDataClassification,
    DemandForecastRecord,
    DemandRecord,
    DistributionIncidentRecord,
    FrequencyRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
    ObservationWindow,
    RemitUnavailabilityRecord,
    SystemWarningRecord,
    WindForecastRecord,
    as_utc,
)
from app.sources.ukpn import contains_full_postcode, sanitize_ukpn_payload
from app.worker.contracts import IngestionCheckpoint, PersistOutcome


SessionFactory = Callable[[], AsyncSession]

_RUN_NAMESPACE = UUID("b1f130e2-ec87-5a79-88a3-a3ed99a5321c")
_RAW_PAYLOAD_NAMESPACE = UUID("40321ac1-3394-58d2-9cf7-33dbb7187b6c")
_DISTRIBUTION_CURRENT_NAMESPACE = UUID("d9b24b44-6241-5089-b0f2-91f64325b921")
_BM_UNIT_ASSET_NAMESPACE = UUID("5834312d-f415-51a7-acb7-cc5dff551b73")
_REPD_ASSET_NAMESPACE = UUID("b946b36d-248e-5e25-9213-49cd56bcd1aa")


@dataclass(frozen=True, slots=True)
class _UpsertSpec:
    model: type
    conflict_columns: tuple[str, ...]
    update_columns: tuple[str, ...] = ()
    change_columns: tuple[str, ...] = ()
    identity_columns: tuple[str, ...] = ()
    factual_columns: tuple[str, ...] = ()

    @property
    def immutable_revisioned(self) -> bool:
        return bool(self.identity_columns)


_REVISION_LOOKUP_BATCH_SIZE = 250
_NORMALIZED_WRITE_BATCH_SIZE = 400


class ImmutableRevisionConflictError(RuntimeError):
    """A source lock failed to serialize immutable revision allocation."""


_GENERATION_SPEC = _UpsertSpec(
    model=GenerationObservation,
    conflict_columns=("source_id", "series_key", "observed_at", "revision"),
    identity_columns=("source_id", "series_key", "observed_at"),
    factual_columns=(
        "source_record_id",
        "fuel_type",
        "asset_id",
        "generation_mw",
        "settlement_date",
        "settlement_period",
        "published_at",
        "quality",
        "attributes",
    ),
)
_DEMAND_SPEC = _UpsertSpec(
    model=DemandObservation,
    conflict_columns=(
        "source_id",
        "series_key",
        "demand_type",
        "observed_at",
        "revision",
    ),
    identity_columns=("source_id", "series_key", "demand_type", "observed_at"),
    factual_columns=(
        "source_record_id",
        "demand_mw",
        "settlement_date",
        "settlement_period",
        "published_at",
        "quality",
        "attributes",
    ),
)
_FREQUENCY_SPEC = _UpsertSpec(
    model=FrequencyObservation,
    conflict_columns=("source_id", "series_key", "observed_at", "revision"),
    identity_columns=("source_id", "series_key", "observed_at"),
    factual_columns=(
        "source_record_id",
        "frequency_hz",
        "published_at",
        "quality",
        "attributes",
    ),
)
_INTERCONNECTOR_SPEC = _UpsertSpec(
    model=InterconnectorObservation,
    conflict_columns=("source_id", "connector_code", "observed_at", "revision"),
    identity_columns=("source_id", "connector_code", "observed_at"),
    factual_columns=(
        "source_record_id",
        "asset_id",
        "counterparty",
        "flow_mw",
        "published_at",
        "quality",
        "attributes",
    ),
)
_CARBON_ACTUAL_SPEC = _UpsertSpec(
    model=CarbonObservation,
    conflict_columns=("source_id", "region_code", "observed_at", "revision"),
    identity_columns=("source_id", "region_code", "observed_at"),
    factual_columns=(
        "source_record_id",
        "intensity_gco2_kwh",
        "index_label",
        "generation_mix",
        "published_at",
        "quality",
        "attributes",
    ),
)
_FORECAST_SPEC = _UpsertSpec(
    model=ForecastObservation,
    conflict_columns=(
        "source_id",
        "metric_type",
        "series_key",
        "variant",
        "valid_from",
        "issued_at",
        "revision",
    ),
    identity_columns=(
        "source_id",
        "metric_type",
        "series_key",
        "variant",
        "valid_from",
        "issued_at",
    ),
    factual_columns=(
        "source_record_id",
        "value",
        "unit",
        "value_low",
        "value_high",
        "valid_to",
        "published_at",
        "model_name",
        "settlement_date",
        "settlement_period",
        "attributes",
    ),
)
_REPORTED_NOTICE_SPEC = _UpsertSpec(
    model=ReportedNotice,
    conflict_columns=("source_id", "notice_kind", "external_id", "revision_key"),
    update_columns=(
        "raw_payload_id",
        "source_record_id",
        "content_sha256",
        "revision_number",
        "published_at",
        "source_created_at",
        "retrieved_at",
        "event_start",
        "event_end",
        "heading",
        "event_type",
        "unavailability_type",
        "event_status",
        "participant_id",
        "asset_id",
        "asset_type",
        "affected_unit",
        "affected_unit_eic",
        "affected_area",
        "bidding_zone",
        "fuel_type",
        "normal_capacity_mw",
        "available_capacity_mw",
        "unavailable_capacity_mw",
        "duration_uncertainty",
        "reported_cause",
        "reported_related_information",
        "warning_type",
        "warning_text",
        "evidence",
    ),
)
_DISTRIBUTION_INCIDENT_SPEC = _UpsertSpec(
    model=DistributionIncidentRevision,
    conflict_columns=("source_id", "incident_reference", "revision"),
    identity_columns=("source_id", "incident_reference"),
    factual_columns=(
        "content_sha256",
        "classification",
        "status",
        "status_id",
        "source_created_at",
        "observed_at",
        "incident_start",
        "restored_at",
        "estimated_restoration_at",
        "customers_affected",
        "calls_reported",
        "postcode_sectors",
        "outward_codes",
        "latitude",
        "longitude",
        "geography_precision",
        "operating_zone",
        "official_summary",
        "official_details",
        "restoration_window_text",
        "incident_category",
    ),
)
_ASSET_REFERENCE_SPEC = _UpsertSpec(
    model=Asset,
    conflict_columns=("source_id", "external_id"),
    update_columns=(
        "asset_type",
        "display_name",
        "fuel_type",
        "region_code",
        "counterparty",
        "capacity_mw",
        "latitude",
        "longitude",
        "map_x",
        "map_y",
        "active",
        "attributes",
    ),
)
_PHYSICAL_NOTIFICATION_SPEC = _UpsertSpec(
    model=PhysicalNotificationSegmentCurrent,
    conflict_columns=(
        "source_id",
        "national_grid_bm_unit",
        "settlement_date",
        "settlement_period",
        "segment_start",
        "segment_end",
    ),
    update_columns=(
        "raw_payload_id",
        "asset_id",
        "elexon_bm_unit",
        "level_from_mw",
        "level_to_mw",
        "classification",
        "retrieved_at",
        "attributes",
    ),
    # A later retrieval of the identical submitted plan is delivery metadata,
    # not a changed plan. When a factual level does change, every update column
    # (including its new provenance) is refreshed together.
    change_columns=(
        "asset_id",
        "elexon_bm_unit",
        "level_from_mw",
        "level_to_mw",
        "classification",
    ),
)
_B1610_SETTLED_ENERGY_SPEC = _UpsertSpec(
    model=B1610SettledEnergyRevision,
    conflict_columns=(
        "source_id",
        "asset_id",
        "settlement_date",
        "settlement_period",
        "revision",
    ),
    identity_columns=(
        "source_id",
        "asset_id",
        "settlement_date",
        "settlement_period",
    ),
    factual_columns=(
        "national_grid_bm_unit",
        "elexon_bm_unit",
        "interval_start",
        "interval_end",
        "energy_mwh",
        "average_mw",
        "psr_type",
        "classification",
    ),
)


class PostgresIngestionRepository:
    """Atomic PostgreSQL implementation of the worker persistence contract."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def get_checkpoint(self, job_id: str) -> IngestionCheckpoint | None:
        async with self._session_factory() as session:
            latest_result = await session.execute(
                select(IngestionRun)
                .where(IngestionRun.adapter == job_id)
                .order_by(IngestionRun.started_at.desc(), IngestionRun.id.desc())
                .limit(1)
            )
            latest = latest_result.scalars().first()
            if latest is None:
                return None

            if (
                latest.status is IngestionRunStatus.SUCCEEDED
                and latest.completed_at is not None
            ):
                succeeded = latest
            else:
                success_result = await session.execute(
                    select(IngestionRun)
                    .where(
                        IngestionRun.adapter == job_id,
                        IngestionRun.status == IngestionRunStatus.SUCCEEDED,
                    )
                    .order_by(
                        IngestionRun.completed_at.desc().nullslast(),
                        IngestionRun.started_at.desc(),
                    )
                    .limit(1)
                )
                succeeded = success_result.scalars().first()

        return IngestionCheckpoint(
            job_id=job_id,
            last_attempted_at=latest.started_at,
            last_succeeded_at=(
                succeeded.completed_at if succeeded is not None else None
            ),
            window_end=succeeded.requested_to if succeeded is not None else None,
        )

    async def persist_success(
        self,
        *,
        job_id: str,
        result: AdapterResult[Any],
        attempted_at: datetime,
        completed_at: datetime,
    ) -> PersistOutcome:
        attempted_at = as_utc(attempted_at, field_name="attempted_at")
        completed_at = as_utc(completed_at, field_name="completed_at")
        if completed_at < attempted_at:
            raise ValueError("completed_at cannot precede attempted_at")
        if not isinstance(result.raw_payload, (dict, list)):
            raise TypeError("raw payload must be a JSON object or array")
        if _is_distribution_snapshot(result):
            if (
                contains_full_postcode(result.raw_payload)
                or sanitize_ukpn_payload(result.raw_payload) != result.raw_payload
            ):
                raise ValueError(
                    "distribution incident payload has not been privacy-reduced"
                )
        if re.fullmatch(r"[0-9a-f]{64}", result.checksum_sha256.lower()) is None:
            raise ValueError("raw payload checksum must be a SHA-256 hex digest")
        checksum_sha256 = result.checksum_sha256.lower()

        _validate_record_types(result.records)
        _validate_repd_snapshot_contract(result)
        metadata = (
            repd_source_metadata_values()
            if _is_repd_snapshot(result)
            else source_metadata_values(
                provider=result.source_id,
                dataset=result.dataset,
                request_url=result.request_url,
            )
        )
        source_id = metadata["id"]
        idempotency_key = _success_idempotency_key(job_id, result)
        proposed_run_id = uuid.uuid5(_RUN_NAMESPACE, idempotency_key)
        proposed_raw_id = uuid.uuid5(
            _RAW_PAYLOAD_NAMESPACE,
            f"{source_id}|{result.endpoint}|{checksum_sha256}",
        )
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(_source_metadata_upsert(metadata))

                run_statement = pg_insert(IngestionRun).values(
                    id=proposed_run_id,
                    source_id=source_id,
                    adapter=job_id,
                    endpoint=result.endpoint,
                    idempotency_key=idempotency_key,
                    requested_from=result.window.start,
                    requested_to=result.window.end,
                    started_at=attempted_at,
                    completed_at=None,
                    status=IngestionRunStatus.RUNNING,
                    records_received=len(result.records),
                    records_written=0,
                    cursor={
                        "jobId": job_id,
                        "windowEnd": result.window.end.isoformat(),
                        "requestUrl": result.request_url,
                        "checksumSha256": checksum_sha256,
                        "warnings": list(result.warnings),
                    },
                    error=None,
                )
                run_statement = run_statement.on_conflict_do_update(
                    index_elements=[IngestionRun.idempotency_key],
                    set_={
                        "status": IngestionRunStatus.RUNNING,
                        "started_at": attempted_at,
                        "completed_at": None,
                        "error": None,
                    },
                ).returning(IngestionRun.id)
                run_result = await session.execute(run_statement)
                run_id = run_result.scalar_one()

                raw_statement = (
                    pg_insert(RawPayload)
                    .values(
                        id=proposed_raw_id,
                        ingestion_run_id=run_id,
                        source_id=source_id,
                        endpoint=result.endpoint,
                        retrieved_at=as_utc(
                            result.retrieved_at, field_name="result.retrieved_at"
                        ),
                        observed_window_start=result.window.start,
                        observed_window_end=result.window.end,
                        http_status=200,
                        content_type=result.content_type,
                        etag=result.metadata.get("etag"),
                        checksum_sha256=checksum_sha256,
                        payload=result.raw_payload,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            RawPayload.source_id,
                            RawPayload.endpoint,
                            RawPayload.checksum_sha256,
                        ]
                    )
                    .returning(RawPayload.id)
                )
                raw_result = await session.execute(raw_statement)
                raw_payload_id = raw_result.scalar_one_or_none()
                if raw_payload_id is None:
                    raw_payload_result = await session.execute(
                        select(RawPayload.id).where(
                            RawPayload.source_id == source_id,
                            RawPayload.endpoint == result.endpoint,
                            RawPayload.checksum_sha256 == checksum_sha256,
                        )
                    )
                    raw_payload_id = raw_payload_result.scalar_one()

                inserted = 0
                updated_count = 0
                unchanged = 0
                asset_references = tuple(
                    record
                    for record in result.records
                    if isinstance(record, AssetReference)
                )
                if asset_references:
                    asset_inserted, asset_updated, asset_unchanged = (
                        await _persist_asset_reference_snapshot(
                            session,
                            source_id=source_id,
                            records=asset_references,
                        )
                    )
                    inserted += asset_inserted
                    updated_count += asset_updated
                    unchanged += asset_unchanged

                repd_sites = tuple(
                    record for record in result.records if isinstance(record, REPDSite)
                )
                if repd_sites:
                    repd_inserted, repd_updated, repd_unchanged = (
                        await _persist_repd_snapshot(
                            session,
                            source_id=source_id,
                            records=repd_sites,
                        )
                    )
                    inserted += repd_inserted
                    updated_count += repd_updated
                    unchanged += repd_unchanged

                dependent_records = tuple(
                    record
                    for record in result.records
                    if isinstance(
                        record,
                        (PlannedProfileSegment, SettledMeteredEnergy),
                    )
                )
                asset_ids = (
                    await _ensure_bm_unit_assets(session, dependent_records)
                    if dependent_records
                    else {}
                )
                batches = _map_record_batches(
                    result.records,
                    source_id=source_id,
                    raw_payload_id=raw_payload_id,
                    asset_ids=asset_ids,
                )
                if _is_physical_notification_snapshot(result):
                    pn_rows = next(
                        (
                            rows
                            for spec, rows in batches
                            if spec is _PHYSICAL_NOTIFICATION_SPEC
                        ),
                        [],
                    )
                    await _prune_physical_notification_scope(
                        session,
                        source_id=source_id,
                        metadata=result.metadata,
                        rows=pn_rows,
                    )
                reported_notice_rows: list[dict[str, Any]] = []
                for spec, rows in batches:
                    if spec is _REPORTED_NOTICE_SPEC:
                        reported_notice_rows.extend(rows)
                    unique_rows, duplicate_count = _deduplicate_rows(
                        rows,
                        spec.identity_columns or spec.conflict_columns,
                    )
                    unchanged += duplicate_count
                    if not unique_rows:
                        continue
                    if spec.immutable_revisioned:
                        latest = await _load_latest_revisions(
                            session,
                            spec,
                            unique_rows,
                        )
                        prepared, new_count, correction_count, same_count = (
                            _prepare_immutable_revisions(spec, unique_rows, latest)
                        )
                        unchanged += same_count
                        if prepared:
                            await _insert_immutable_revisions(session, spec, prepared)
                        inserted += new_count
                        updated_count += correction_count
                        continue
                    for write_rows in _normalized_write_batches(unique_rows):
                        write_result = await session.execute(
                            _observation_upsert(spec, write_rows)
                        )
                        insert_flags = list(write_result.scalars().all())
                        inserted += sum(1 for flag in insert_flags if bool(flag))
                        updated_count += sum(
                            1 for flag in insert_flags if not bool(flag)
                        )
                        unchanged += len(write_rows) - len(insert_flags)

                if reported_notice_rows:
                    await materialize_reported_notice_rows(
                        session,
                        reported_notice_rows,
                    )
                if _is_distribution_snapshot(result):
                    await _refresh_distribution_incident_current(
                        session,
                        source_id=source_id,
                        records=tuple(
                            record
                            for record in result.records
                            if isinstance(record, DistributionIncidentRecord)
                        ),
                        seen_at=as_utc(
                            result.retrieved_at,
                            field_name="result.retrieved_at",
                        ),
                    )

                await session.execute(
                    update(IngestionRun)
                    .where(IngestionRun.id == run_id)
                    .values(
                        completed_at=completed_at,
                        status=IngestionRunStatus.SUCCEEDED,
                        records_written=inserted + updated_count,
                    )
                )

        return PersistOutcome(
            inserted=inserted,
            updated=updated_count,
            unchanged=unchanged,
        )

    async def record_failure(
        self,
        *,
        job_id: str,
        window: ObservationWindow,
        attempted_at: datetime,
        failed_at: datetime,
        error_type: str,
        error_message: str,
    ) -> None:
        attempted_at = as_utc(attempted_at, field_name="attempted_at")
        failed_at = as_utc(failed_at, field_name="failed_at")
        if failed_at < attempted_at:
            raise ValueError("failed_at cannot precede attempted_at")
        metadata = job_source_metadata_values(job_id)
        idempotency_key = _failure_idempotency_key(
            job_id, window, attempted_at, error_type
        )
        run_id = uuid.uuid5(_RUN_NAMESPACE, idempotency_key)
        error = {
            "type": error_type[:160],
            "message": error_message[:2000],
        }

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(_source_metadata_upsert(metadata))
                statement = pg_insert(IngestionRun).values(
                    id=run_id,
                    source_id=metadata["id"],
                    adapter=job_id,
                    endpoint=job_id,
                    idempotency_key=idempotency_key,
                    requested_from=window.start,
                    requested_to=window.end,
                    started_at=attempted_at,
                    completed_at=failed_at,
                    status=IngestionRunStatus.FAILED,
                    records_received=0,
                    records_written=0,
                    cursor={
                        "jobId": job_id,
                        "windowEnd": window.end.isoformat(),
                    },
                    error=error,
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[IngestionRun.idempotency_key],
                        set_={
                            "completed_at": failed_at,
                            "status": IngestionRunStatus.FAILED,
                            "error": error,
                        },
                    )
                )


def _source_metadata_upsert(values: dict[str, Any]):
    statement = pg_insert(SourceMetadata).values(**values)
    return statement.on_conflict_do_update(
        index_elements=[SourceMetadata.id],
        set_={
            "provider": statement.excluded.provider,
            "dataset": statement.excluded.dataset,
            "display_name": statement.excluded.display_name,
            "base_url": statement.excluded.base_url,
            "documentation_url": statement.excluded.documentation_url,
            "licence_name": statement.excluded.licence_name,
            "licence_url": statement.excluded.licence_url,
            "attribution": statement.excluded.attribution,
            "expected_cadence_seconds": statement.excluded.expected_cadence_seconds,
            "active": statement.excluded.active,
            "updated_at": func.now(),
        },
    )


def _observation_upsert(spec: _UpsertSpec, rows: Sequence[dict[str, Any]]):
    statement = pg_insert(spec.model).values(list(rows))
    update_values = {
        column: getattr(statement.excluded, column) for column in spec.update_columns
    }
    changed = or_(
        *(
            getattr(spec.model, column).is_distinct_from(
                getattr(statement.excluded, column)
            )
            for column in (spec.change_columns or spec.update_columns)
        )
    )
    return statement.on_conflict_do_update(
        index_elements=[getattr(spec.model, name) for name in spec.conflict_columns],
        set_=update_values,
        where=changed,
    ).returning(literal_column("xmax = 0", Boolean).label("was_inserted"))


async def _load_latest_revisions(
    session: AsyncSession,
    spec: _UpsertSpec,
    rows: Sequence[dict[str, Any]],
) -> dict[tuple[Any, ...], Any]:
    """Load one deterministic latest row per identity in bounded bulk queries."""

    identity_keys = [_identity_key(spec, row) for row in rows]
    latest: dict[tuple[Any, ...], Any] = {}
    for offset in range(0, len(identity_keys), _REVISION_LOOKUP_BATCH_SIZE):
        batch = identity_keys[offset : offset + _REVISION_LOOKUP_BATCH_SIZE]
        result = await session.execute(_latest_revision_statement(spec, batch))
        for existing in result.scalars().all():
            latest[_identity_key(spec, existing)] = existing
    return latest


def _latest_revision_statement(
    spec: _UpsertSpec,
    identity_keys: Sequence[tuple[Any, ...]],
):
    if not spec.immutable_revisioned:
        raise ValueError("latest-revision lookup requires an immutable spec")
    columns = tuple(getattr(spec.model, name) for name in spec.identity_columns)
    return (
        select(spec.model)
        .where(tuple_(*columns).in_(list(identity_keys)))
        # PostgreSQL DISTINCT ON avoids a query per observation while retaining
        # deterministic tie-breaking for any pre-existing malformed duplicates.
        .distinct(*columns)
        .order_by(
            *columns,
            spec.model.revision.desc(),
            spec.model.created_at.desc(),
            spec.model.id.desc(),
        )
    )


def _prepare_immutable_revisions(
    spec: _UpsertSpec,
    rows: Sequence[dict[str, Any]],
    latest: Mapping[tuple[Any, ...], Any],
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Allocate local revisions without treating delivery metadata as evidence."""

    prepared: list[dict[str, Any]] = []
    inserted = 0
    corrected = 0
    unchanged = 0
    for candidate in rows:
        identity = _identity_key(spec, candidate)
        existing = latest.get(identity)
        if existing is None:
            revision = 0
            inserted += 1
        elif _factual_signature(spec, candidate) == _factual_signature(
            spec, existing
        ):
            unchanged += 1
            continue
        else:
            revision = int(_row_value(existing, "revision")) + 1
            corrected += 1
        revisioned = dict(candidate)
        revisioned["revision"] = revision
        prepared.append(revisioned)
    return prepared, inserted, corrected, unchanged


async def _insert_immutable_revisions(
    session: AsyncSession,
    spec: _UpsertSpec,
    rows: Sequence[dict[str, Any]],
) -> None:
    for write_rows in _normalized_write_batches(rows):
        statement = (
            pg_insert(spec.model)
            .values(list(write_rows))
            .on_conflict_do_nothing(
                index_elements=[
                    getattr(spec.model, name) for name in spec.conflict_columns
                ]
            )
            .returning(spec.model.id)
        )
        result = await session.execute(statement)
        written = len(result.scalars().all())
        if written != len(write_rows):
            # Never turn a revision-allocation race into an in-place overwrite.
            # The surrounding transaction rolls back every batch and a later
            # locked run can retry.
            raise ImmutableRevisionConflictError(
                "immutable observation revision conflicted; source lock was not exclusive"
            )


def _normalized_write_batches(
    rows: Sequence[dict[str, Any]],
) -> tuple[Sequence[dict[str, Any]], ...]:
    """Keep PostgreSQL/asyncpg bind counts bounded for dense history chunks."""

    return tuple(
        rows[offset : offset + _NORMALIZED_WRITE_BATCH_SIZE]
        for offset in range(0, len(rows), _NORMALIZED_WRITE_BATCH_SIZE)
    )


def _identity_key(spec: _UpsertSpec, row: Any) -> tuple[Any, ...]:
    return tuple(_row_value(row, name) for name in spec.identity_columns)


def _factual_signature(spec: _UpsertSpec, row: Any) -> tuple[Any, ...]:
    return tuple(_canonical_fact(_row_value(row, name)) for name in spec.factual_columns)


def _row_value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def _canonical_fact(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return as_utc(value, field_name="factual datetime").isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _canonical_fact(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_fact(item) for item in value)
    return value


def _map_record_batches(
    records: Sequence[Any],
    *,
    source_id: str,
    raw_payload_id: UUID,
    asset_ids: Mapping[str, UUID] | None = None,
) -> tuple[tuple[_UpsertSpec, list[dict[str, Any]]], ...]:
    asset_ids = asset_ids or {}
    grouped: dict[_UpsertSpec, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if isinstance(record, (AssetReference, REPDSite)):
            # Reference snapshots are handled first so dependent FK rows can be
            # resolved in the same transaction. REPD complete-snapshot rows are
            # likewise persisted by their deliberately scoped path.
            continue
        if isinstance(record, PlannedProfileSegment):
            grouped[_PHYSICAL_NOTIFICATION_SPEC].append(
                map_physical_notification_segment(
                    record,
                    source_id=source_id,
                    raw_payload_id=raw_payload_id,
                    asset_id=_required_asset_id(asset_ids, record.asset_id),
                )
            )
        elif isinstance(record, SettledMeteredEnergy):
            grouped[_B1610_SETTLED_ENERGY_SPEC].append(
                map_b1610_settled_energy(
                    record,
                    source_id=source_id,
                    raw_payload_id=raw_payload_id,
                    asset_id=_required_asset_id(asset_ids, record.asset_id),
                )
            )
        elif isinstance(record, GenerationRecord):
            grouped[_GENERATION_SPEC].append(
                map_generation_record(
                    record, source_id=source_id, raw_payload_id=raw_payload_id
                )
            )
        elif isinstance(record, DemandRecord):
            grouped[_DEMAND_SPEC].append(
                map_demand_record(
                    record, source_id=source_id, raw_payload_id=raw_payload_id
                )
            )
        elif isinstance(record, FrequencyRecord):
            grouped[_FREQUENCY_SPEC].append(
                map_frequency_record(
                    record, source_id=source_id, raw_payload_id=raw_payload_id
                )
            )
        elif isinstance(record, InterconnectorFlowRecord):
            grouped[_INTERCONNECTOR_SPEC].append(
                map_interconnector_record(
                    record, source_id=source_id, raw_payload_id=raw_payload_id
                )
            )
        elif isinstance(record, CarbonIntensityRecord):
            if record.classification is SourceDataClassification.ESTIMATED:
                grouped[_CARBON_ACTUAL_SPEC].append(
                    map_carbon_actual_record(
                        record, source_id=source_id, raw_payload_id=raw_payload_id
                    )
                )
            elif record.classification is SourceDataClassification.FORECAST:
                grouped[_FORECAST_SPEC].append(
                    map_carbon_forecast_record(
                        record, source_id=source_id, raw_payload_id=raw_payload_id
                    )
                )
            else:
                raise ValueError("carbon intensity records must be estimated or forecast")
        elif isinstance(record, DemandForecastRecord):
            grouped[_FORECAST_SPEC].append(
                map_demand_forecast_record(
                    record, source_id=source_id, raw_payload_id=raw_payload_id
                )
            )
        elif isinstance(record, WindForecastRecord):
            grouped[_FORECAST_SPEC].append(
                map_wind_forecast_record(
                    record, source_id=source_id, raw_payload_id=raw_payload_id
                )
            )
        elif isinstance(record, RemitUnavailabilityRecord):
            grouped[_REPORTED_NOTICE_SPEC].append(
                map_remit_notice_record(
                    record, source_id=source_id, raw_payload_id=raw_payload_id
                )
            )
        elif isinstance(record, SystemWarningRecord):
            grouped[_REPORTED_NOTICE_SPEC].append(
                map_system_warning_record(
                    record, source_id=source_id, raw_payload_id=raw_payload_id
                )
            )
        elif isinstance(record, DistributionIncidentRecord):
            grouped[_DISTRIBUTION_INCIDENT_SPEC].append(
                map_distribution_incident_record(
                    record,
                    source_id=source_id,
                    raw_payload_id=raw_payload_id,
                )
            )
        else:  # pragma: no cover - guarded before the transaction begins
            raise TypeError(f"unsupported normalized record: {type(record).__name__}")
    ordered_specs = (
        _GENERATION_SPEC,
        _DEMAND_SPEC,
        _FREQUENCY_SPEC,
        _INTERCONNECTOR_SPEC,
        _CARBON_ACTUAL_SPEC,
        _FORECAST_SPEC,
        _REPORTED_NOTICE_SPEC,
        _DISTRIBUTION_INCIDENT_SPEC,
        _PHYSICAL_NOTIFICATION_SPEC,
        _B1610_SETTLED_ENERGY_SPEC,
    )
    return tuple((spec, grouped[spec]) for spec in ordered_specs if grouped[spec])


def _validate_record_types(records: Sequence[Any]) -> None:
    supported = (
        AssetReference,
        REPDSite,
        PlannedProfileSegment,
        SettledMeteredEnergy,
        GenerationRecord,
        DemandRecord,
        FrequencyRecord,
        InterconnectorFlowRecord,
        CarbonIntensityRecord,
        DemandForecastRecord,
        WindForecastRecord,
        RemitUnavailabilityRecord,
        SystemWarningRecord,
        DistributionIncidentRecord,
    )
    for record in records:
        if not isinstance(record, supported):
            raise TypeError(f"unsupported normalized record: {type(record).__name__}")


def _deduplicate_rows(
    rows: Sequence[dict[str, Any]], conflict_columns: Sequence[str]
) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row[column] for column in conflict_columns)
        by_key[key] = row
    return list(by_key.values()), len(rows) - len(by_key)


def _is_distribution_snapshot(result: AdapterResult[Any]) -> bool:
    return (
        result.source_id == "ukpn.live_faults"
        and result.dataset.upper() == "LIVE_FAULTS"
    )


def _is_physical_notification_snapshot(result: AdapterResult[Any]) -> bool:
    return result.source_id == "elexon.pn" and result.dataset.upper() == "PN"


def _is_repd_snapshot(result: AdapterResult[Any]) -> bool:
    return result.source_id == REPD_SOURCE_ID and result.dataset.upper() == "REPD"


def _validate_repd_snapshot_contract(result: AdapterResult[Any]) -> None:
    """Require proof of a complete parsed extract before membership changes.

    A missing, empty, mixed, or manually sliced result must fail before opening
    a database transaction. This keeps the complete-snapshot deactivation path
    available only to the official adapter contract.
    """

    repd_records = tuple(
        record for record in result.records if isinstance(record, REPDSite)
    )
    is_repd_source = _is_repd_snapshot(result)
    if repd_records and not is_repd_source:
        raise ValueError("REPD sites must use the canonical DESNZ source contract")
    if not is_repd_source:
        return
    if not result.records:
        raise ValueError("a complete REPD snapshot cannot be empty")
    if len(repd_records) != len(result.records):
        raise ValueError("a complete REPD snapshot cannot mix record types")
    if result.metadata.get("snapshotKind") != "complete_reference":
        raise ValueError("REPD result is not declared as a complete reference snapshot")

    record_count = result.metadata.get("recordCount")
    if isinstance(record_count, bool) or record_count != len(repd_records):
        raise ValueError("REPD snapshot metadata record count does not match records")
    if not isinstance(result.raw_payload, Mapping):
        raise ValueError("REPD snapshot raw payload must be a parse summary object")
    parse_summary = result.raw_payload.get("parse")
    if not isinstance(parse_summary, Mapping):
        raise ValueError("REPD snapshot is missing its complete parse summary")
    retained_rows = parse_summary.get("retainedRows")
    if isinstance(retained_rows, bool) or retained_rows != len(repd_records):
        raise ValueError("REPD parse summary does not match snapshot membership")
    source_ids = [record.source_id.strip() for record in repd_records]
    if any(not source_id for source_id in source_ids):
        raise ValueError("REPD snapshot contains an empty source record ID")
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("REPD complete snapshot contains duplicate source record IDs")


def _required_asset_id(asset_ids: Mapping[str, UUID], external_id: str) -> UUID:
    try:
        return asset_ids[external_id]
    except KeyError as exc:  # pragma: no cover - guarded by placeholder creation
        raise ValueError(f"BM-unit asset was not resolved: {external_id}") from exc


async def _persist_asset_reference_snapshot(
    session: AsyncSession,
    *,
    source_id: str,
    records: Sequence[AssetReference],
) -> tuple[int, int, int]:
    if source_id != BM_UNIT_REFERENCE_SOURCE_ID:
        raise ValueError("BM-unit references must use their canonical source ID")
    rows, duplicate_count = _merge_asset_reference_rows(
        [map_asset_reference(record, source_id=source_id) for record in records]
    )
    for row in rows:
        row["id"] = uuid.uuid5(
            _BM_UNIT_ASSET_NAMESPACE,
            f"{source_id}|{row['external_id']}",
        )

    inserted = 0
    updated_count = 0
    unchanged = duplicate_count
    for write_rows in _normalized_write_batches(rows):
        result = await session.execute(
            _observation_upsert(_ASSET_REFERENCE_SPEC, write_rows)
        )
        flags = list(result.scalars().all())
        inserted += sum(1 for flag in flags if bool(flag))
        updated_count += sum(1 for flag in flags if not bool(flag))
        unchanged += len(write_rows) - len(flags)

    external_ids = tuple(row["external_id"] for row in rows)
    if external_ids:
        deactivated = await session.execute(
            update(Asset)
            .where(
                Asset.source_id == source_id,
                Asset.asset_type == "bm_unit",
                Asset.active.is_(True),
                Asset.attributes["classification"].as_string() == "reference",
                Asset.external_id.not_in(external_ids),
            )
            .values(active=False, updated_at=func.now())
            .returning(Asset.id)
        )
        updated_count += len(deactivated.scalars().all())
    return inserted, updated_count, unchanged


async def _persist_repd_snapshot(
    session: AsyncSession,
    *,
    source_id: str,
    records: Sequence[REPDSite],
) -> tuple[int, int, int]:
    """Atomically upsert one complete REPD extract and retire absent sites."""

    if source_id != REPD_SOURCE_ID:
        raise ValueError("REPD sites must use their canonical source ID")
    if not records:
        raise ValueError("refusing to persist an empty REPD complete snapshot")
    rows = [map_repd_site(record, source_id=source_id) for record in records]
    membership = repd_snapshot_membership(records, source_id=source_id)
    external_ids = membership.active_external_ids
    if len(external_ids) != len(rows):
        raise ValueError("REPD complete snapshot contains duplicate external IDs")
    for row in rows:
        row["id"] = uuid.uuid5(
            _REPD_ASSET_NAMESPACE,
            f"{source_id}|{row['external_id']}",
        )

    inserted = 0
    updated_count = 0
    unchanged = 0
    for write_rows in _normalized_write_batches(rows):
        result = await session.execute(
            _observation_upsert(_ASSET_REFERENCE_SPEC, write_rows)
        )
        flags = list(result.scalars().all())
        inserted += sum(1 for flag in flags if bool(flag))
        updated_count += sum(1 for flag in flags if not bool(flag))
        unchanged += len(write_rows) - len(flags)

    # Scope deactivation to this publisher and this reference asset family.
    # The non-empty guard above ensures a malformed/partial run can never turn
    # this into a source-wide mass retirement.
    deactivated = await session.execute(
        update(Asset)
        .where(
            Asset.source_id == source_id,
            Asset.asset_type == REPD_ASSET_TYPE,
            Asset.active.is_(True),
            Asset.external_id.not_in(external_ids),
        )
        .values(active=False, updated_at=func.now())
        .returning(Asset.id)
    )
    updated_count += len(deactivated.scalars().all())
    return inserted, updated_count, unchanged


def _merge_asset_reference_rows(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse duplicate national IDs without discarding source variants."""

    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    duplicate_count = 0
    for candidate in rows:
        key = tuple(
            candidate[column]
            for column in _ASSET_REFERENCE_SPEC.conflict_columns
        )
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = candidate
            continue
        duplicate_count += 1
        existing_attributes = existing["attributes"]
        candidate_attributes = candidate["attributes"]
        variants = list(existing_attributes.get("referenceVariants", ()))
        if not variants:
            variants.append(_reference_variant(existing_attributes))
        next_variant = _reference_variant(candidate_attributes)
        if next_variant not in variants:
            variants.append(next_variant)
        merged = dict(candidate)
        merged["attributes"] = {
            **candidate_attributes,
            "referenceVariants": variants,
        }
        by_key[key] = merged
    return list(by_key.values()), duplicate_count


def _reference_variant(attributes: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in attributes.items()
        if key
        not in {
            "classification",
            "locationStatus",
            "provenance",
            "referenceVariants",
        }
    }


async def _ensure_bm_unit_assets(
    session: AsyncSession,
    records: Sequence[PlannedProfileSegment | SettledMeteredEnergy],
) -> dict[str, UUID]:
    """Create non-geographic placeholders when dependent data wins the race."""

    await session.execute(
        _source_metadata_upsert(bm_unit_reference_source_metadata_values())
    )
    by_external_id: dict[str, tuple[str | None, str | None]] = {}
    for record in records:
        national_grid_bm_unit = (
            record.asset_id
            if isinstance(record, PlannedProfileSegment)
            else record.national_grid_bm_unit
        )
        by_external_id.setdefault(
            record.asset_id,
            (national_grid_bm_unit, record.source_asset_id),
        )
    placeholder_rows = [
        {
            "id": uuid.uuid5(
                _BM_UNIT_ASSET_NAMESPACE,
                f"{BM_UNIT_REFERENCE_SOURCE_ID}|{external_id}",
            ),
            "source_id": BM_UNIT_REFERENCE_SOURCE_ID,
            "external_id": external_id,
            "asset_type": "bm_unit",
            "display_name": (
                source_asset_id or national_grid_bm_unit or external_id
            )[:160],
            "fuel_type": None,
            "region_code": None,
            "counterparty": None,
            "capacity_mw": None,
            "latitude": None,
            "longitude": None,
            "map_x": None,
            "map_y": None,
            "active": True,
            "attributes": {
                "classification": "reference_placeholder",
                "nationalGridBmUnit": national_grid_bm_unit,
                "elexonBmUnit": source_asset_id,
                "locationStatus": "not_provided_by_elexon",
            },
        }
        for external_id, (
            national_grid_bm_unit,
            source_asset_id,
        ) in by_external_id.items()
    ]
    for rows in _normalized_write_batches(placeholder_rows):
        await session.execute(
            pg_insert(Asset)
            .values(list(rows))
            .on_conflict_do_update(
                index_elements=[Asset.source_id, Asset.external_id],
                set_={"active": True, "updated_at": func.now()},
            )
        )
    identifiers = tuple(by_external_id)
    resolved = (
        await session.execute(
            select(Asset.external_id, Asset.id).where(
                Asset.source_id == BM_UNIT_REFERENCE_SOURCE_ID,
                Asset.external_id.in_(identifiers),
            )
        )
    ).all()
    asset_ids = {external_id: asset_id for external_id, asset_id in resolved}
    missing = set(identifiers) - set(asset_ids)
    if missing:
        raise ValueError(f"failed to resolve {len(missing)} BM-unit asset(s)")
    return asset_ids


async def _prune_physical_notification_scope(
    session: AsyncSession,
    *,
    source_id: str,
    metadata: Mapping[str, Any],
    rows: Sequence[dict[str, Any]],
) -> None:
    """Remove only rows absent from the adapter-declared PN query scope."""

    raw_date = metadata.get("settlementDate")
    raw_period = metadata.get("settlementPeriod")
    all_units = metadata.get("allUnits")
    bm_units = metadata.get("bmUnits")
    if not isinstance(raw_date, str):
        raise ValueError("PN snapshot metadata is missing settlementDate")
    try:
        settlement_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError("PN snapshot settlementDate is invalid") from exc
    if isinstance(raw_period, bool) or not isinstance(raw_period, int):
        raise ValueError("PN snapshot settlementPeriod is invalid")
    if not 1 <= raw_period <= 50:
        raise ValueError("PN snapshot settlementPeriod is invalid")
    if not isinstance(all_units, bool) or not isinstance(bm_units, list):
        raise ValueError("PN snapshot unit scope is invalid")
    if not all(isinstance(unit, str) and unit.strip() for unit in bm_units):
        raise ValueError("PN snapshot BM-unit scope is invalid")
    if all_units and bm_units:
        raise ValueError("all-unit PN scope cannot also list BM units")
    if not all_units and not bm_units:
        raise ValueError("bounded PN scope must list at least one BM unit")

    for row in rows:
        if (
            row["settlement_date"] != settlement_date
            or row["settlement_period"] != raw_period
        ):
            raise ValueError("PN row escaped declared settlement scope")
        if not all_units and row["elexon_bm_unit"] not in bm_units:
            raise ValueError("PN row escaped declared BM-unit scope")

    # This is a current-state table, not a PN history archive. Retire older
    # periods for exactly the same declared unit scope before replacing current
    # membership. Both deletes and the upsert remain in the ingestion transaction.
    stale_periods = delete(PhysicalNotificationSegmentCurrent).where(
        PhysicalNotificationSegmentCurrent.source_id == source_id,
        or_(
            PhysicalNotificationSegmentCurrent.settlement_date != settlement_date,
            PhysicalNotificationSegmentCurrent.settlement_period != raw_period,
        ),
    )
    if not all_units:
        stale_periods = stale_periods.where(
            PhysicalNotificationSegmentCurrent.elexon_bm_unit.in_(bm_units)
        )
    await session.execute(stale_periods)

    scope = (
        delete(PhysicalNotificationSegmentCurrent)
        .where(
            PhysicalNotificationSegmentCurrent.source_id == source_id,
            PhysicalNotificationSegmentCurrent.settlement_date == settlement_date,
            PhysicalNotificationSegmentCurrent.settlement_period == raw_period,
        )
    )
    if not all_units:
        scope = scope.where(
            PhysicalNotificationSegmentCurrent.elexon_bm_unit.in_(bm_units)
        )
    present_keys = tuple(
        (
            row["national_grid_bm_unit"],
            row["segment_start"],
            row["segment_end"],
        )
        for row in rows
    )
    if present_keys:
        scope = scope.where(
            tuple_(
                PhysicalNotificationSegmentCurrent.national_grid_bm_unit,
                PhysicalNotificationSegmentCurrent.segment_start,
                PhysicalNotificationSegmentCurrent.segment_end,
            ).not_in(present_keys)
        )
    await session.execute(scope)


async def _refresh_distribution_incident_current(
    session: AsyncSession,
    *,
    source_id: str,
    records: Sequence[DistributionIncidentRecord],
    seen_at: datetime,
) -> None:
    """Atomically replace current membership without deleting revision history."""

    seen_at = as_utc(seen_at, field_name="seen_at")
    await session.execute(
        update(DistributionIncidentCurrent)
        .where(
            DistributionIncidentCurrent.source_id == source_id,
            DistributionIncidentCurrent.present.is_(True),
        )
        .values(present=False, updated_at=seen_at)
    )
    by_reference = {record.incident_reference: record for record in records}
    if not by_reference:
        return
    rows = [
        {
            "id": uuid.uuid5(
                _DISTRIBUTION_CURRENT_NAMESPACE,
                f"{source_id}|{record.incident_reference}",
            ),
            "source_id": source_id,
            "incident_reference": record.incident_reference,
            "status": record.status,
            "present": True,
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
            "updated_at": seen_at,
        }
        for record in by_reference.values()
    ]
    for write_rows in _normalized_write_batches(rows):
        statement = pg_insert(DistributionIncidentCurrent).values(list(write_rows))
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    DistributionIncidentCurrent.source_id,
                    DistributionIncidentCurrent.incident_reference,
                ],
                set_={
                    "status": statement.excluded.status,
                    "present": True,
                    "last_seen_at": statement.excluded.last_seen_at,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )


def _success_idempotency_key(job_id: str, result: AdapterResult[Any]) -> str:
    material = "|".join(
        (
            "success",
            job_id,
            result.window.start.isoformat(),
            result.window.end.isoformat(),
            result.checksum_sha256.lower(),
        )
    )
    return _bounded_key(job_id, material)


def _failure_idempotency_key(
    job_id: str,
    window: ObservationWindow,
    attempted_at: datetime,
    error_type: str,
) -> str:
    material = "|".join(
        (
            "failure",
            job_id,
            window.start.isoformat(),
            window.end.isoformat(),
            attempted_at.astimezone(UTC).isoformat(),
            error_type,
        )
    )
    return _bounded_key(job_id, material)


def _bounded_key(job_id: str, material: str) -> str:
    prefix = re_safe_job_id(job_id)[:80]
    return f"{prefix}:{hashlib.sha256(material.encode()).hexdigest()}"


def re_safe_job_id(job_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", job_id).strip("-")
    return value or "job"
