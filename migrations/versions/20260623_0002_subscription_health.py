"""Add subscription health state.

Revision ID: 20260623_0002
Revises: 20260622_0001
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260623_0002"
down_revision: str | Sequence[str] | None = "20260622_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "health_status",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column("health_message", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "health_endpoint_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "health_reachable_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column("health_response_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "health_checked_at")
    op.drop_column("subscriptions", "health_response_ms")
    op.drop_column("subscriptions", "health_reachable_count")
    op.drop_column("subscriptions", "health_endpoint_count")
    op.drop_column("subscriptions", "health_message")
    op.drop_column("subscriptions", "health_status")
