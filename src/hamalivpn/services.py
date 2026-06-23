import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import AuditLog, Customer, Subscription, SubscriptionStatus, as_utc
from .remnawave import RemnawaveGateway, RemnawaveNotFoundError
from .schemas import TrialResult
from .subscription_health import SubscriptionProbeResult, probe_subscription_url


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
        select(Customer).where(Customer.telegram_id == telegram_id).with_for_update()
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
    expires_at = datetime.now(UTC) + timedelta(days=settings.test_access_days)

    if customer.trial_used:
        existing = await session.scalar(
            select(Subscription)
            .where(
                Subscription.customer_id == customer.id,
                Subscription.plan_code == "trial",
            )
            .order_by(desc(Subscription.created_at))
            .limit(1)
            .with_for_update()
        )
        if existing is None or not existing.remnawave_uuid:
            raise TrialAlreadyUsedError

        try:
            remote = await gateway.update_user_access(
                user_uuid=existing.remnawave_uuid,
                expires_at=expires_at,
                device_limit=settings.trial_device_limit,
                traffic_limit_bytes=settings.trial_traffic_gb * 1024**3,
                squads=settings.squad_uuids,
            )
        except RemnawaveNotFoundError:
            # Юзера удалили в Remnawave — пересоздаём, а не падаем с ошибкой.
            remote = await gateway.create_user(
                username=_remote_username(telegram_id),
                telegram_id=telegram_id,
                expires_at=expires_at,
                device_limit=settings.trial_device_limit,
                traffic_limit_bytes=settings.trial_traffic_gb * 1024**3,
                squads=settings.squad_uuids,
                description=f"HamaliVpn trial; local_subscription={existing.id}",
            )
        existing.status = SubscriptionStatus.active
        existing.expires_at = expires_at
        existing.device_limit = settings.trial_device_limit
        existing.traffic_limit_gb = settings.trial_traffic_gb
        existing.subscription_url = remote.subscription_url
        existing.remnawave_short_uuid = remote.short_uuid
        existing.remnawave_uuid = remote.uuid
        session.add(
            AuditLog(
                actor=f"telegram:{telegram_id}",
                action="trial.extended",
                entity_type="subscription",
                entity_id=existing.id,
                details={
                    "remnawave_uuid": remote.uuid,
                    "expires_at": expires_at.isoformat(),
                },
            )
        )
        await session.commit()
        return TrialResult(
            subscription_id=existing.id,
            access_token=existing.access_token,
            subscription_url=remote.subscription_url,
            connect_url=(f"{settings.public_base_url.rstrip('/')}/connect/{existing.access_token}"),
            expires_at=expires_at,
            device_limit=settings.trial_device_limit,
            traffic_limit_gb=settings.trial_traffic_gb,
        )

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


async def record_subscription_health(
    session: AsyncSession,
    subscription: Subscription,
    settings: Settings,
    *,
    actor: str,
) -> SubscriptionProbeResult:
    previous_status = subscription.health_status
    if not subscription.subscription_url:
        result = SubscriptionProbeResult(
            status="empty",
            message="Ссылка подписки отсутствует",
            endpoint_count=0,
            reachable_count=0,
            response_ms=None,
        )
    else:
        result = await probe_subscription_url(
            subscription.subscription_url,
            timeout_seconds=settings.subscription_probe_timeout_seconds,
            user_agent=settings.subscription_probe_user_agent,
        )

    subscription.health_status = result.status
    subscription.health_message = result.message
    subscription.health_endpoint_count = result.endpoint_count
    subscription.health_reachable_count = result.reachable_count
    subscription.health_response_ms = result.response_ms
    subscription.health_checked_at = datetime.now(UTC)
    if previous_status != result.status:
        session.add(
            AuditLog(
                actor=actor,
                action=f"subscription.health.{result.status}",
                entity_type="subscription",
                entity_id=subscription.id,
                details={
                    "previous_status": previous_status,
                    "message": result.message,
                    "endpoint_count": result.endpoint_count,
                    "reachable_count": result.reachable_count,
                    "response_ms": result.response_ms,
                },
            )
        )
    await session.commit()
    return result


