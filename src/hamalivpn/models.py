import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class SubscriptionStatus(StrEnum):
    pending = "pending"
    active = "active"
    disabled = "disabled"
    expired = "expired"
    revoked = "revoked"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    balance_rub: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(32), default="client", server_default="client")
    reseller_level: Mapped[int] = mapped_column(default=1, server_default="1")
    portal_access_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    withdrawal_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    withdrawal_requisites: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    referrer: Mapped["Customer"] = relationship(
        "Customer", remote_side="Customer.id", back_populates="referrals"
    )
    referrals: Mapped[list["Customer"]] = relationship("Customer", back_populates="referrer")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    plan_code: Mapped[str] = mapped_column(String(32), default="trial")
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False), default=SubscriptionStatus.pending
    )
    remnawave_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    remnawave_short_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_limit: Mapped[int] = mapped_column(Integer, default=1)
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=0)
    reseller_batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("reseller_key_batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reseller_seat_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reseller_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reseller_client_telegram: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    health_status: Mapped[str] = mapped_column(String(16), default="unknown")
    health_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    health_endpoint_count: Mapped[int] = mapped_column(Integer, default=0)
    health_reachable_count: Mapped[int] = mapped_column(Integer, default=0)
    health_response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    customer: Mapped[Customer] = relationship(back_populates="subscriptions")
    devices: Mapped[list["SubscriptionDevice"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )
    reseller_batch: Mapped["ResellerKeyBatch | None"] = relationship(
        back_populates="subscriptions"
    )

    __table_args__ = (
        UniqueConstraint(
            "reseller_batch_id",
            "reseller_seat_number",
            name="uq_subscriptions_reseller_batch_seat",
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.status == SubscriptionStatus.active and as_utc(self.expires_at) > utcnow()


class SubscriptionDevice(Base):
    __tablename__ = "subscription_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[str] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    device_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(80), default="Устройство")
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remnawave_uuid: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    remnawave_short_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    subscription_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subscription: Mapped[Subscription] = relationship(back_populates="devices")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentStatus(StrEnum):
    pending = "pending"
    paid = "paid"
    cancelled = "cancelled"
    expired = "expired"


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    provider: Mapped[str] = mapped_column(String(32))  # e.g., cryptomus, yookassa, manual
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False), default=PaymentStatus.pending
    )
    payload: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g., plan code
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WithdrawalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    requisites: Mapped[str] = mapped_column(Text)
    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus, native_enum=False), default=WithdrawalStatus.pending
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    duration_days: Mapped[int] = mapped_column(Integer)
    price_rub: Mapped[int] = mapped_column(Integer)
    device_limit: Mapped[int] = mapped_column(Integer, default=1)
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ResellerKeyBatch(Base):
    __tablename__ = "reseller_key_batches"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    reseller_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    tariff_id: Mapped[int | None] = mapped_column(
        ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    total_seats: Mapped[int] = mapped_column(Integer)
    duration_days: Mapped[int] = mapped_column(Integer)
    price_rub: Mapped[int] = mapped_column(Integer)
    traffic_limit_gb: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="active", server_default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="reseller_batch", order_by="Subscription.reseller_seat_number"
    )

    __table_args__ = (
        UniqueConstraint("reseller_id", "request_id", name="uq_reseller_batch_request"),
    )


class IntegrationLink(Base):
    __tablename__ = "integration_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    hwid: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(255))
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    nodes: Mapped[list["IntegrationNode"]] = relationship(
        back_populates="link", cascade="all, delete-orphan"
    )


class IntegrationNode(Base):
    __tablename__ = "integration_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    link_id: Mapped[int] = mapped_column(
        ForeignKey("integration_links.id", ondelete="CASCADE"), index=True
    )
    raw_link: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    source_position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    link: Mapped[IntegrationLink] = relationship(back_populates="nodes")


class FeatureFlag(Base):
    """Runtime switch for gradual, deterministic application rollouts.

    Flags are deliberately application-level: the current production path stays
    unchanged until a flag is explicitly enabled and its rollout percentage is
    raised above zero.
    """

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    rollout_percent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )
