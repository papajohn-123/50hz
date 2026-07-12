"""Add immutable daily-prediction resolution revisions.

Revision ID: 20260712_0006
Revises: 20260712_0005
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260712_0006"
down_revision: str | None = "20260712_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "prediction_resolution_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prediction_id", sa.String(200), nullable=False),
        sa.Column("prediction_date", sa.Date(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("resolution_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=True),
        sa.Column("evidence_checksum", sa.String(64), nullable=False),
        sa.Column(
            "revision_watermark_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rule_version > 0",
            name="positive_prediction_resolution_rule_version",
        ),
        sa.CheckConstraint(
            "resolution_revision > 0",
            name="positive_prediction_resolution_revision",
        ),
        sa.CheckConstraint(
            "state IN ('resolved', 'void')",
            name="terminal_prediction_resolution_state",
        ),
        sa.CheckConstraint(
            "(state = 'resolved' AND outcome IN ('importing', 'exporting')) OR "
            "(state = 'void' AND outcome IS NULL)",
            name="prediction_resolution_outcome_matches_state",
        ),
        sa.CheckConstraint(
            "char_length(evidence_checksum) = 64",
            name="prediction_resolution_sha256_length",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prediction_id",
            "rule_version",
            "resolution_revision",
            name="uq_prediction_resolution_revision",
        ),
        sa.UniqueConstraint(
            "prediction_id",
            "rule_version",
            "evidence_checksum",
            name="uq_prediction_resolution_evidence",
        ),
    )
    op.create_index(
        "ix_prediction_resolution_date",
        "prediction_resolution_revisions",
        ["prediction_date", "rule_version"],
    )


def downgrade() -> None:
    op.drop_table("prediction_resolution_revisions")
