from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CarbonObservation,
    DemandObservation,
    ForecastObservation,
    FrequencyObservation,
    GenerationObservation,
    IngestionRun,
    InterconnectorObservation,
    RawPayload,
    ReportedNotice,
    SourceMetadata,
)
from app.domain.enums import IngestionRunStatus
from app.persistence.event_lifecycle import materialize_reported_notice_rows
from app.persistence.records import (
    job_source_metadata_values,
    map_carbon_actual_record,
    map_carbon_forecast_record,
    map_demand_forecast_record,
    map_demand_record,
    map_frequency_record,
    map_generation_record,
    map_interconnector_record,
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
    FrequencyRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
    ObservationWindow,
    RemitUnavailabilityRecord,
    SystemWarningRecord,
    WindForecastRecord,
    as_utc,
)
from app.worker.contracts import IngestionCheckpoint, PersistOutcome


SessionFactory = Callable[[], AsyncSession]

_RUN_NAMESPACE = UUID("b1f130e2-ec87-5a79-88a3-a3ed99a5321c")
_RAW_PAYLOAD_NAMESPACE = UUID("40321ac1-3394-58d2-9cf7-33dbb7187b6c")


@dataclass(frozen=True, slots=True)
class _UpsertSpec:
    model: type
    conflict_columns: tuple[str, ...]
    update_columns: tuple[str, ...]


_GENERATION_SPEC = _UpsertSpec(
    model=GenerationObservation,
    conflict_columns=("source_id", "series_key", "observed_at", "revision"),
    update_columns=(
        "raw_payload_id",
        "source_record_id",
        "fuel_type",
        "asset_id",
        "generation_mw",
        "settlement_date",
        "settlement_period",
        "published_at",
        "retrieved_at",
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
    update_columns=(
        "raw_payload_id",
        "source_record_id",
        "demand_mw",
        "settlement_date",
        "settlement_period",
        "published_at",
        "retrieved_at",
        "quality",
        "attributes",
    ),
)
_FREQUENCY_SPEC = _UpsertSpec(
    model=FrequencyObservation,
    conflict_columns=("source_id", "series_key", "observed_at", "revision"),
    update_columns=(
        "raw_payload_id",
        "source_record_id",
        "frequency_hz",
        "published_at",
        "retrieved_at",
        "quality",
        "attributes",
    ),
)
_INTERCONNECTOR_SPEC = _UpsertSpec(
    model=InterconnectorObservation,
    conflict_columns=("source_id", "connector_code", "observed_at", "revision"),
    update_columns=(
        "raw_payload_id",
        "source_record_id",
        "asset_id",
        "counterparty",
        "flow_mw",
        "published_at",
        "retrieved_at",
        "quality",
        "attributes",
    ),
)
_CARBON_ACTUAL_SPEC = _UpsertSpec(
    model=CarbonObservation,
    conflict_columns=("source_id", "region_code", "observed_at", "revision"),
    update_columns=(
        "raw_payload_id",
        "source_record_id",
        "intensity_gco2_kwh",
        "index_label",
        "generation_mix",
        "published_at",
        "retrieved_at",
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
    ),
    update_columns=(
        "raw_payload_id",
        "source_record_id",
        "value",
        "unit",
        "value_low",
        "value_high",
        "valid_to",
        "published_at",
        "retrieved_at",
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
        if re.fullmatch(r"[0-9a-f]{64}", result.checksum_sha256.lower()) is None:
            raise ValueError("raw payload checksum must be a SHA-256 hex digest")
        checksum_sha256 = result.checksum_sha256.lower()

        metadata = source_metadata_values(
            provider=result.source_id,
            dataset=result.dataset,
            request_url=result.request_url,
        )
        source_id = metadata["id"]
        idempotency_key = _success_idempotency_key(job_id, result)
        proposed_run_id = uuid.uuid5(_RUN_NAMESPACE, idempotency_key)
        proposed_raw_id = uuid.uuid5(
            _RAW_PAYLOAD_NAMESPACE,
            f"{source_id}|{result.endpoint}|{checksum_sha256}",
        )
        _validate_record_types(result.records)

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

                batches = _map_record_batches(
                    result.records,
                    source_id=source_id,
                    raw_payload_id=raw_payload_id,
                )
                inserted = 0
                updated_count = 0
                unchanged = 0
                reported_notice_rows: list[dict[str, Any]] = []
                for spec, rows in batches:
                    if spec is _REPORTED_NOTICE_SPEC:
                        reported_notice_rows.extend(rows)
                    unique_rows, duplicate_count = _deduplicate_rows(
                        rows, spec.conflict_columns
                    )
                    unchanged += duplicate_count
                    if not unique_rows:
                        continue
                    write_result = await session.execute(
                        _observation_upsert(spec, unique_rows)
                    )
                    insert_flags = list(write_result.scalars().all())
                    inserted += sum(1 for flag in insert_flags if bool(flag))
                    updated_count += sum(1 for flag in insert_flags if not bool(flag))
                    unchanged += len(unique_rows) - len(insert_flags)

                if reported_notice_rows:
                    await materialize_reported_notice_rows(
                        session,
                        reported_notice_rows,
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
            "active": True,
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
            for column in spec.update_columns
        )
    )
    return statement.on_conflict_do_update(
        index_elements=[getattr(spec.model, name) for name in spec.conflict_columns],
        set_=update_values,
        where=changed,
    ).returning(literal_column("xmax = 0", Boolean).label("was_inserted"))


def _map_record_batches(
    records: Sequence[Any],
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> tuple[tuple[_UpsertSpec, list[dict[str, Any]]], ...]:
    grouped: dict[_UpsertSpec, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if isinstance(record, GenerationRecord):
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
    )
    return tuple((spec, grouped[spec]) for spec in ordered_specs if grouped[spec])


def _validate_record_types(records: Sequence[Any]) -> None:
    supported = (
        GenerationRecord,
        DemandRecord,
        FrequencyRecord,
        InterconnectorFlowRecord,
        CarbonIntensityRecord,
        DemandForecastRecord,
        WindForecastRecord,
        RemitUnavailabilityRecord,
        SystemWarningRecord,
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
