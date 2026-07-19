"""Track externally integrated subscription nodes.

Revision ID: 20260719_0005
Revises: 20260701_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0005"
down_revision: str | Sequence[str] | None = "20260701_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index_names(inspector: sa.Inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "integration_links" not in tables:
        op.create_table(
            "integration_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("hwid", sa.String(length=64), nullable=False),
            sa.Column("user_agent", sa.String(length=255), nullable=False),
            sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        tables.add("integration_links")
    else:
        op.alter_column(
            "integration_links",
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )

    if "integration_nodes" not in tables:
        op.create_table(
            "integration_nodes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "link_id",
                sa.Integer(),
                sa.ForeignKey("integration_links.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("raw_link", sa.Text(), nullable=False),
            sa.Column("original_name", sa.String(length=255), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_integration_nodes_link_id", "integration_nodes", ["link_id"])
    else:
        op.alter_column(
            "integration_nodes",
            "is_active",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )
        op.alter_column(
            "integration_nodes",
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )
        inspector = sa.inspect(bind)
        if "ix_integration_nodes_link_id" not in _index_names(inspector, "integration_nodes"):
            op.create_index("ix_integration_nodes_link_id", "integration_nodes", ["link_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "integration_nodes" in tables:
        op.drop_table("integration_nodes")
    if "integration_links" in tables:
        op.drop_table("integration_links")
