"""add prediction family result fields

Revision ID: 0004_pred_family_results
Revises: 0003_add_user_auth_fields
Create Date: 2026-03-22 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004_pred_family_results"
down_revision = "0003_add_user_auth_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prediction_runs", sa.Column("requested_model_families", sa.JSON(), nullable=True))
    op.add_column("prediction_runs", sa.Column("risk_threshold", sa.Float(), nullable=True))
    op.add_column("prediction_runs", sa.Column("family_results", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("prediction_runs", "family_results")
    op.drop_column("prediction_runs", "risk_threshold")
    op.drop_column("prediction_runs", "requested_model_families")
