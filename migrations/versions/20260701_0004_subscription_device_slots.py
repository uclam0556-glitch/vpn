"""Add subscription device slots.

Revision ID: 20260701_0004
Revises: 20260627_0003
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0004"
down_revision: str | Sequence[str] | None = "20260627_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _index_names(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def _create_index_if_missing(
    inspector,
    table: str,
    name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    if name not in _index_names(inspector, table):
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "subscription_devices" not in _table_names(inspector):
        op.create_table(
            "subscription_devices",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("subscription_id", sa.String(length=36), nullable=False),
            sa.Column("device_token", sa.String(length=64), nullable=False),
            sa.Column("label", sa.String(length=80), nullable=False, server_default="Устройство"),
            sa.Column("platform", sa.String(length=32), nullable=True),
            sa.Column("remnawave_uuid", sa.String(length=36), nullable=True),
            sa.Column("remnawave_short_uuid", sa.String(length=64), nullable=True),
            sa.Column("subscription_url", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("first_ip", sa.String(length=64), nullable=True),
            sa.Column("last_ip", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=255), nullable=True),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("device_token", name="uq_subscription_devices_device_token"),
            sa.UniqueConstraint("remnawave_uuid", name="uq_subscription_devices_remnawave_uuid"),
        )
        inspector = sa.inspect(bind)

    _create_index_if_missing(
        inspector,
        "subscription_devices",
        "ix_subscription_devices_subscription_id",
        ["subscription_id"],
    )
    _create_index_if_missing(
        inspector,
        "subscription_devices",
        "ix_subscription_devices_device_token",
        ["device_token"],
        unique=True,
    )
    _create_index_if_missing(
        inspector,
        "subscription_devices",
        "ix_subscription_devices_remnawave_short_uuid",
        ["remnawave_short_uuid"],
    )
    _create_index_if_missing(
        inspector,
        "subscription_devices",
        "ix_subscription_devices_is_active",
        ["is_active"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "subscription_devices" in _table_names(inspector):
        op.drop_table("subscription_devices")
