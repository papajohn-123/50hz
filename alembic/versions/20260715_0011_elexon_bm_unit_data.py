"""Add current PN segments and revisioned B1610 settled energy.

Revision ID: 20260715_0011
Revises: 20260715_0010
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260715_0011"
down_revision: str | None = "20260715_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "physical_notification_segments_current",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("national_grid_bm_unit", sa.String(120), nullable=False),
        sa.Column("elexon_bm_unit", sa.String(120), nullable=True),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("settlement_period", sa.SmallInteger(), nullable=False),
        sa.Column("segment_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("segment_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level_from_mw", sa.Float(), nullable=False),
        sa.Column("level_to_mw", sa.Float(), nullable=False),
        sa.Column(
            "classification",
            sa.String(32),
            server_default="reported_plan",
            nullable=False,
        ),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attributes",
            _json(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
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
            "settlement_period BETWEEN 1 AND 50",
            name="valid_pn_current_settlement_period",
        ),
        sa.CheckConstraint(
            "segment_end > segment_start",
            name="valid_pn_current_segment_window",
        ),
        sa.CheckConstraint(
            "classification = 'reported_plan'",
            name="pn_current_reported_plan_only",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"], ["raw_payloads.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "national_grid_bm_unit",
            "settlement_date",
            "settlement_period",
            "segment_start",
            "segment_end",
            name="uq_pn_current_segment",
        ),
    )
    op.create_index(
        "ix_pn_current_period_unit",
        "physical_notification_segments_current",
        ["settlement_date", "settlement_period", "national_grid_bm_unit"],
    )
    op.create_index(
        "ix_pn_current_asset_start",
        "physical_notification_segments_current",
        ["asset_id", "segment_start"],
    )

    op.create_table(
        "b1610_settled_energy_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("raw_payload_id", sa.Uuid(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("national_grid_bm_unit", sa.String(120), nullable=True),
        sa.Column("elexon_bm_unit", sa.String(120), nullable=True),
        sa.Column("settlement_date", sa.Date(), nullable=False),
        sa.Column("settlement_period", sa.SmallInteger(), nullable=False),
        sa.Column("interval_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("energy_mwh", sa.Float(), nullable=False),
        sa.Column("average_mw", sa.Float(), nullable=False),
        sa.Column("psr_type", sa.String(120), nullable=True),
        sa.Column(
            "classification",
            sa.String(32),
            server_default="settled_metered",
            nullable=False,
        ),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "attributes",
            _json(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision >= 0", name="nonnegative_b1610_energy_revision"
        ),
        sa.CheckConstraint(
            "settlement_period BETWEEN 1 AND 50",
            name="valid_b1610_settlement_period",
        ),
        sa.CheckConstraint(
            "interval_end > interval_start",
            name="valid_b1610_interval_window",
        ),
        sa.CheckConstraint(
            "classification = 'settled_metered'",
            name="b1610_settled_metered_only",
        ),
        sa.CheckConstraint(
            "national_grid_bm_unit IS NOT NULL OR elexon_bm_unit IS NOT NULL",
            name="b1610_has_official_unit_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source_metadata.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["raw_payload_id"], ["raw_payloads.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "asset_id",
            "settlement_date",
            "settlement_period",
            "revision",
            name="uq_b1610_energy_revision",
        ),
    )
    op.create_index(
        "ix_b1610_latest_unit_period",
        "b1610_settled_energy_revisions",
        [
            "national_grid_bm_unit",
            "elexon_bm_unit",
            "settlement_date",
            "settlement_period",
            "revision",
        ],
    )
    op.create_index(
        "ix_b1610_asset_interval",
        "b1610_settled_energy_revisions",
        ["asset_id", "interval_start"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_b1610_asset_interval",
        table_name="b1610_settled_energy_revisions",
    )
    op.drop_index(
        "ix_b1610_latest_unit_period",
        table_name="b1610_settled_energy_revisions",
    )
    op.drop_table("b1610_settled_energy_revisions")
    op.drop_index(
        "ix_pn_current_asset_start",
        table_name="physical_notification_segments_current",
    )
    op.drop_index(
        "ix_pn_current_period_unit",
        table_name="physical_notification_segments_current",
    )
    op.drop_table("physical_notification_segments_current")
