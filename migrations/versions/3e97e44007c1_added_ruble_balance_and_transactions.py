"""portal: resellers, secret keys, tariffs, clients, vpn keys, ruble ledger

Revision ID: 3e97e44007c1
Revises: d7a5b53339ec
Create Date: 2026-06-24 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3e97e44007c1"
down_revision = "d7a5b53339ec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resellers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="start"),
        sa.Column("balance_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("allow_negative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_resellers_telegram_id", "resellers", ["telegram_id"])

    op.create_table(
        "portal_secret_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "reseller_id",
            sa.Integer(),
            sa.ForeignKey("resellers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("key_prefix", sa.String(length=12), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_portal_secret_keys_key_hash"),
    )
    op.create_index("ix_portal_secret_keys_reseller_id", "portal_secret_keys", ["reseller_id"])
    op.create_index("ix_portal_secret_keys_key_prefix", "portal_secret_keys", ["key_prefix"])
    op.create_index("ix_portal_secret_keys_key_hash", "portal_secret_keys", ["key_hash"])

    op.create_table(
        "portal_tariffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("device_limit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("traffic_limit_gb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("squad_uuids", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_portal_tariffs_code"),
    )
    op.create_index("ix_portal_tariffs_code", "portal_tariffs", ["code"])

    op.create_table(
        "portal_tariff_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tariff_id",
            sa.Integer(),
            sa.ForeignKey("portal_tariffs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.String(length=16), nullable=True),
        sa.Column(
            "reseller_id",
            sa.Integer(),
            sa.ForeignKey("resellers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("price_kopecks", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("tariff_id", "level", "reseller_id", name="uq_tariff_price_scope"),
    )
    op.create_index("ix_portal_tariff_prices_tariff_id", "portal_tariff_prices", ["tariff_id"])
    op.create_index(
        "ix_portal_tariff_prices_reseller_id", "portal_tariff_prices", ["reseller_id"]
    )

    op.create_table(
        "portal_clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reseller_id",
            sa.Integer(),
            sa.ForeignKey("resellers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("telegram", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_portal_clients_reseller_id", "portal_clients", ["reseller_id"])

    op.create_table(
        "portal_vpn_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "reseller_id",
            sa.Integer(),
            sa.ForeignKey("resellers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("portal_clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tariff_code", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("remnawave_uuid", sa.String(length=36), nullable=True),
        sa.Column("remnawave_short_uuid", sa.String(length=64), nullable=True),
        sa.Column("subscription_url", sa.Text(), nullable=True),
        sa.Column("device_limit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("traffic_limit_gb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_paid_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("remnawave_uuid", name="uq_portal_vpn_keys_remnawave_uuid"),
    )
    op.create_index("ix_portal_vpn_keys_reseller_id", "portal_vpn_keys", ["reseller_id"])
    op.create_index("ix_portal_vpn_keys_client_id", "portal_vpn_keys", ["client_id"])
    op.create_index("ix_portal_vpn_keys_expires_at", "portal_vpn_keys", ["expires_at"])

    op.create_table(
        "portal_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "reseller_id",
            sa.Integer(),
            sa.ForeignKey("resellers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_kopecks", sa.BigInteger(), nullable=False),
        sa.Column(
            "vpn_key_id",
            sa.String(length=36),
            sa.ForeignKey("portal_vpn_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("comment", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_portal_ledger_idempotency_key"),
    )
    op.create_index(
        "ix_portal_ledger_entries_reseller_id", "portal_ledger_entries", ["reseller_id"]
    )
    op.create_index(
        "ix_portal_ledger_entries_idempotency_key", "portal_ledger_entries", ["idempotency_key"]
    )


def downgrade() -> None:
    op.drop_table("portal_ledger_entries")
    op.drop_table("portal_vpn_keys")
    op.drop_table("portal_clients")
    op.drop_table("portal_tariff_prices")
    op.drop_table("portal_tariffs")
    op.drop_table("portal_secret_keys")
    op.drop_index("ix_resellers_telegram_id", table_name="resellers")
    op.drop_table("resellers")
