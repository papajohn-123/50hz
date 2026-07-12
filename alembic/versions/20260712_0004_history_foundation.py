"""Add coverage-aware history and comparison storage.

Revision ID: 20260712_0004
Revises: 20260711_0003
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_0004"
down_revision: str | None = "20260711_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "metric_definitions",
        sa.Column("id", sa.String(120), nullable=False),
        sa.Column("identity_version", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("geography_scope", sa.String(80), nullable=False),
        sa.Column("fact_class", sa.String(32), nullable=False),
        sa.Column("methodology_version", sa.String(120), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column(
            "inclusions", _json(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "exclusions", _json(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("expected_interval_minutes", sa.SmallInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "attributes", _json(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expected_interval_minutes IS NULL OR expected_interval_minutes > 0",
            name="positive_metric_interval",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "identity_version",
            "methodology_version",
            name="uq_metric_definition_identity",
        ),
    )
    op.create_index(
        "ix_metric_definitions_active", "metric_definitions", ["active", "id"]
    )

    op.create_table(
        "observation_coverage_daily",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.String(120), nullable=False),
        sa.Column("series_key", sa.String(160), nullable=False),
        sa.Column("geography", sa.String(80), nullable=False),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("expected_interval_count", sa.SmallInteger(), nullable=False),
        sa.Column("observed_interval_count", sa.SmallInteger(), nullable=False),
        sa.Column(
            "duplicate_interval_count", sa.SmallInteger(), server_default="0", nullable=False
        ),
        sa.Column("source_record_count", sa.Integer(), nullable=False),
        sa.Column("coverage_fraction", sa.Float(), nullable=False),
        sa.Column("is_sufficient", sa.Boolean(), nullable=False),
        sa.Column(
            "missing_starts", _json(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("methodology_version", sa.String(120), nullable=False),
        sa.Column("source_watermark_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expected_interval_count BETWEEN 46 AND 50",
            name="valid_daily_expected_intervals",
        ),
        sa.CheckConstraint(
            "observed_interval_count BETWEEN 0 AND expected_interval_count",
            name="valid_daily_observed_intervals",
        ),
        sa.CheckConstraint(
            "duplicate_interval_count >= 0",
            name="nonnegative_daily_duplicate_intervals",
        ),
        sa.CheckConstraint(
            "source_record_count >= observed_interval_count",
            name="valid_daily_source_record_count",
        ),
        sa.CheckConstraint(
            "coverage_fraction >= 0 AND coverage_fraction <= 1",
            name="bounded_daily_coverage",
        ),
        sa.ForeignKeyConstraint(
            ["metric_id"], ["metric_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "metric_id",
            "series_key",
            "geography",
            "settlement_date",
            "methodology_version",
            name="uq_daily_coverage_series_date_method",
        ),
    )
    op.create_index(
        "ix_daily_coverage_metric_date",
        "observation_coverage_daily",
        ["metric_id", "settlement_date"],
    )

    op.create_table(
        "metric_aggregates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.String(120), nullable=False),
        sa.Column("series_key", sa.String(160), nullable=False),
        sa.Column("geography", sa.String(80), nullable=False),
        sa.Column("aggregate_kind", sa.String(64), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("expected_sample_count", sa.Integer(), nullable=False),
        sa.Column("coverage_fraction", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("methodology_version", sa.String(120), nullable=False),
        sa.Column("source_watermark_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attributes", _json(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("period_end > period_start", name="valid_aggregate_period"),
        sa.CheckConstraint("sample_count >= 0", name="nonnegative_aggregate_samples"),
        sa.CheckConstraint(
            "expected_sample_count > 0 AND sample_count <= expected_sample_count",
            name="valid_aggregate_expected_samples",
        ),
        sa.CheckConstraint(
            "coverage_fraction >= 0 AND coverage_fraction <= 1",
            name="bounded_aggregate_coverage",
        ),
        sa.CheckConstraint(
            "(status = 'available' AND value IS NOT NULL) OR "
            "(status <> 'available' AND value IS NULL)",
            name="aggregate_value_matches_status",
        ),
        sa.ForeignKeyConstraint(
            ["metric_id"], ["metric_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "metric_id",
            "series_key",
            "geography",
            "aggregate_kind",
            "period_start",
            "period_end",
            "methodology_version",
            name="uq_metric_aggregate_series_period_method",
        ),
    )
    op.create_index(
        "ix_metric_aggregates_metric_period",
        "metric_aggregates",
        ["metric_id", "period_start", "period_end"],
    )

    op.create_table(
        "comparison_baselines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("metric_id", sa.String(120), nullable=False),
        sa.Column("series_key", sa.String(160), nullable=False),
        sa.Column("geography", sa.String(80), nullable=False),
        sa.Column("baseline_kind", sa.String(80), nullable=False),
        sa.Column("reference_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("median", sa.Float(), nullable=True),
        sa.Column("first_quartile", sa.Float(), nullable=True),
        sa.Column("third_quartile", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("expected_sample_count", sa.Integer(), nullable=False),
        sa.Column("coverage_fraction", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("methodology_version", sa.String(120), nullable=False),
        sa.Column("source_watermark_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("window_end > window_start", name="valid_baseline_window"),
        sa.CheckConstraint("sample_count >= 0", name="nonnegative_baseline_samples"),
        sa.CheckConstraint(
            "expected_sample_count > 0 AND sample_count <= expected_sample_count",
            name="valid_baseline_expected_samples",
        ),
        sa.CheckConstraint(
            "coverage_fraction >= 0 AND coverage_fraction <= 1",
            name="bounded_baseline_coverage",
        ),
        sa.CheckConstraint(
            "(status = 'available' AND median IS NOT NULL "
            "AND first_quartile IS NOT NULL AND third_quartile IS NOT NULL) OR "
            "(status <> 'available' AND median IS NULL "
            "AND first_quartile IS NULL AND third_quartile IS NULL)",
            name="baseline_values_match_status",
        ),
        sa.CheckConstraint(
            "first_quartile IS NULL OR third_quartile IS NULL "
            "OR first_quartile <= median AND median <= third_quartile",
            name="ordered_baseline_quartiles",
        ),
        sa.ForeignKeyConstraint(
            ["metric_id"], ["metric_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "metric_id",
            "series_key",
            "geography",
            "baseline_kind",
            "reference_start",
            "methodology_version",
            name="uq_comparison_baseline_reference_method",
        ),
    )
    op.create_index(
        "ix_comparison_baselines_metric_reference",
        "comparison_baselines",
        ["metric_id", "reference_start"],
    )


def downgrade() -> None:
    op.drop_table("comparison_baselines")
    op.drop_table("metric_aggregates")
    op.drop_table("observation_coverage_daily")
    op.drop_table("metric_definitions")
