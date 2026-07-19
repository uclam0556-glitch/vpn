"""Add referrer and balance_stars

Revision ID: d7a5b53339ec
Revises: 20260623_0002
Create Date: 2026-06-23 19:48:56.328401
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a5b53339ec"
down_revision: str | Sequence[str] | None = "20260623_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.add_column(sa.Column("referrer_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("balance_stars", sa.Integer(), server_default="0", nullable=False)
        )
        batch.create_foreign_key(
            "fk_customers_referrer_id_customers",
            "customers",
            ["referrer_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("customers") as batch:
        batch.drop_constraint("fk_customers_referrer_id_customers", type_="foreignkey")
        batch.drop_column("balance_stars")
        batch.drop_column("referrer_id")
