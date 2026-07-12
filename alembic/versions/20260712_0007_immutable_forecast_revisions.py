"""Preserve immutable revisions of corrected forecast observations.

Revision ID: 20260712_0007
Revises: 20260712_0006
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_0007"
down_revision: str | None = "20260712_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_UNIQUE = "uq_forecast_source_metric_series_variant_valid_issue"
_REVISION_UNIQUE = "uq_forecast_series_valid_issue_revision"
_IDENTITY_COLUMNS = [
    "source_id",
    "metric_type",
    "series_key",
    "variant",
    "valid_from",
    "issued_at",
]


def upgrade() -> None:
    # Existing observations are the first known version of their source vintage.
    op.add_column(
        "forecast_observations",
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.drop_constraint(_OLD_UNIQUE, "forecast_observations", type_="unique")
    op.create_unique_constraint(
        _REVISION_UNIQUE,
        "forecast_observations",
        [*_IDENTITY_COLUMNS, "revision"],
    )


def downgrade() -> None:
    # A downgrade cannot represent corrected vintages. Keep the earliest row for
    # each identity so the former unique constraint can be restored deterministically.
    op.execute(
        sa.text(
            """
            DELETE FROM forecast_observations AS corrected
            USING forecast_observations AS original
            WHERE corrected.source_id = original.source_id
              AND corrected.metric_type = original.metric_type
              AND corrected.series_key = original.series_key
              AND corrected.variant = original.variant
              AND corrected.valid_from = original.valid_from
              AND corrected.issued_at = original.issued_at
              AND (
                    corrected.revision > original.revision
                    OR (
                        corrected.revision = original.revision
                        AND corrected.id > original.id
                    )
              )
            """
        )
    )
    op.drop_constraint(_REVISION_UNIQUE, "forecast_observations", type_="unique")
    op.create_unique_constraint(
        _OLD_UNIQUE,
        "forecast_observations",
        _IDENTITY_COLUMNS,
    )
    op.drop_column("forecast_observations", "revision")
