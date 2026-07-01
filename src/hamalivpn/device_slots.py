import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import AuditLog, Customer, Subscription, SubscriptionDevice, SubscriptionStatus, as_utc, utcnow
from .remnawave import RemnawaveGateway, RemnawaveNotFoundError


class DeviceLimitReached(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_user_agent(value: str | None) -> str:
    return (value or "")[:255]


def _platform_from_user_agent(user_agent: str | None) -> str | None:
    ua = (user_agent or "").lower()
    if "iphone" in ua or "ipad" in ua or "ios" in ua:
        return "iOS"
    if "android" in ua:
        return "Android"
    if "mac os" in ua or "macintosh" in ua:
        return "macOS"
    if "windows" in ua:
        return "Windows"
    if "linux" in ua:
        return "Linux"
    return None


def _device_username(subscription: Subscription, slot_id: int | None = None) -> str:
    suffix = secrets.token_hex(3)
    slot_part = f"{slot_id}_" if slot_id is not None else ""
    return f"dev_{subscription.id[:8]}_{slot_part}{suffix}"[:64]


async def _subscription_customer(session: AsyncSession, subscription: Subscription) -> Customer | None:
    return await session.get(Customer, subscription.customer_id)


def device_subscription_url(settings: Settings, subscription: Subscription, slot: SubscriptionDevice) -> str:
    """Public subscription URL for a device slot.

    The Remnawave user behind the slot has its own shortUuid, but we expose the
    opaque device_token. The local sub_injector resolves that token to the real
    shortUuid and injects the per-device Hysteria auth.
    """
    base = settings.panel_base_url.rstrip()
    source_url = (subscription.subscription_url or "").strip()
    if "/api/sub/" in source_url:
        parsed = urlsplit(source_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
    return f"{base}/api/sub/{slot.device_token}"


async def get_device_slot_by_token(
    session: AsyncSession,
    token: str,
) -> SubscriptionDevice | None:
    token = (token or "").strip()
    if not token:
        return None
    return await session.scalar(select(SubscriptionDevice).where(SubscriptionDevice.device_token == token))


async def active_device_slots(
    session: AsyncSession,
    subscription_id: str,
) -> list[SubscriptionDevice]:
    return (
        await session.scalars(
            select(SubscriptionDevice)
            .where(
                SubscriptionDevice.subscription_id == subscription_id,
                SubscriptionDevice.is_active.is_(True),
            )
            .order_by(asc(SubscriptionDevice.created_at), asc(SubscriptionDevice.id))
        )
    ).all()


async def active_device_slot_count(session: AsyncSession, subscription_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count(SubscriptionDevice.id)).where(
                SubscriptionDevice.subscription_id == subscription_id,
                SubscriptionDevice.is_active.is_(True),
            )
        )
        or 0
    )


async def _create_remote_device_user(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    settings: Settings,
    subscription: Subscription,
    slot: SubscriptionDevice,
) -> None:
    customer = await _subscription_customer(session, subscription)
    telegram_id = customer.telegram_id if customer else 0
    remote = await gateway.create_user(
        username=_device_username(subscription, slot.id),
        telegram_id=telegram_id,
        expires_at=as_utc(subscription.expires_at),
        device_limit=1,
        traffic_limit_bytes=(subscription.traffic_limit_gb or 0) * 1024**3,
        squads=settings.squad_uuids,
        description=f"HamaliVPN device slot; local_subscription={subscription.id}; device_slot={slot.id}",
    )
    slot.remnawave_uuid = remote.uuid
    slot.remnawave_short_uuid = remote.short_uuid
    slot.subscription_url = remote.subscription_url


