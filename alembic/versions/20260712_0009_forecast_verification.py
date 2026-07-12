"""Add immutable forecast verification evidence and results.

Revision ID: 20260712_0009
Revises: 20260712_0008
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_0009"
down_revision: str | None = "20260712_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_forecast_verify_source_metric_valid",
        "forecast_observations",
        [
            "source_id",
            "metric_type",
            "series_key",
            "valid_from",
            "issued_at",
            "revision",
        ],
    )
    op.create_index(
        "ix_demand_verify_source_time",
        "demand_observations",
        ["source_id", "series_key", "demand_type", "observed_at", "revision"],
    )
    op.create_index(
        "ix_generation_verify_source_time",
        "generation_observations",
        ["source_id", "series_key", "observed_at", "revision"],
    )
    op.create_index(
        "ix_carbon_verify_source_time",
        "carbon_observations",
        ["source_id", "region_code", "observed_at", "revision"],
    )

    op.create_table(
        "forecast_verification_pairs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.String(120), nullable=False),
        sa.Column("horizon_bucket", sa.String(16), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_source_id", sa.String(64), nullable=False),
        sa.Column("outturn_source_id", sa.String(64), nullable=False),
        sa.Column("forecast_observation_id", sa.Uuid(), nullable=False),
        sa.Column("outturn_observation_id", sa.Uuid(), nullable=False),
        sa.Column("forecast_vintage_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "forecast_source_issued_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("forecast_captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issue_time_basis", sa.String(64), nullable=False),
        sa.Column("effective_vintage_time_basis", sa.String(32), nullable=False),
        sa.Column("forecast_value", sa.Float(), nullable=False),
        sa.Column("outturn_value", sa.Float(), nullable=False),
        sa.Column("signed_error", sa.Float(), nullable=False),
        sa.Column("absolute_error", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("forecast_revision", sa.Integer(), nullable=False),
        sa.Column("outturn_revision", sa.Integer(), nullable=False),
        sa.Column("forecast_methodology_version", sa.String(120), nullable=False),
        sa.Column("outturn_methodology_version", sa.String(120), nullable=False),
        sa.Column("verification_methodology_version", sa.String(120), nullable=False),
        sa.Column("registry_version", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "horizon_bucket IN ('0_3h', '3_12h', '12_24h', '24_48h')",
            name="valid_forecast_verification_horizon",
        ),
        sa.CheckConstraint(
            "issue_time_basis IN "
            "('source_published_at', 'source_does_not_publish_issue_time')",
            name="valid_forecast_issue_time_basis",
        ),
        sa.CheckConstraint(
            "effective_vintage_time_basis IN ('source_published_at', 'retrieved_at')",
            name="valid_forecast_effective_vintage_basis",
        ),
        sa.CheckConstraint(
            "forecast_vintage_at <= valid_from",
            name="forecast_vintage_precedes_valid_time",
        ),
        sa.CheckConstraint(
            "(issue_time_basis = 'source_published_at' "
            "AND effective_vintage_time_basis = 'source_published_at' "
            "AND forecast_source_issued_at IS NOT NULL "
            "AND forecast_vintage_at = forecast_source_issued_at) OR "
            "(issue_time_basis = 'source_does_not_publish_issue_time' "
            "AND effective_vintage_time_basis = 'retrieved_at' "
            "AND forecast_source_issued_at IS NULL "
            "AND forecast_vintage_at = forecast_captured_at)",
            name="forecast_vintage_basis_matches_timestamps",
        ),
        sa.CheckConstraint(
            "absolute_error >= 0", name="nonnegative_forecast_absolute_error"
        ),
        sa.CheckConstraint(
            "forecast_revision >= 0 AND outturn_revision >= 0 AND revision >= 0",
            name="nonnegative_forecast_verification_revisions",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="valid_forecast_pair_content_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["forecast_source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["outturn_source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["forecast_observation_id"],
            ["forecast_observations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "metric_id",
            "horizon_bucket",
            "valid_from",
            "forecast_vintage_at",
            "verification_methodology_version",
            "revision",
            name="uq_forecast_pair_identity_revision",
        ),
    )
    op.create_index(
        "ix_forecast_pairs_metric_window",
        "forecast_verification_pairs",
        ["metric_id", "horizon_bucket", "valid_from"],
    )

    op.create_table(
        "forecast_verification_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.String(120), nullable=False),
        sa.Column("horizon_bucket", sa.String(16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("mae", sa.Float(), nullable=True),
        sa.Column("bias", sa.Float(), nullable=True),
        sa.Column("wape_percent", sa.Float(), nullable=True),
        sa.Column("verified_sample_count", sa.Integer(), nullable=False),
        sa.Column("expected_sample_count", sa.Integer(), nullable=False),
        sa.Column("coverage_fraction", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("forecast_source_id", sa.String(64), nullable=False),
        sa.Column("outturn_source_id", sa.String(64), nullable=False),
        sa.Column("issue_time_basis", sa.String(64), nullable=False),
        sa.Column("effective_vintage_time_basis", sa.String(32), nullable=False),
        sa.Column("forecast_methodology_version", sa.String(120), nullable=False),
        sa.Column("outturn_methodology_version", sa.String(120), nullable=False),
        sa.Column("verification_methodology_version", sa.String(120), nullable=False),
        sa.Column("registry_version", sa.String(80), nullable=False),
        sa.Column("evidence_checksum", sa.String(64), nullable=False),
        sa.Column("source_watermark_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("window_end > window_start", name="valid_forecast_verify_window"),
        sa.CheckConstraint(
            "horizon_bucket IN ('0_3h', '3_12h', '12_24h', '24_48h')",
            name="valid_forecast_result_horizon",
        ),
        sa.CheckConstraint(
            "status IN ('available', 'insufficient_data')",
            name="valid_forecast_verification_status",
        ),
        sa.CheckConstraint(
            "verified_sample_count >= 0 AND expected_sample_count >= 0 "
            "AND verified_sample_count <= expected_sample_count",
            name="valid_forecast_verification_samples",
        ),
        sa.CheckConstraint(
            "coverage_fraction >= 0 AND coverage_fraction <= 1",
            name="bounded_forecast_verification_coverage",
        ),
        sa.CheckConstraint(
            "mae IS NULL OR mae >= 0",
            name="nonnegative_forecast_verification_mae",
        ),
        sa.CheckConstraint(
            "wape_percent IS NULL OR wape_percent >= 0",
            name="nonnegative_forecast_verification_wape",
        ),
        sa.CheckConstraint(
            "(verified_sample_count = 0 AND mae IS NULL AND bias IS NULL "
            "AND wape_percent IS NULL) OR "
            "(verified_sample_count > 0 AND mae IS NOT NULL AND bias IS NOT NULL)",
            name="forecast_metrics_match_samples",
        ),
        sa.CheckConstraint(
            "status <> 'available' OR "
            "(verified_sample_count >= 100 AND coverage_fraction >= 0.9)",
            name="forecast_display_threshold",
        ),
        sa.CheckConstraint(
            "issue_time_basis IN "
            "('source_published_at', 'source_does_not_publish_issue_time')",
            name="valid_result_issue_time_basis",
        ),
        sa.CheckConstraint(
            "(issue_time_basis = 'source_published_at' "
            "AND effective_vintage_time_basis = 'source_published_at') OR "
            "(issue_time_basis = 'source_does_not_publish_issue_time' "
            "AND effective_vintage_time_basis = 'retrieved_at')",
            name="valid_result_effective_vintage_basis",
        ),
        sa.CheckConstraint("revision >= 0", name="nonnegative_forecast_result_revision"),
        sa.CheckConstraint(
            "length(evidence_checksum) = 64",
            name="valid_forecast_result_evidence_checksum",
        ),
        sa.ForeignKeyConstraint(
            ["forecast_source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["outturn_source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "metric_id",
            "horizon_bucket",
            "window_start",
            "window_end",
            "verification_methodology_version",
            "revision",
            name="uq_forecast_result_identity_revision",
        ),
    )
    op.create_index(
        "ix_forecast_results_public_latest",
        "forecast_verification_results",
        ["metric_id", "horizon_bucket", "window_end", "revision"],
    )

    op.create_table(
        "forecast_verification_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_key", sa.String(200), nullable=False),
        sa.Column("metric_id", sa.String(120), nullable=False),
        sa.Column("registry_version", sa.String(80), nullable=False),
        sa.Column("window_start_date", sa.Date(), nullable=False),
        sa.Column("window_end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pairs_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("results_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_checksum", sa.String(64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_type", sa.String(120), nullable=True),
        sa.CheckConstraint(
            "window_end_date > window_start_date",
            name="valid_forecast_verification_run_range",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="valid_forecast_verification_run_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND pairs_written >= 0 AND results_written >= 0",
            name="nonnegative_forecast_verification_run_counts",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL "
            "AND result_checksum IS NULL AND error_type IS NULL) OR "
            "(status = 'succeeded' AND completed_at IS NOT NULL "
            "AND result_checksum IS NOT NULL AND error_type IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL "
            "AND result_checksum IS NULL AND error_type IS NOT NULL)",
            name="forecast_verification_run_state_complete",
        ),
        sa.CheckConstraint(
            "result_checksum IS NULL OR length(result_checksum) = 64",
            name="valid_forecast_verification_run_checksum",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_key", name="uq_forecast_verification_job_key"),
    )
    op.create_index(
        "ix_forecast_verify_runs_status",
        "forecast_verification_runs",
        ["status", "started_at"],
    )


def downgrade() -> None:
    op.drop_table("forecast_verification_runs")
    op.drop_table("forecast_verification_results")
    op.drop_table("forecast_verification_pairs")
    op.drop_index("ix_carbon_verify_source_time", table_name="carbon_observations")
    op.drop_index("ix_generation_verify_source_time", table_name="generation_observations")
    op.drop_index("ix_demand_verify_source_time", table_name="demand_observations")
    op.drop_index(
        "ix_forecast_verify_source_metric_valid", table_name="forecast_observations"
    )
