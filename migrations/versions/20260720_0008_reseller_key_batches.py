"""Add reseller key batches with independent one-device seats.

Revision ID: 20260720_0008
Revises: 20260720_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0008"
down_revision: str | Sequence[str] | None = "20260720_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reseller_key_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("reseller_id", sa.Integer(), nullable=False),
        sa.Column("tariff_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("total_seats", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price_rub", sa.Integer(), nullable=False),
        sa.Column("traffic_limit_gb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["reseller_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tariff_id"], ["tariffs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reseller_id", "request_id", name="uq_reseller_batch_request"),
    )
    op.create_index(
        "ix_reseller_key_batches_reseller_id", "reseller_key_batches", ["reseller_id"]
    )
    op.create_index(
        "ix_reseller_key_batches_expires_at", "reseller_key_batches", ["expires_at"]
    )
    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.add_column(
            sa.Column("reseller_batch_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reseller_seat_number", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reseller_assigned_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("reseller_client_telegram", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_subscriptions_reseller_batch_id",
            "reseller_key_batches",
            ["reseller_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_subscriptions_reseller_batch_id", ["reseller_batch_id"]
        )
        batch_op.create_unique_constraint(
            "uq_subscriptions_reseller_batch_seat",
            ["reseller_batch_id", "reseller_seat_number"],
        )


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.drop_constraint("uq_subscriptions_reseller_batch_seat", type_="unique")
        batch_op.drop_index("ix_subscriptions_reseller_batch_id")
        batch_op.drop_constraint("fk_subscriptions_reseller_batch_id", type_="foreignkey")
        batch_op.drop_column("reseller_client_telegram")
        batch_op.drop_column("reseller_assigned_at")
        batch_op.drop_column("reseller_seat_number")
        batch_op.drop_column("reseller_batch_id")
    op.drop_index("ix_reseller_key_batches_expires_at", table_name="reseller_key_batches")
    op.drop_index("ix_reseller_key_batches_reseller_id", table_name="reseller_key_batches")
    op.drop_table("reseller_key_batches")