async def refresh_subscription_access(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    settings: Settings,
    subscription: Subscription,
    *,
    actor: str,
) -> SubscriptionProbeResult:
    if not subscription.remnawave_uuid:
        raise SubscriptionNotFoundError

    minimum_expiry = datetime.now(UTC) + timedelta(days=settings.test_access_days)
    expires_at = max(as_utc(subscription.expires_at), minimum_expiry)
    try:
        remote = await gateway.update_user_access(
            user_uuid=subscription.remnawave_uuid,
            expires_at=expires_at,
            device_limit=subscription.device_limit,
            traffic_limit_bytes=subscription.traffic_limit_gb * 1024**3,
            squads=settings.squad_uuids,
        )
    except RemnawaveNotFoundError:
        customer = await session.get(Customer, subscription.customer_id)
        if customer is None:
            raise SubscriptionNotFoundError from None
        remote = await gateway.create_user(
            username=_remote_username(customer.telegram_id),
            telegram_id=customer.telegram_id,
            expires_at=expires_at,
            device_limit=subscription.device_limit,
            traffic_limit_bytes=subscription.traffic_limit_gb * 1024**3,
            squads=settings.squad_uuids,
            description=f"HamaliVpn repaired; local_subscription={subscription.id}",
        )
        subscription.remnawave_uuid = remote.uuid
    subscription.status = SubscriptionStatus.active
    subscription.expires_at = expires_at
    subscription.subscription_url = remote.subscription_url
    subscription.remnawave_short_uuid = remote.short_uuid
    session.add(
        AuditLog(
            actor=actor,
            action="subscription.refreshed",
            entity_type="subscription",
            entity_id=subscription.id,
            details={"remnawave_uuid": remote.uuid},
        )
    )
    await session.commit()
    return await record_subscription_health(
        session,
        subscription,
        settings,
        actor=actor,
    )


async def rotate_subscription_link(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    settings: Settings,
    subscription: Subscription,
    *,
    actor: str,
) -> SubscriptionProbeResult:
    if not subscription.remnawave_uuid:
        raise SubscriptionNotFoundError
    remote = await gateway.revoke_subscription(subscription.remnawave_uuid)
    if remote is None:
        raise SubscriptionNotFoundError
    subscription.subscription_url = remote.subscription_url
    subscription.remnawave_short_uuid = remote.short_uuid
    subscription.status = SubscriptionStatus.active
    session.add(
        AuditLog(
            actor=actor,
            action="subscription.link_rotated",
            entity_type="subscription",
            entity_id=subscription.id,
            details={"remnawave_uuid": remote.uuid},
        )
    )
    await session.commit()
    return await record_subscription_health(
        session,
        subscription,
        settings,
        actor=actor,
    )


async def get_latest_subscription(session: AsyncSession, telegram_id: int) -> Subscription | None:
    statement = (
        select(Subscription)
        .join(Customer)
        .where(Customer.telegram_id == telegram_id)
        .order_by(desc(Subscription.created_at))
        .limit(1)
    )
    return await session.scalar(statement)


async def get_subscription_by_token(session: AsyncSession, token: str) -> Subscription | None:
    return await session.scalar(select(Subscription).where(Subscription.access_token == token))


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
    now = datetime.now(UTC)
    customers = await session.scalar(select(func.count(Customer.id)))
    subscriptions = await session.scalar(select(func.count(Subscription.id)))
    active = await session.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.active,
            Subscription.expires_at > now,
        )
    )
    trials = await session.scalar(
        select(func.count(Subscription.id)).where(Subscription.plan_code == "trial")
    )
    healthy = await session.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.active,
            Subscription.health_status == "healthy",
        )
    )
    unhealthy = await session.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.active,
            Subscription.health_status.in_(("empty", "degraded", "unreachable")),
        )
    )
    return {
        "customers": customers or 0,
        "subscriptions": subscriptions or 0,
        "active": active or 0,
        "trials": trials or 0,
        "healthy": healthy or 0,
        "unhealthy": unhealthy or 0,
    }


async def check_due_subscription_health(
    session: AsyncSession,
    settings: Settings,
) -> int:
    threshold = datetime.now(UTC) - timedelta(seconds=settings.subscription_health_interval_seconds)
    subscriptions = (
        await session.scalars(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.active,
                (
                    (Subscription.health_checked_at.is_(None))
                    | (Subscription.health_checked_at <= threshold)
                ),
            )
            .order_by(Subscription.health_checked_at.asc().nullsfirst())
            .limit(settings.subscription_health_batch_size)
        )
    ).all()
    for subscription in subscriptions:
        await record_subscription_health(
            session,
            subscription,
            settings,
            actor="system:health",
        )
    return len(subscriptions)


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
            try:
                await gateway.disable_user(subscription.remnawave_uuid)
            except RemnawaveNotFoundError:
                pass
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
