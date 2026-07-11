from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base
from app.domain.enums import (
    EventSeverity,
    EventStatus,
    EvidenceConfidence,
    FactQuality,
    FreshnessState,
    IngestionRunStatus,
)


JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def _enum_type(enum_class: type, name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ObservationMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_metadata.id", ondelete="RESTRICT"), nullable=False
    )
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_payloads.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[str | None] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    quality: Mapped[FactQuality] = mapped_column(
        _enum_type(FactQuality, "fact_quality"),
        nullable=False,
        default=FactQuality.VALIDATED,
        server_default=FactQuality.VALIDATED.value,
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceMetadata(TimestampMixin, Base):
    __tablename__ = "source_metadata"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    documentation_url: Mapped[str | None] = mapped_column(String(500))
    licence_name: Mapped[str | None] = mapped_column(String(160))
    licence_url: Mapped[str | None] = mapped_column(String(500))
    attribution: Mapped[str | None] = mapped_column(Text)
    expected_cadence_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (
        CheckConstraint(
            "expected_cadence_seconds > 0", name="positive_expected_cadence"
        ),
        UniqueConstraint("provider", "dataset", name="uq_source_provider_dataset"),
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_metadata.id", ondelete="RESTRICT"), nullable=False
    )
    adapter: Mapped[str] = mapped_column(String(160), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    requested_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[IngestionRunStatus] = mapped_column(
        _enum_type(IngestionRunStatus, "ingestion_run_status"),
        nullable=False,
        default=IngestionRunStatus.RUNNING,
        server_default=IngestionRunStatus.RUNNING.value,
    )
    records_received: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    records_written: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cursor: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict, server_default=text("'{}'")
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)

    __table_args__ = (
        CheckConstraint("records_received >= 0", name="nonnegative_records_received"),
        CheckConstraint("records_written >= 0", name="nonnegative_records_written"),
        Index("ix_ingestion_runs_source_status_started", "source_id", "status", "started_at"),
    )


class RawPayload(Base):
    __tablename__ = "raw_payloads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_metadata.id", ondelete="RESTRICT"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    content_type: Mapped[str | None] = mapped_column(String(160))
    etag: Mapped[str | None] = mapped_column(String(500))
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id", "endpoint", "checksum_sha256", name="uq_raw_payload_content"
        ),
        Index("ix_raw_payloads_source_retrieved", "source_id", "retrieved_at"),
    )


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_metadata.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    fuel_type: Mapped[str | None] = mapped_column(String(64))
    region_code: Mapped[str | None] = mapped_column(String(64))
    counterparty: Mapped[str | None] = mapped_column(String(120))
    capacity_mw: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    map_x: Mapped[float | None] = mapped_column(Float)
    map_y: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict, server_default=text("'{}'")
    )

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_asset_source_external"),
        CheckConstraint(
            "capacity_mw IS NULL OR capacity_mw >= 0", name="nonnegative_capacity"
        ),
        CheckConstraint(
            "map_x IS NULL OR (map_x >= 0 AND map_x <= 1)", name="normalized_map_x"
        ),
        CheckConstraint(
            "map_y IS NULL OR (map_y >= 0 AND map_y <= 1)", name="normalized_map_y"
        ),
        Index("ix_assets_type_active", "asset_type", "active"),
    )


class GenerationObservation(ObservationMixin, Base):
    __tablename__ = "generation_observations"

    series_key: Mapped[str] = mapped_column(String(120), nullable=False)
    fuel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    generation_mw: Mapped[float] = mapped_column(Float, nullable=False)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    settlement_period: Mapped[int | None] = mapped_column(SmallInteger)

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "series_key",
            "observed_at",
            "revision",
            name="uq_generation_source_series_time_revision",
        ),
        CheckConstraint(
            "settlement_period IS NULL OR settlement_period BETWEEN 1 AND 50",
            name="valid_settlement_period",
        ),
        Index("ix_generation_observed_at", "observed_at"),
        Index("ix_generation_fuel_observed", "fuel_type", "observed_at"),
    )


class DemandObservation(ObservationMixin, Base):
    __tablename__ = "demand_observations"

    series_key: Mapped[str] = mapped_column(String(120), nullable=False, default="gb")
    demand_type: Mapped[str] = mapped_column(String(64), nullable=False)
    demand_mw: Mapped[float] = mapped_column(Float, nullable=False)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    settlement_period: Mapped[int | None] = mapped_column(SmallInteger)

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "series_key",
            "demand_type",
            "observed_at",
            "revision",
            name="uq_demand_source_series_type_time_revision",
        ),
        CheckConstraint(
            "settlement_period IS NULL OR settlement_period BETWEEN 1 AND 50",
            name="valid_settlement_period",
        ),
        Index("ix_demand_observed_at", "observed_at"),
    )


class FrequencyObservation(ObservationMixin, Base):
    __tablename__ = "frequency_observations"

    series_key: Mapped[str] = mapped_column(String(120), nullable=False, default="gb")
    frequency_hz: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "series_key",
            "observed_at",
            "revision",
            name="uq_frequency_source_series_time_revision",
        ),
        Index("ix_frequency_observed_at", "observed_at"),
    )


