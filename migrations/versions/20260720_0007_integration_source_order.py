"""Preserve provider ordering for integrated VPN profiles.

Revision ID: 20260720_0007
Revises: 20260720_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0007"
down_revision: str | Sequence[str] | None = "20260720_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integration_nodes",
        sa.Column("source_position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY link_id ORDER BY id) - 1 AS position
            FROM integration_nodes
        )
        UPDATE integration_nodes AS node
        SET source_position = ranked.position
        FROM ranked
        WHERE node.id = ranked.id
        """
    )


def downgrade() -> None:
    op.drop_column("integration_nodes", "source_position")
