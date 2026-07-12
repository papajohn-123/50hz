"""Add an immutable event lifecycle revision and delta ledger.

Revision ID: 20260712_0005
Revises: 20260712_0004
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_0005"
down_revision: str | None = "20260712_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "event_lifecycle_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "updated",
                "resolved",
                "superseded",
                "withdrawn",
                name="event_lifecycle_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("authority", sa.String(40), nullable=False),
        sa.Column("evidence_class", sa.String(32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asset_id", sa.String(200), nullable=True),
        sa.Column("asset_name", sa.String(240), nullable=True),
        sa.Column(
            "asset_identity_reliable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("unavailable_mw", sa.Float(), nullable=True),
        sa.Column("normal_capacity_mw", sa.Float(), nullable=True),
        sa.Column("planned", sa.Boolean(), nullable=True),
        sa.Column("reported_cause", sa.Text(), nullable=True),
        sa.Column("evidence_checksum", sa.String(64), nullable=False),
        sa.Column("material_reason", sa.Text(), nullable=True),
        sa.Column("superseded_by_event_id", sa.String(200), nullable=True),
        sa.Column("source_ids", _json(), nullable=False),
        sa.Column("source_record_ids", _json(), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_kind IN ('reported', 'detected')",
            name="valid_event_lifecycle_kind",
        ),
        sa.CheckConstraint(
            "authority IN ('system_warning', 'authoritative_notice', 'other_reported')",
            name="valid_event_lifecycle_authority",
        ),
        sa.CheckConstraint(
            "evidence_class IN ('reported', 'observed', 'derived')",
            name="valid_event_lifecycle_evidence_class",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="positive_event_lifecycle_revision",
        ),
        sa.CheckConstraint(
            "effective_end IS NULL OR effective_start IS NULL "
            "OR effective_end >= effective_start",
            name="valid_event_lifecycle_window",
        ),
        sa.CheckConstraint(
            "unavailable_mw IS NULL OR unavailable_mw >= 0",
            name="nonnegative_event_lifecycle_unavailable_mw",
        ),
        sa.CheckConstraint(
            "normal_capacity_mw IS NULL OR normal_capacity_mw > 0",
            name="positive_event_lifecycle_normal_capacity",
        ),
        sa.CheckConstraint(
            "NOT asset_identity_reliable OR asset_id IS NOT NULL",
            name="reliable_event_lifecycle_asset_has_id",
        ),
        sa.CheckConstraint(
            "(status = 'superseded' AND superseded_by_event_id IS NOT NULL) OR "
            "(status <> 'superseded' AND superseded_by_event_id IS NULL)",
            name="event_lifecycle_supersession_matches_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "revision_number",
            name="uq_event_lifecycle_revision",
        ),
    )
    op.create_index(
        "ix_event_lifecycle_status_window",
        "event_lifecycle_revisions",
        ["status", "effective_start", "effective_end"],
    )
    op.create_index(
        "ix_event_lifecycle_event_published",
        "event_lifecycle_revisions",
        ["event_id", "published_at"],
    )

    op.create_table(
        "event_lifecycle_deltas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_revision_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=False),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(80), nullable=False),
        sa.Column("changes", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_revision > 0 AND to_revision = from_revision + 1",
            name="sequential_event_lifecycle_delta",
        ),
        sa.ForeignKeyConstraint(
            ["event_revision_id"],
            ["event_lifecycle_revisions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "from_revision",
            "to_revision",
            name="uq_event_lifecycle_delta",
        ),
    )
    op.create_index(
        "ix_event_lifecycle_deltas_event",
        "event_lifecycle_deltas",
        ["event_id", "to_revision"],
    )


def downgrade() -> None:
    op.drop_table("event_lifecycle_deltas")
    op.drop_table("event_lifecycle_revisions")
