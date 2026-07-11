"""Create the initial auditable grid-data schema.

Revision ID: 20260711_0001
Revises: None
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260711_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def _observation_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        sa.Column("source_record_id", sa.String(200), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "quality",
            _enum("fact_quality", "validated", "provisional", "estimated"),
            server_default="validated",
            nullable=False,
        ),
        sa.Column("attributes", _json(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"], ["raw_payloads.id"], ondelete="SET NULL"
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "source_metadata",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("dataset", sa.String(160), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("documentation_url", sa.String(500), nullable=True),
        sa.Column("licence_name", sa.String(160), nullable=True),
        sa.Column("licence_url", sa.String(500), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("expected_cadence_seconds", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "expected_cadence_seconds > 0",
            name="positive_expected_cadence",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "dataset", name="uq_source_provider_dataset"),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("adapter", sa.String(160), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("requested_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            _enum(
                "ingestion_run_status", "running", "succeeded", "partial", "failed"
            ),
            server_default="running",
            nullable=False,
        ),
        sa.Column("records_received", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cursor", _json(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error", _json(), nullable=True),
        sa.CheckConstraint(
            "records_received >= 0", name="nonnegative_records_received"
        ),
        sa.CheckConstraint(
            "records_written >= 0", name="nonnegative_records_written"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_ingestion_runs_idempotency_key"),
    )
    op.create_index(
        "ix_ingestion_runs_source_status_started",
        "ingestion_runs",
        ["source_id", "status", "started_at"],
    )

    op.create_table(
        "raw_payloads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("content_type", sa.String(160), nullable=True),
        sa.Column("etag", sa.String(500), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"], ["ingestion_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id", "endpoint", "checksum_sha256", name="uq_raw_payload_content"
        ),
    )
    op.create_index(
        "ix_raw_payloads_source_retrieved",
        "raw_payloads",
        ["source_id", "retrieved_at"],
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=False),
        sa.Column("asset_type", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("fuel_type", sa.String(64), nullable=True),
        sa.Column("region_code", sa.String(64), nullable=True),
        sa.Column("counterparty", sa.String(120), nullable=True),
        sa.Column("capacity_mw", sa.Float(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("map_x", sa.Float(), nullable=True),
        sa.Column("map_y", sa.Float(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("attributes", _json(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "capacity_mw IS NULL OR capacity_mw >= 0",
            name="nonnegative_capacity",
        ),
        sa.CheckConstraint(
            "map_x IS NULL OR (map_x >= 0 AND map_x <= 1)",
            name="normalized_map_x",
        ),
        sa.CheckConstraint(
            "map_y IS NULL OR (map_y >= 0 AND map_y <= 1)",
            name="normalized_map_y",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_asset_source_external"),
    )
    op.create_index("ix_assets_type_active", "assets", ["asset_type", "active"])

    op.create_table(
        "generation_observations",
        *_observation_columns(),
        sa.Column("series_key", sa.String(120), nullable=False),
        sa.Column("fuel_type", sa.String(64), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("generation_mw", sa.Float(), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=True),
        sa.Column("settlement_period", sa.SmallInteger(), nullable=True),
        sa.CheckConstraint(
            "settlement_period IS NULL OR settlement_period BETWEEN 1 AND 50",
            name="valid_settlement_period",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "source_id",
            "series_key",
            "observed_at",
            "revision",
            name="uq_generation_source_series_time_revision",
        ),
    )
    op.create_index(
        "ix_generation_observed_at", "generation_observations", ["observed_at"]
    )
    op.create_index(
        "ix_generation_fuel_observed",
        "generation_observations",
        ["fuel_type", "observed_at"],
    )

    op.create_table(
        "demand_observations",
        *_observation_columns(),
        sa.Column("series_key", sa.String(120), nullable=False),
        sa.Column("demand_type", sa.String(64), nullable=False),
        sa.Column("demand_mw", sa.Float(), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=True),
        sa.Column("settlement_period", sa.SmallInteger(), nullable=True),
        sa.CheckConstraint(
            "settlement_period IS NULL OR settlement_period BETWEEN 1 AND 50",
            name="valid_settlement_period",
        ),
        sa.UniqueConstraint(
            "source_id",
            "series_key",
            "demand_type",
            "observed_at",
            "revision",
            name="uq_demand_source_series_type_time_revision",
        ),
    )
    op.create_index("ix_demand_observed_at", "demand_observations", ["observed_at"])

    op.create_table(
        "frequency_observations",
        *_observation_columns(),
        sa.Column("series_key", sa.String(120), nullable=False),
        sa.Column("frequency_hz", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "source_id",
            "series_key",
            "observed_at",
            "revision",
            name="uq_frequency_source_series_time_revision",
        ),
    )
    op.create_index(
        "ix_frequency_observed_at", "frequency_observations", ["observed_at"]
    )

    op.create_table(
        "interconnector_observations",
        *_observation_columns(),
        sa.Column("connector_code", sa.String(120), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("counterparty", sa.String(120), nullable=False),
        sa.Column(
            "flow_mw",
            sa.Float(),
            nullable=False,
            comment="Signed MW: positive imports into Britain; negative exports.",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "source_id",
            "connector_code",
            "observed_at",
            "revision",
            name="uq_interconnector_source_connector_time_revision",
        ),
    )
    op.create_index(
        "ix_interconnector_observed_at",
        "interconnector_observations",
        ["observed_at"],
    )

    op.create_table(
        "carbon_observations",
        *_observation_columns(),
        sa.Column("region_code", sa.String(64), nullable=False),
        sa.Column("intensity_gco2_kwh", sa.Float(), nullable=False),
        sa.Column("index_label", sa.String(32), nullable=True),
        sa.Column("generation_mix", _json(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.CheckConstraint(
            "intensity_gco2_kwh >= 0", name="nonnegative_intensity"
        ),
        sa.UniqueConstraint(
            "source_id",
            "region_code",
            "observed_at",
            "revision",
            name="uq_carbon_source_region_time_revision",
        ),
    )
    op.create_index(
        "ix_carbon_region_observed",
        "carbon_observations",
        ["region_code", "observed_at"],
    )

    op.create_table(
        "forecast_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        sa.Column("source_record_id", sa.String(200), nullable=True),
        sa.Column("metric_type", sa.String(80), nullable=False),
        sa.Column("series_key", sa.String(120), nullable=False),
        sa.Column("variant", sa.String(64), server_default="point", nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("value_low", sa.Float(), nullable=True),
        sa.Column("value_high", sa.Float(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("settlement_date", sa.Date(), nullable=True),
        sa.Column("settlement_period", sa.SmallInteger(), nullable=True),
        sa.Column("attributes", _json(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="valid_forecast_window",
        ),
        sa.CheckConstraint(
            "value_low IS NULL OR value_high IS NULL OR value_low <= value_high",
            name="ordered_forecast_interval",
        ),
        sa.CheckConstraint(
            "settlement_period IS NULL OR settlement_period BETWEEN 1 AND 50",
            name="valid_settlement_period",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"], ["raw_payloads.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "metric_type",
            "series_key",
            "variant",
            "valid_from",
            "issued_at",
            name="uq_forecast_source_metric_series_variant_valid_issue",
        ),
    )
    op.create_index(
        "ix_forecast_metric_valid", "forecast_observations", ["metric_type", "valid_from"]
    )
    op.create_index(
        "ix_forecast_series_issue", "forecast_observations", ["series_key", "issued_at"]
    )

    op.create_table(
        "grid_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(16), server_default="1.0", nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "freshness",
            _enum("freshness_state", "fresh", "delayed", "stale", "unavailable"),
            nullable=False,
        ),
        sa.Column("generation_total_mw", sa.Float(), nullable=True),
        sa.Column("demand_mw", sa.Float(), nullable=True),
        sa.Column("frequency_hz", sa.Float(), nullable=True),
        sa.Column("carbon_intensity_gco2_kwh", sa.Float(), nullable=True),
        sa.Column("net_import_mw", sa.Float(), nullable=True),
        sa.Column("completeness", sa.Float(), nullable=False),
        sa.Column("generation_mix", _json(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("interconnectors", _json(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("provenance", _json(), nullable=False),
        sa.Column("missing_datasets", _json(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.CheckConstraint(
            "completeness >= 0 AND completeness <= 1",
            name="bounded_completeness",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_at", name="uq_grid_snapshots_snapshot_at"),
    )
    op.create_index(
        "ix_grid_snapshots_generated_at", "grid_snapshots", ["generated_at"]
    )

    op.create_table(
        "detected_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deterministic_key", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "status",
            _enum(
                "event_status", "open", "updated", "resolved", "superseded", "withdrawn"
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            _enum("event_severity", "info", "notable", "material", "critical"),
            nullable=False,
        ),
        sa.Column(
            "confidence",
            _enum(
                "evidence_confidence", "low", "medium", "high", "authoritative"
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("deterministic_summary", sa.Text(), nullable=True),
        sa.Column("rule_version", sa.String(80), nullable=False),
        sa.Column("evidence_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("evidence_checksum", sa.String(64), nullable=False),
        sa.Column("evidence", _json(), nullable=False),
        sa.Column("source_ids", _json(), nullable=False),
        sa.Column("related_asset_ids", _json(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("event_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "evidence_version > 0", name="positive_evidence_version"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "deterministic_key", name="uq_detected_events_deterministic_key"
        ),
    )
    op.create_index(
        "ix_detected_events_status_severity", "detected_events", ["status", "severity"]
    )
    op.create_index(
        "ix_detected_events_type_started",
        "detected_events",
        ["event_type", "event_started_at"],
    )

    op.create_table(
        "event_explanations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_checksum", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("locale", sa.String(20), server_default="en-GB", nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("structured_response", _json(), nullable=True),
        sa.Column("error", _json(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="nonnegative_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="nonnegative_output_tokens",
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="nonnegative_cost",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["detected_events.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "evidence_checksum",
            "provider",
            "model",
            "prompt_version",
            "locale",
            name="uq_event_explanation_cache_key",
        ),
    )
    op.create_index(
        "ix_event_explanations_event_generated",
        "event_explanations",
        ["event_id", "generated_at"],
    )


def downgrade() -> None:
    op.drop_table("event_explanations")
    op.drop_table("detected_events")
    op.drop_table("grid_snapshots")
    op.drop_table("forecast_observations")
    op.drop_table("carbon_observations")
    op.drop_table("interconnector_observations")
    op.drop_table("frequency_observations")
    op.drop_table("demand_observations")
    op.drop_table("generation_observations")
    op.drop_table("assets")
    op.drop_table("raw_payloads")
    op.drop_table("ingestion_runs")
    op.drop_table("source_metadata")
