import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .config import get_settings
from .db import SessionFactory, create_schema
from .device_limits import prune_hwid_devices_to_limit
from .models import AuditLog, Customer, Subscription, SubscriptionStatus, as_utc
from .premium_emoji import ce
from .remnawave import RemnawaveError, make_remnawave_gateway
from .services import (
    check_due_subscription_health,
    expire_due_subscriptions,
    subscription_connect_url,
)
from .telegram_ui import inline_button

logger = logging.getLogger(__name__)

_HWID_ENFORCE_INTERVAL_SECONDS = 60
_HWID_ENFORCE_BATCH_SIZE = 100
_last_hwid_enforce_at: datetime | None = None


def _connect_url(settings, subscription: Subscription) -> str:
    return subscription_connect_url(settings, subscription)


def _support_url(settings) -> str:
    return f"https://t.me/{settings.support_username.lstrip('@')}"


def _expiry_reminder_keyboard(settings, subscription: Subscription):
    from aiogram.types import InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                inline_button(
                    "Продлить доступ",
                    icon="card",
                    style="success",
                    callback_data="menu:buy",
                ),
                inline_button(
                    "Подключить",
                    icon="connect",
                    style="primary",
                    url=_connect_url(settings, subscription),
                ),
            ],
            [
                inline_button("Моя подписка", icon="user", callback_data="subscription:show"),
                inline_button("Поддержка", icon="support", url=_support_url(settings)),
            ],
        ]
    )


def _expiry_reminder_text(subscription: Subscription, days_left: int) -> str:
    if days_left == 1:
        title = "доступ закончится завтра"
        urgency = "Лучше продлить сегодня, чтобы VPN не отключился в самый неудобный момент."
    else:
        title = f"до окончания осталось {days_left} дня"
        urgency = "Можно продлить заранее — оставшиеся дни сохранятся, время не потеряется."

    return (
        f"{ce('calendar')} <b>HamaliVPN: {title}</b>\n\n"
        f"Дата окончания — <b>{as_utc(subscription.expires_at).strftime('%d.%m.%Y')}</b>\n"
        f"Лимит устройств — <b>{subscription.device_limit}</b>\n\n"
        f"{ce('shield')} {urgency}\n\n"
        "Нажмите «💳 Продлить доступ», выберите тариф и оплатите — подписка обновится автоматически."
    )


async def send_expiry_reminders(session, settings) -> int:
    token = settings.bot_token.get_secret_value()
    if not token:
        return 0

    now = datetime.now(UTC)
    horizon = now + timedelta(days=3)
    rows = (
        await session.execute(
            select(Subscription, Customer)
            .join(Customer, Subscription.customer_id == Customer.id)
            .where(
                Subscription.status == SubscriptionStatus.active,
                Subscription.expires_at > now,
                Subscription.expires_at <= horizon,
                Customer.is_blocked.is_(False),
            )
            .order_by(Subscription.expires_at.asc())
        )
    ).all()
    if not rows:
        return 0

    from aiogram import Bot

    sent = 0
    bot = Bot(token=token)
    try:
        for subscription, customer in rows:
            expires_at = as_utc(subscription.expires_at)
            seconds_left = (expires_at - now).total_seconds()
            if seconds_left <= 0:
                continue
            days_left = math.ceil(seconds_left / 86400)
            if days_left not in {1, 2, 3}:
                continue

            expiry_key = expires_at.strftime("%Y%m%d")
            action = f"subscription.expiry_reminder.{days_left}d.{expiry_key}"
            already_sent = await session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.action == action,
                    AuditLog.entity_type == "subscription",
                    AuditLog.entity_id == subscription.id,
                )
                .limit(1)
            )
            if already_sent:
                continue

            try:
                await bot.send_message(
                    customer.telegram_id,
                    _expiry_reminder_text(subscription, days_left),
                    parse_mode="HTML",
                    reply_markup=_expiry_reminder_keyboard(settings, subscription),
                )
            except Exception:
                logger.exception(
                    "Could not send expiry reminder",
                    extra={"subscription_id": subscription.id},
                )
                continue

            session.add(
                AuditLog(
                    actor="system:maintenance",
                    action=action,
                    entity_type="subscription",
                    entity_id=subscription.id,
                    details={
                        "telegram_id": customer.telegram_id,
                        "expires_at": expires_at.isoformat(),
                        "days_left": days_left,
                    },
                )
            )
            sent += 1
        if sent:
            await session.commit()
    finally:
        await bot.session.close()
    return sent


async def enforce_hwid_device_limits(session, gateway) -> int:
    """Strictly prune Remnawave HWID slots to the tariff device limit.

    Remnawave may keep historical HWID slots even when hwidDeviceLimit is lower.
    HamaliVPN's commercial rule is stricter: the first activated devices keep
    their slots; later devices are removed until the owner/reseller manually
    deletes an old device.
    """

    global _last_hwid_enforce_at

    now = datetime.now(UTC)
    if (
        _last_hwid_enforce_at is not None
        and (now - _last_hwid_enforce_at).total_seconds() < _HWID_ENFORCE_INTERVAL_SECONDS
    ):
        return 0
    _last_hwid_enforce_at = now

    rows = (
        (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.expires_at > now,
                    Subscription.remnawave_uuid.is_not(None),
                    Subscription.device_limit >= 1,
                )
                .order_by(Subscription.updated_at.desc())
                .limit(_HWID_ENFORCE_BATCH_SIZE)
            )
        )
        .scalars()
        .all()
    )

    pruned = 0
    for subscription in rows:
        result = await prune_hwid_devices_to_limit(
            user_uuid=subscription.remnawave_uuid,
            device_limit=subscription.device_limit,
            list_devices=gateway.list_hwid_devices,
            delete_device=gateway.delete_hwid_device,
            keep="oldest",
        )
        removed_count = result.get("removed_count", 0)
        if not removed_count:
            continue
        pruned += removed_count
        session.add(
            AuditLog(
                actor="system:maintenance",
                action="subscription.devices.pruned",
                entity_type="subscription",
                entity_id=subscription.id,
                details={
                    "remnawave_uuid": subscription.remnawave_uuid,
                    "device_limit": subscription.device_limit,
                    "result": result,
                    "policy": "keep_first_activated",
                },
            )
        )

    if pruned:
        await session.commit()
    return pruned


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if settings.auto_create_schema:
        await create_schema()
    gateway = make_remnawave_gateway(settings)
    while True:
        try:
            async with SessionFactory() as session:
                count = await expire_due_subscriptions(session, gateway)
                if count:
                    logger.info("Expired %s subscriptions", count)
                checked = await check_due_subscription_health(session, settings)
                if checked:
                    logger.info("Checked %s subscription health records", checked)
                reminded = await send_expiry_reminders(session, settings)
                if reminded:
                    logger.info("Sent %s expiry reminders", reminded)
                pruned = await enforce_hwid_device_limits(session, gateway)
                if pruned:
                    logger.info("Pruned %s extra HWID devices", pruned)
        except RemnawaveError:
            logger.exception("Maintenance could not reach Remnawave")
        except Exception:
            logger.exception("Maintenance loop failed")
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
