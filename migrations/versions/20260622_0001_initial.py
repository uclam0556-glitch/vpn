"""Initial HamaliVpn control schema.

Revision ID: 20260622_0001
Revises:
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260622_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("trial_used", sa.Boolean(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_customers_telegram_id", "customers", ["telegram_id"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "active",
                "disabled",
                "expired",
                "revoked",
                name="subscriptionstatus",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("remnawave_uuid", sa.String(length=36), nullable=True, unique=True),
        sa.Column("remnawave_short_uuid", sa.String(length=64), nullable=True),
        sa.Column("subscription_url", sa.Text(), nullable=True),
        sa.Column("access_token", sa.String(length=64), nullable=False),
        sa.Column("device_limit", sa.Integer(), nullable=False),
        sa.Column("traffic_limit_gb", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
    )
    op.create_index(
        "ix_subscriptions_access_token",
        "subscriptions",
        ["access_token"],
        unique=True,
    )
    op.create_index("ix_subscriptions_customer_id", "subscriptions", ["customer_id"])
    op.create_index("ix_subscriptions_expires_at", "subscriptions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_table("audit_logs")
    op.drop_table("customers")
