"""Ensure reseller portal commercial schema.

Revision ID: 20260627_0003
Revises: c1d2e3f4a5b6
Create Date: 2026-06-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260627_0003"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names(inspector) -> set[str]:
    return set(inspector.get_table_names())


def _column_names(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _index_names(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def _add_column_if_missing(inspector, table: str, column: sa.Column) -> None:
    if column.name not in _column_names(inspector, table):
        op.add_column(table, column)


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
    tables = _table_names(inspector)

    if "customers" in tables:
        _add_column_if_missing(
            inspector,
            "customers",
            sa.Column("balance_rub", sa.Integer(), nullable=False, server_default="0"),
        )
        _add_column_if_missing(
            inspector,
            "customers",
            sa.Column("role", sa.String(length=32), nullable=False, server_default="client"),
        )
        _add_column_if_missing(
            inspector,
            "customers",
            sa.Column("reseller_level", sa.Integer(), nullable=False, server_default="1"),
        )
        _add_column_if_missing(
            inspector,
            "customers",
            sa.Column("portal_access_key", sa.String(length=64), nullable=True),
        )
        _add_column_if_missing(
            inspector,
            "customers",
            sa.Column("withdrawal_method", sa.String(length=32), nullable=True),
        )
        _add_column_if_missing(
            inspector,
            "customers",
            sa.Column("withdrawal_requisites", sa.String(length=255), nullable=True),
        )
        _add_column_if_missing(
            inspector,
            "customers",
            sa.Column("referrer_id", sa.Integer(), nullable=True),
        )
        inspector = sa.inspect(bind)
        _create_index_if_missing(
            inspector,
            "customers",
            "ix_customers_portal_access_key",
            ["portal_access_key"],
            unique=True,
        )

    if "payment_transactions" not in tables:
        op.create_table(
            "payment_transactions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="RUB"),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("external_id", sa.String(length=128), nullable=True, unique=True),
            sa.Column(
                "status",
                sa.Enum("pending", "paid", "cancelled", "expired", native_enum=False),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("payload", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_payment_transactions_customer_id",
            "payment_transactions",
            ["customer_id"],
        )

    if "balance_transactions" not in tables:
        op.create_table(
            "balance_transactions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_balance_transactions_customer_id",
            "balance_transactions",
            ["customer_id"],
        )

    if "withdrawal_requests" not in tables:
        op.create_table(
            "withdrawal_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("requisites", sa.Text(), nullable=False),
            sa.Column(
                "status",
                sa.Enum("pending", "approved", "rejected", native_enum=False),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_withdrawal_requests_customer_id",
            "withdrawal_requests",
            ["customer_id"],
        )

    if "tariffs" not in tables:
        op.create_table(
            "tariffs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("duration_days", sa.Integer(), nullable=False),
            sa.Column("price_rub", sa.Integer(), nullable=False),
            sa.Column("device_limit", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("traffic_limit_gb", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )


def downgrade() -> None:
    # Intentionally no destructive downgrade: this migration is a safe schema repair for production.
    pass
