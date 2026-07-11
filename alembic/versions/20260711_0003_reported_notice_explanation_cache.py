"""Cache validated explanations for stable reported-notice revisions.

Revision ID: 20260711_0003
Revises: 20260711_0002
Create Date: 2026-07-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260711_0003"
down_revision: str | None = "20260711_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reported_notice_explanations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_event_id", sa.String(80), nullable=False),
        sa.Column("notice_revision_key", sa.String(128), nullable=False),
        sa.Column("notice_revision_number", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("locale", sa.String(20), server_default="en-GB", nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "structured_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "error",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "notice_revision_number IS NULL OR notice_revision_number > 0",
            name="positive_notice_explanation_revision_number",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="nonnegative_notice_explanation_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="nonnegative_notice_explanation_output_tokens",
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="nonnegative_notice_explanation_cost",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "public_event_id",
            "notice_revision_key",
            "provider",
            "model",
            "prompt_version",
            "locale",
            name="uq_reported_notice_explanation_cache_key",
        ),
    )
    op.create_index(
        "ix_reported_notice_explanations_event_generated",
        "reported_notice_explanations",
        ["public_event_id", "generated_at"],
    )


def downgrade() -> None:
    op.drop_table("reported_notice_explanations")