class InterconnectorObservation(ObservationMixin, Base):
    __tablename__ = "interconnector_observations"

    connector_code: Mapped[str] = mapped_column(String(120), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    counterparty: Mapped[str] = mapped_column(String(120), nullable=False)
    flow_mw: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="Signed MW: positive imports into Britain; negative exports.",
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "connector_code",
            "observed_at",
            "revision",
            name="uq_interconnector_source_connector_time_revision",
        ),
        Index("ix_interconnector_observed_at", "observed_at"),
    )


class CarbonObservation(ObservationMixin, Base):
    __tablename__ = "carbon_observations"

    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    intensity_gco2_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    index_label: Mapped[str | None] = mapped_column(String(32))
    generation_mix: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list, server_default=text("'[]'")
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "region_code",
            "observed_at",
            "revision",
            name="uq_carbon_source_region_time_revision",
        ),
        CheckConstraint("intensity_gco2_kwh >= 0", name="nonnegative_intensity"),
        Index("ix_carbon_region_observed", "region_code", "observed_at"),
    )


class ForecastObservation(Base):
    __tablename__ = "forecast_observations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_metadata.id", ondelete="RESTRICT"), nullable=False
    )
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_payloads.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[str | None] = mapped_column(String(200))
    metric_type: Mapped[str] = mapped_column(String(80), nullable=False)
    series_key: Mapped[str] = mapped_column(String(120), nullable=False)
    variant: Mapped[str] = mapped_column(
        String(64), nullable=False, default="point", server_default="point"
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    value_low: Mapped[float | None] = mapped_column(Float)
    value_high: Mapped[float | None] = mapped_column(Float)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(120))
    settlement_date: Mapped[date | None] = mapped_column(Date)
    settlement_period: Mapped[int | None] = mapped_column(SmallInteger)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "metric_type",
            "series_key",
            "variant",
            "valid_from",
            "issued_at",
            name="uq_forecast_source_metric_series_variant_valid_issue",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="valid_forecast_window"
        ),
        CheckConstraint(
            "value_low IS NULL OR value_high IS NULL OR value_low <= value_high",
            name="ordered_forecast_interval",
        ),
        CheckConstraint(
            "settlement_period IS NULL OR settlement_period BETWEEN 1 AND 50",
            name="valid_settlement_period",
        ),
        Index("ix_forecast_metric_valid", "metric_type", "valid_from"),
        Index("ix_forecast_series_issue", "series_key", "issued_at"),
    )


class GridSnapshotRecord(Base):
    __tablename__ = "grid_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    schema_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="1.0", server_default="1.0"
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, unique=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    freshness: Mapped[FreshnessState] = mapped_column(
        _enum_type(FreshnessState, "freshness_state"), nullable=False
    )
    generation_total_mw: Mapped[float | None] = mapped_column(Float)
    demand_mw: Mapped[float | None] = mapped_column(Float)
    frequency_hz: Mapped[float | None] = mapped_column(Float)
    carbon_intensity_gco2_kwh: Mapped[float | None] = mapped_column(Float)
    net_import_mw: Mapped[float | None] = mapped_column(Float)
    completeness: Mapped[float] = mapped_column(Float, nullable=False)
    generation_mix: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list, server_default=text("'[]'")
    )
    interconnectors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list, server_default=text("'[]'")
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    missing_datasets: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list, server_default=text("'[]'")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "completeness >= 0 AND completeness <= 1", name="bounded_completeness"
        ),
        Index("ix_grid_snapshots_generated_at", "generated_at"),
    )


class DetectedEvent(TimestampMixin, Base):
    __tablename__ = "detected_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    deterministic_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[EventStatus] = mapped_column(
        _enum_type(EventStatus, "event_status"), nullable=False
    )
    severity: Mapped[EventSeverity] = mapped_column(
        _enum_type(EventSeverity, "event_severity"), nullable=False
    )
    confidence: Mapped[EvidenceConfidence] = mapped_column(
        _enum_type(EvidenceConfidence, "evidence_confidence"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    deterministic_summary: Mapped[str | None] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    evidence_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    related_asset_ids: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, nullable=False, default=list, server_default=text("'[]'")
    )
    event_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("evidence_version > 0", name="positive_evidence_version"),
        Index("ix_detected_events_status_severity", "status", "severity"),
        Index("ix_detected_events_type_started", "event_type", "event_started_at"),
    )


class EventExplanation(Base):
    __tablename__ = "event_explanations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detected_events.id", ondelete="CASCADE"), nullable=False
    )
    evidence_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    locale: Mapped[str] = mapped_column(
        String(20), nullable=False, default="en-GB", server_default="en-GB"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    structured_response: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "evidence_checksum",
            "provider",
            "model",
            "prompt_version",
            "locale",
            name="uq_event_explanation_cache_key",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="nonnegative_input_tokens"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="nonnegative_output_tokens"
        ),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="nonnegative_cost"),
        Index("ix_event_explanations_event_generated", "event_id", "generated_at"),
    )