async def ensure_device_slot(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    settings: Settings,
    subscription: Subscription,
    *,
    existing_token: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> SubscriptionDevice:
    if subscription.status != SubscriptionStatus.active or as_utc(subscription.expires_at) <= utcnow():
        raise DeviceLimitReached("subscription_inactive")

    now = _now()
    ua = _safe_user_agent(user_agent)
    platform = _platform_from_user_agent(ua)

    if existing_token:
        slot = await session.scalar(
            select(SubscriptionDevice).where(
                SubscriptionDevice.device_token == existing_token,
                SubscriptionDevice.subscription_id == subscription.id,
                SubscriptionDevice.is_active.is_(True),
            )
        )
        if slot:
            slot.last_ip = client_ip
            slot.last_seen_at = now
            if not slot.platform:
                slot.platform = platform
            if not slot.user_agent:
                slot.user_agent = ua
            if not slot.remnawave_uuid:
                await _create_remote_device_user(session, gateway, settings, subscription, slot)
            return slot

    # Browser refreshes before the cookie is stored should not burn multiple slots.
    if client_ip and ua:
        reusable = await session.scalar(
            select(SubscriptionDevice)
            .where(
                SubscriptionDevice.subscription_id == subscription.id,
                SubscriptionDevice.is_active.is_(True),
                SubscriptionDevice.first_ip == client_ip,
                SubscriptionDevice.user_agent == ua,
            )
            .order_by(desc(SubscriptionDevice.last_seen_at), desc(SubscriptionDevice.id))
            .limit(1)
        )
        if reusable:
            reusable.last_ip = client_ip
            reusable.last_seen_at = now
            if not reusable.remnawave_uuid:
                await _create_remote_device_user(session, gateway, settings, subscription, reusable)
            return reusable

    limit = max(1, int(subscription.device_limit or 1))
    used = await active_device_slot_count(session, subscription.id)
    if used >= limit:
        raise DeviceLimitReached("device_limit_reached")

    slot = SubscriptionDevice(
        subscription_id=subscription.id,
        device_token=secrets.token_urlsafe(32),
        label=platform or "Устройство",
        platform=platform,
        first_ip=client_ip,
        last_ip=client_ip,
        user_agent=ua,
        activated_at=now,
        last_seen_at=now,
        is_active=True,
    )
    session.add(slot)
    await session.flush()
    await _create_remote_device_user(session, gateway, settings, subscription, slot)
    session.add(
        AuditLog(
            actor=f"system:device-slot:{client_ip or 'unknown'}",
            action="subscription.device_slot.created",
            entity_type="subscription",
            entity_id=subscription.id,
            details={
                "slot_id": slot.id,
                "platform": platform,
                "device_limit": subscription.device_limit,
                "remnawave_uuid": slot.remnawave_uuid,
            },
        )
    )
    return slot


async def sync_subscription_device_slots(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    settings: Settings,
    subscription: Subscription,
    *,
    actor: str,
) -> dict[str, int]:
    slots = await active_device_slots(session, subscription.id)
    limit = max(1, int(subscription.device_limit or 1))
    kept = 0
    disabled = 0
    repaired = 0

    for index, slot in enumerate(slots):
        if index >= limit or subscription.status != SubscriptionStatus.active:
            if slot.remnawave_uuid:
                try:
                    await gateway.disable_user(slot.remnawave_uuid)
                except Exception:
                    pass
            slot.is_active = False
            disabled += 1
            continue

        if not slot.remnawave_uuid:
            await _create_remote_device_user(session, gateway, settings, subscription, slot)
            repaired += 1
        else:
            try:
                remote = await gateway.update_user_access(
                    user_uuid=slot.remnawave_uuid,
                    expires_at=as_utc(subscription.expires_at),
                    device_limit=1,
                    traffic_limit_bytes=(subscription.traffic_limit_gb or 0) * 1024**3,
                    squads=settings.squad_uuids,
                )
                slot.remnawave_short_uuid = remote.short_uuid
                slot.subscription_url = remote.subscription_url
            except RemnawaveNotFoundError:
                await _create_remote_device_user(session, gateway, settings, subscription, slot)
                repaired += 1
        kept += 1

    if disabled or repaired:
        session.add(
            AuditLog(
                actor=actor,
                action="subscription.device_slots.synced",
                entity_type="subscription",
                entity_id=subscription.id,
                details={
                    "kept": kept,
                    "disabled": disabled,
                    "repaired": repaired,
                    "device_limit": subscription.device_limit,
                },
            )
        )
    return {"kept": kept, "disabled": disabled, "repaired": repaired}


async def deactivate_device_slot(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    slot: SubscriptionDevice,
    *,
    actor: str,
) -> None:
    if slot.remnawave_uuid:
        try:
            await gateway.disable_user(slot.remnawave_uuid)
        except Exception:
            pass
    slot.is_active = False
    session.add(
        AuditLog(
            actor=actor,
            action="subscription.device_slot.disabled",
            entity_type="subscription",
            entity_id=slot.subscription_id,
            details={"slot_id": slot.id, "remnawave_uuid": slot.remnawave_uuid},
        )
    )


async def deactivate_subscription_slots(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    subscription: Subscription,
    *,
    actor: str,
) -> int:
    count = 0
    for slot in await active_device_slots(session, subscription.id):
        await deactivate_device_slot(session, gateway, slot, actor=actor)
        count += 1
    return count
