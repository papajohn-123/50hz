"""Add revision-safe history materialization and read-path indexes.

Revision ID: 20260712_0008
Revises: 20260712_0007
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_0008"
down_revision: str | None = "20260712_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DERIVED_TABLES = (
    (
        "observation_coverage_daily",
        "uq_daily_coverage_series_date_method",
        [
            "metric_id",
            "series_key",
            "geography",
            "settlement_date",
            "methodology_version",
        ],
        "nonnegative_daily_coverage_revision",
        "valid_daily_coverage_content_sha256",
    ),
    (
        "metric_aggregates",
        "uq_metric_aggregate_series_period_method",
        [
            "metric_id",
            "series_key",
            "geography",
            "aggregate_kind",
            "period_start",
            "period_end",
            "methodology_version",
        ],
        "nonnegative_metric_aggregate_revision",
        "valid_metric_aggregate_content_sha256",
    ),
    (
        "comparison_baselines",
        "uq_comparison_baseline_reference_method",
        [
            "metric_id",
            "series_key",
            "geography",
            "baseline_kind",
            "reference_start",
            "methodology_version",
        ],
        "nonnegative_comparison_baseline_revision",
        "valid_comparison_baseline_content_sha256",
    ),
)


def upgrade() -> None:
    # These match the bounded latest-run and checkpoint lookups used by source
    # status and the worker, without storing endpoint or request data in an index.
    op.create_index(
        "ix_ingestion_runs_adapter_started",
        "ingestion_runs",
        ["adapter", "started_at"],
    )
    op.create_index(
        "ix_ingestion_runs_source_started",
        "ingestion_runs",
        ["source_id", "started_at"],
    )
    op.create_index(
        "ix_ingestion_runs_source_status_completed",
        "ingestion_runs",
        ["source_id", "status", "completed_at"],
    )
    op.create_index(
        "ix_reported_notices_kind_published",
        "reported_notices",
        ["notice_kind", "published_at"],
    )
    op.create_index(
        "ix_reported_notices_identity_revision",
        "reported_notices",
        [
            "source_id",
            "notice_kind",
            "external_id",
            "revision_number",
            "published_at",
        ],
    )
    op.create_index(
        "ix_reported_notices_external_history",
        "reported_notices",
        ["external_id", "published_at", "retrieved_at"],
    )

    # ``id`` remains the compact FK/row key. The stable public metric ID is a
    # separate identity component so more than one methodology version can be
    # retained without rewriting derived rows.
    op.add_column(
        "metric_definitions",
        sa.Column("stable_metric_id", sa.String(120), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE metric_definitions SET stable_metric_id = id "
            "WHERE stable_metric_id IS NULL"
        )
    )
    op.alter_column(
        "metric_definitions",
        "stable_metric_id",
        existing_type=sa.String(120),
        nullable=False,
    )
    op.drop_index("ix_metric_definitions_active", table_name="metric_definitions")
    op.drop_constraint(
        "uq_metric_definition_identity",
        "metric_definitions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_metric_definition_identity",
        "metric_definitions",
        ["stable_metric_id", "identity_version", "methodology_version"],
    )
    op.create_index(
        "ix_metric_definitions_active",
        "metric_definitions",
        ["active", "stable_metric_id"],
    )

    # Existing derived rows become revision zero. New corrections append a new
    # revision; no prior aggregate, coverage decision or baseline is overwritten.
    for table, unique_name, identity_columns, revision_check, digest_check in _DERIVED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "revision",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "content_sha256",
                sa.String(64),
                server_default=sa.text("'" + ("0" * 64) + "'"),
                nullable=False,
            ),
        )
        op.alter_column(
            table,
            "source_watermark_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        op.drop_constraint(unique_name, table, type_="unique")
        op.create_unique_constraint(
            unique_name,
            table,
            [*identity_columns, "revision"],
        )
        op.create_check_constraint(revision_check, table, "revision >= 0")
        op.create_check_constraint(
            digest_check,
            table,
            "length(content_sha256) = 64",
        )
        op.alter_column(
            table,
            "content_sha256",
            existing_type=sa.String(64),
            server_default=None,
        )

    op.add_column(
        "comparison_baselines",
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    op.create_table(
        "history_materialization_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_key", sa.String(200), nullable=False),
        sa.Column("registry_version", sa.String(80), nullable=False),
        sa.Column("metric_definition_id", sa.String(120), nullable=True),
        sa.Column("stable_metric_id", sa.String(120), nullable=False),
        sa.Column("series_key", sa.String(160), nullable=False),
        sa.Column("geography", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("output_start_date", sa.Date(), nullable=False),
        sa.Column("output_end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "records_written", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("result_checksum", sa.String(64), nullable=True),
        sa.Column("source_watermark_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_type", sa.String(120), nullable=True),
        sa.CheckConstraint(
            "output_end_date > output_start_date",
            name="valid_history_materialization_range",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="valid_history_materialization_status",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL "
            "AND result_checksum IS NULL) OR "
            "(status = 'succeeded' AND completed_at IS NOT NULL "
            "AND result_checksum IS NOT NULL AND metric_definition_id IS NOT NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL "
            "AND result_checksum IS NULL)",
            name="history_materialization_state_is_complete",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND error_type IS NOT NULL) OR "
            "(status <> 'failed' AND error_type IS NULL)",
            name="history_materialization_error_matches_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="nonnegative_history_materialization_attempts",
        ),
        sa.CheckConstraint(
            "records_written >= 0",
            name="nonnegative_history_materialization_writes",
        ),
        sa.CheckConstraint(
            "result_checksum IS NULL OR length(result_checksum) = 64",
            name="valid_history_materialization_result_checksum",
        ),
        sa.ForeignKeyConstraint(
            ["metric_definition_id"],
            ["metric_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_key", name="uq_history_materialization_job_key"),
    )
    op.create_index(
        "ix_history_materialization_status_started",
        "history_materialization_runs",
        ["status", "started_at"],
    )
    op.create_index(
        "ix_history_materialization_metric_range",
        "history_materialization_runs",
        [
            "stable_metric_id",
            "series_key",
            "output_start_date",
            "output_end_date",
        ],
    )


def downgrade() -> None:
    op.drop_table("history_materialization_runs")
    op.drop_column("comparison_baselines", "attributes")

    for table, unique_name, identity_columns, revision_check, digest_check in reversed(
        _DERIVED_TABLES
    ):
        # The old schema can represent only one result per identity. Retain the
        # first materialized revision deterministically rather than overwriting it.
        op.execute(sa.text(f"DELETE FROM {table} WHERE revision > 0"))
        op.execute(
            sa.text(f"DELETE FROM {table} WHERE source_watermark_at IS NULL")
        )
        op.drop_constraint(digest_check, table, type_="check")
        op.drop_constraint(revision_check, table, type_="check")
        op.drop_constraint(unique_name, table, type_="unique")
        op.create_unique_constraint(unique_name, table, identity_columns)
        op.alter_column(
            table,
            "source_watermark_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        op.drop_column(table, "content_sha256")
        op.drop_column(table, "revision")

    op.drop_index("ix_metric_definitions_active", table_name="metric_definitions")
    op.drop_constraint(
        "uq_metric_definition_identity", "metric_definitions", type_="unique"
    )
    op.create_unique_constraint(
        "uq_metric_definition_identity",
        "metric_definitions",
        ["id", "identity_version", "methodology_version"],
    )
    op.create_index(
        "ix_metric_definitions_active",
        "metric_definitions",
        ["active", "id"],
    )
    op.drop_column("metric_definitions", "stable_metric_id")

    op.drop_index(
        "ix_reported_notices_external_history", table_name="reported_notices"
    )
    op.drop_index(
        "ix_reported_notices_identity_revision", table_name="reported_notices"
    )
    op.drop_index("ix_reported_notices_kind_published", table_name="reported_notices")
    op.drop_index(
        "ix_ingestion_runs_source_status_completed", table_name="ingestion_runs"
    )
    op.drop_index("ix_ingestion_runs_source_started", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_adapter_started", table_name="ingestion_runs")
