import secrets
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid.uuid4())


class SecretKeyRole(StrEnum):
    admin = "admin"
    reseller = "reseller"


class VpnKeyStatus(StrEnum):
    pending = "pending"
    active = "active"
    expired = "expired"
    disabled = "disabled"
    suspended = "suspended"
    error = "error"
    deleted = "deleted"


class LedgerKind(StrEnum):
    topup = "topup"          # admin credits the reseller
    purchase = "purchase"    # reseller buys a key (debit)
    extend = "extend"        # reseller extends a key (debit)
    adjust = "adjust"        # manual admin correction (signed)
    bonus = "bonus"          # admin credit
    penalty = "penalty"      # admin debit
    refund = "refund"        # credit back


class ResellerLevel(StrEnum):
    start = "start"
    partner = "partner"
    vip = "vip"


class Reseller(Base):
    __tablename__ = "resellers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    level: Mapped[ResellerLevel] = mapped_column(
        Enum(ResellerLevel, native_enum=False), default=ResellerLevel.start
    )
    # Cached balance in kopecks. The ledger is the source of truth; this column
    # is kept in sync inside the same transaction as every ledger entry.
    balance_kopecks: Mapped[int] = mapped_column(BigInteger, default=0)
    # Whether the reseller may go below zero (admin-granted credit line).
    allow_negative: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    secret_keys: Mapped[list["SecretKey"]] = relationship(
        back_populates="reseller", cascade="all, delete-orphan"
    )
    clients: Mapped[list["Client"]] = relationship(
        back_populates="reseller", cascade="all, delete-orphan"
    )
    vpn_keys: Mapped[list["VpnKey"]] = relationship(back_populates="reseller")


class SecretKey(Base):
    """Login credential. The plaintext token is shown once at creation; only its
    SHA-256 hash is stored. Admin keys have reseller_id = NULL."""

    __tablename__ = "portal_secret_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[SecretKeyRole] = mapped_column(Enum(SecretKeyRole, native_enum=False))
    reseller_id: Mapped[int | None] = mapped_column(
        ForeignKey("resellers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # First chars of the token, shown in the UI so a key can be identified
    # without revealing it.
    key_prefix: Mapped[str] = mapped_column(String(12), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reseller: Mapped[Reseller | None] = relationship(back_populates="secret_keys")


class Tariff(Base):
    __tablename__ = "portal_tariffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    duration_days: Mapped[int] = mapped_column(Integer)
    # Base reseller price in kopecks. Per-level / per-reseller overrides live in
    # tariff_prices and take precedence.
    price_kopecks: Mapped[int] = mapped_column(BigInteger)
    device_limit: Mapped[int] = mapped_column(Integer, default=1)
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=0)
    # Comma-separated Remnawave squad UUIDs; empty -> use global default.
    squad_uuids: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    prices: Mapped[list["TariffPrice"]] = relationship(
        back_populates="tariff", cascade="all, delete-orphan"
    )

    @property
    def squads(self) -> list[str]:
        return [s.strip() for s in self.squad_uuids.split(",") if s.strip()]


class TariffPrice(Base):
    """Optional price override for a tariff, by reseller level or specific
    reseller. A reseller-specific override beats a level override beats base."""

    __tablename__ = "portal_tariff_prices"
    __table_args__ = (
        UniqueConstraint("tariff_id", "level", "reseller_id", name="uq_tariff_price_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tariff_id: Mapped[int] = mapped_column(
        ForeignKey("portal_tariffs.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[ResellerLevel | None] = mapped_column(
        Enum(ResellerLevel, native_enum=False), nullable=True
    )
    reseller_id: Mapped[int | None] = mapped_column(
        ForeignKey("resellers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    price_kopecks: Mapped[int] = mapped_column(BigInteger)

    tariff: Mapped[Tariff] = relationship(back_populates="prices")


class Client(Base):
    """Reseller-owned CRM record for an end customer."""

    __tablename__ = "portal_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    reseller_id: Mapped[int] = mapped_column(
        ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    telegram: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    reseller: Mapped[Reseller] = relationship(back_populates="clients")
    vpn_keys: Mapped[list["VpnKey"]] = relationship(back_populates="client")


class VpnKey(Base):
    __tablename__ = "portal_vpn_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    reseller_id: Mapped[int] = mapped_column(
        ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("portal_clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tariff_code: Mapped[str] = mapped_column(String(48))
    status: Mapped[VpnKeyStatus] = mapped_column(
        Enum(VpnKeyStatus, native_enum=False), default=VpnKeyStatus.pending
    )
    remnawave_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    remnawave_short_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_limit: Mapped[int] = mapped_column(Integer, default=1)
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=0)
    price_paid_kopecks: Mapped[int] = mapped_column(BigInteger, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    reseller: Mapped[Reseller] = relationship(back_populates="vpn_keys")
    client: Mapped[Client | None] = relationship(back_populates="vpn_keys")


class LedgerEntry(Base):
    """Append-only money journal. Every balance change writes exactly one row.

    `amount_kopecks` is signed: positive = credit (balance up), negative =
    debit. `balance_after_kopecks` snapshots the reseller balance right after
    this entry for auditability. `idempotency_key` makes a retried request a
    no-op that returns the original result."""

    __tablename__ = "portal_ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    reseller_id: Mapped[int] = mapped_column(
        ForeignKey("resellers.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[LedgerKind] = mapped_column(Enum(LedgerKind, native_enum=False))
    amount_kopecks: Mapped[int] = mapped_column(BigInteger)
    balance_after_kopecks: Mapped[int] = mapped_column(BigInteger)
    vpn_key_id: Mapped[str | None] = mapped_column(
        ForeignKey("portal_vpn_keys.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(80), unique=True, nullable=True, index=True
    )
    comment: Mapped[str] = mapped_column(String(255), default="")
    actor: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def generate_secret_key() -> str:
    """Plaintext login token, shown to the operator exactly once."""
    return "hk_" + secrets.token_urlsafe(32)
