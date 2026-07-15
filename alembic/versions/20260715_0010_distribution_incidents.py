"""Add privacy-reduced distribution incident revisions and current membership.

Revision ID: 20260715_0010
Revises: 20260712_0009
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260715_0010"
down_revision: str | None = "20260712_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "distribution_incident_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        sa.Column("incident_reference", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "classification",
            sa.String(16),
            server_default="reported",
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("status_id", sa.Integer(), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("incident_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "estimated_restoration_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "customers_affected", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("calls_reported", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "postcode_sectors",
            _json(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "outward_codes",
            _json(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("geography_precision", sa.String(64), nullable=False),
        sa.Column("operating_zone", sa.String(160), nullable=True),
        sa.Column("official_summary", sa.Text(), nullable=True),
        sa.Column("official_details", sa.Text(), nullable=True),
        sa.Column("restoration_window_text", sa.String(500), nullable=True),
        sa.Column("incident_category", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision >= 0", name="nonnegative_distribution_incident_revision"
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'unplanned', 'restored')",
            name="valid_distribution_incident_status",
        ),
        sa.CheckConstraint(
            "classification = 'reported'",
            name="distribution_incident_reported_only",
        ),
        sa.CheckConstraint(
            "customers_affected >= 0 AND calls_reported >= 0",
            name="nonnegative_distribution_incident_counts",
        ),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="valid_distribution_incident_geopoint",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="valid_distribution_incident_checksum",
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
            "incident_reference",
            "revision",
            name="uq_distribution_incident_revision",
        ),
    )
    op.create_index(
        "ix_distribution_incident_latest",
        "distribution_incident_revisions",
        ["source_id", "incident_reference", "revision"],
    )
    op.create_index(
        "ix_distribution_incident_status_observed",
        "distribution_incident_revisions",
        ["status", "observed_at"],
    )

    op.create_table(
        "distribution_incident_current",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("incident_reference", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("present", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'unplanned', 'restored')",
            name="valid_distribution_current_status",
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="valid_distribution_current_seen_window",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "incident_reference",
            name="uq_distribution_incident_current",
        ),
    )
    op.create_index(
        "ix_distribution_current_present_status",
        "distribution_incident_current",
        ["source_id", "present", "status", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_distribution_current_present_status",
        table_name="distribution_incident_current",
    )
    op.drop_table("distribution_incident_current")
    op.drop_index(
        "ix_distribution_incident_status_observed",
        table_name="distribution_incident_revisions",
    )
    op.drop_index(
        "ix_distribution_incident_latest",
        table_name="distribution_incident_revisions",
    )
    op.drop_table("distribution_incident_revisions")
