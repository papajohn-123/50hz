"""Preserve reported REMIT and SYSWARN publications by revision.

Revision ID: 20260711_0002
Revises: 20260711_0001
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260711_0002"
down_revision: str | None = "20260711_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reported_notices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        sa.Column("notice_kind", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(240), nullable=False),
        sa.Column("revision_key", sa.String(128), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=True),
        sa.Column("source_record_id", sa.String(300), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "classification", sa.String(16), server_default="reported", nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heading", sa.String(500), nullable=True),
        sa.Column("event_type", sa.String(160), nullable=True),
        sa.Column("unavailability_type", sa.String(160), nullable=True),
        sa.Column("event_status", sa.String(120), nullable=True),
        sa.Column("participant_id", sa.String(160), nullable=True),
        sa.Column("asset_id", sa.String(160), nullable=True),
        sa.Column("asset_type", sa.String(120), nullable=True),
        sa.Column("affected_unit", sa.String(240), nullable=True),
        sa.Column("affected_unit_eic", sa.String(160), nullable=True),
        sa.Column("affected_area", sa.String(240), nullable=True),
        sa.Column("bidding_zone", sa.String(120), nullable=True),
        sa.Column("fuel_type", sa.String(80), nullable=True),
        sa.Column("normal_capacity_mw", sa.Float(), nullable=True),
        sa.Column("available_capacity_mw", sa.Float(), nullable=True),
        sa.Column("unavailable_capacity_mw", sa.Float(), nullable=True),
        sa.Column("duration_uncertainty", sa.String(240), nullable=True),
        sa.Column("reported_cause", sa.Text(), nullable=True),
        sa.Column("reported_related_information", sa.Text(), nullable=True),
        sa.Column("warning_type", sa.String(160), nullable=True),
        sa.Column("warning_text", sa.Text(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number IS NULL OR revision_number > 0",
            name="positive_revision_number",
        ),
        sa.CheckConstraint(
            "event_end IS NULL OR event_start IS NULL OR event_end >= event_start",
            name="valid_reported_event_window",
        ),
        sa.CheckConstraint(
            "classification = 'reported'", name="reported_classification_only"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"], ["raw_payloads.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "notice_kind",
            "external_id",
            "revision_key",
            name="uq_reported_notice_revision",
        ),
    )
    op.create_index(
        "ix_reported_notices_external_published",
        "reported_notices",
        ["notice_kind", "external_id", "published_at"],
    )
    op.create_index(
        "ix_reported_notices_active_window",
        "reported_notices",
        ["notice_kind", "event_start", "event_end"],
    )
    op.create_index(
        "ix_reported_notices_source_retrieved",
        "reported_notices",
        ["source_id", "retrieved_at"],
    )


def downgrade() -> None:
    op.drop_table("reported_notices")

