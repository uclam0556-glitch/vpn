import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import AuditLog, Customer, Subscription, SubscriptionStatus, as_utc
from .remnawave import RemnawaveGateway
from .schemas import TrialResult


class TrialAlreadyUsedError(RuntimeError):
    pass


class CustomerBlockedError(RuntimeError):
    pass


class SubscriptionNotFoundError(RuntimeError):
    pass


def _remote_username(telegram_id: int) -> str:
    return f"tg_{telegram_id}_{secrets.token_hex(3)}"


async def get_or_create_customer(
    session: AsyncSession,
    *,
    telegram_id: int,
    telegram_username: str | None,
    full_name: str,
) -> Customer:
    customer = await session.scalar(
        select(Customer).where(Customer.telegram_id == telegram_id)
    )
    if customer is None:
        customer = Customer(
            telegram_id=telegram_id,
            telegram_username=telegram_username,
            full_name=full_name,
        )
        session.add(customer)
        await session.flush()
    else:
        customer.telegram_username = telegram_username
        customer.full_name = full_name
    return customer


async def issue_trial(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    settings: Settings,
    *,
    telegram_id: int,
    telegram_username: str | None,
    full_name: str,
) -> TrialResult:
    customer = await get_or_create_customer(
        session,
        telegram_id=telegram_id,
        telegram_username=telegram_username,
        full_name=full_name,
    )
    if customer.is_blocked:
        raise CustomerBlockedError
    if customer.trial_used:
        raise TrialAlreadyUsedError

    expires_at = datetime.now(UTC) + timedelta(minutes=settings.trial_duration_minutes)
    access_token = secrets.token_urlsafe(32)
    subscription = Subscription(
        customer=customer,
        plan_code="trial",
        status=SubscriptionStatus.pending,
        access_token=access_token,
        device_limit=settings.trial_device_limit,
        traffic_limit_gb=settings.trial_traffic_gb,
        expires_at=expires_at,
    )
    session.add(subscription)
    await session.flush()

    remote = await gateway.create_user(
        username=_remote_username(telegram_id),
        telegram_id=telegram_id,
        expires_at=expires_at,
        device_limit=settings.trial_device_limit,
        traffic_limit_bytes=settings.trial_traffic_gb * 1024**3,
        squads=settings.squad_uuids,
        description=f"HamaliVpn trial; local_subscription={subscription.id}",
    )

    subscription.remnawave_uuid = remote.uuid
    subscription.remnawave_short_uuid = remote.short_uuid
    subscription.subscription_url = remote.subscription_url
    subscription.status = SubscriptionStatus.active
    customer.trial_used = True
    session.add(
        AuditLog(
            actor=f"telegram:{telegram_id}",
            action="trial.issued",
            entity_type="subscription",
            entity_id=subscription.id,
            details={
                "remnawave_uuid": remote.uuid,
                "expires_at": expires_at.isoformat(),
            },
        )
    )
    await session.commit()

    return TrialResult(
        subscription_id=subscription.id,
        access_token=access_token,
        subscription_url=remote.subscription_url,
        connect_url=f"{settings.public_base_url.rstrip('/')}/connect/{access_token}",
        expires_at=expires_at,
        device_limit=settings.trial_device_limit,
        traffic_limit_gb=settings.trial_traffic_gb,
    )


async def get_latest_subscription(
    session: AsyncSession, telegram_id: int
) -> Subscription | None:
    statement = (
        select(Subscription)
        .join(Customer)
        .where(Customer.telegram_id == telegram_id)
        .order_by(desc(Subscription.created_at))
        .limit(1)
    )
    return await session.scalar(statement)


async def get_subscription_by_token(
    session: AsyncSession, token: str
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(Subscription.access_token == token)
    )


async def disable_subscription(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    subscription_id: str,
    *,
    actor: str,
) -> Subscription:
    subscription = await session.get(Subscription, subscription_id)
    if subscription is None:
        raise SubscriptionNotFoundError
    if subscription.remnawave_uuid:
        await gateway.disable_user(subscription.remnawave_uuid)
    subscription.status = SubscriptionStatus.disabled
    session.add(
        AuditLog(
            actor=actor,
            action="subscription.disabled",
            entity_type="subscription",
            entity_id=subscription.id,
        )
    )
    await session.commit()
    return subscription


async def dashboard_metrics(session: AsyncSession) -> dict[str, int]:
    customers = (await session.scalars(select(Customer))).all()
    subscriptions = (await session.scalars(select(Subscription))).all()
    now = datetime.now(UTC)
    return {
        "customers": len(customers),
        "subscriptions": len(subscriptions),
        "active": sum(
            item.status == SubscriptionStatus.active and as_utc(item.expires_at) > now
            for item in subscriptions
        ),
        "trials": sum(item.plan_code == "trial" for item in subscriptions),
    }


async def expire_due_subscriptions(
    session: AsyncSession,
    gateway: RemnawaveGateway,
) -> int:
    now = datetime.now(UTC)
    subscriptions = (
        await session.scalars(
            select(Subscription).where(Subscription.status == SubscriptionStatus.active)
        )
    ).all()
    expired = [item for item in subscriptions if as_utc(item.expires_at) <= now]
    for subscription in expired:
        if subscription.remnawave_uuid:
            await gateway.disable_user(subscription.remnawave_uuid)
        subscription.status = SubscriptionStatus.expired
        session.add(
            AuditLog(
                actor="system:maintenance",
                action="subscription.expired",
                entity_type="subscription",
                entity_id=subscription.id,
            )
        )
    if expired:
        await session.commit()
    return len(expired)
