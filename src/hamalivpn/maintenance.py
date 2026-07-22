import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select

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
_CUSTOMER_TIMEZONE = ZoneInfo("Europe/Moscow")


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


def _plan_label(subscription: Subscription) -> str:
    return "Пробный период" if subscription.plan_code == "trial" else "HamaliVPN"


def _expiry_reminder_text(subscription: Subscription, days_left: int) -> str:
    expires_local = as_utc(subscription.expires_at).astimezone(_CUSTOMER_TIMEZONE)
    if days_left == 0:
        title = "Подписка закончилась"
        lead = "VPN-доступ приостановлен, но ваш профиль и настройки сохранены."
        urgency = "Продлите подписку — доступ восстановится автоматически."
    elif days_left == 1:
        title = "Подписка заканчивается завтра"
        lead = "Продлите сегодня, чтобы VPN продолжил работать без перерыва."
        urgency = "Оставшееся время сохранится — вы ничего не потеряете."
    else:
        title = "До окончания подписки осталось 2 дня"
        lead = "Напоминаем заранее, чтобы отключение не застало вас неожиданно."
        urgency = "При продлении текущий профиль и подключённые устройства сохранятся."

    return (
        f"{ce('calendar')} <b>{title}</b>\n\n"
        f"{lead}\n\n"
        f"Дата окончания — <b>{expires_local.strftime('%d.%m.%Y в %H:%M')} МСК</b>\n"
        f"Тариф — <b>{_plan_label(subscription)}</b>\n"
        f"Устройств — <b>{subscription.device_limit}</b>\n\n"
        f"{ce('shield')} {urgency}\n\n"
        "Нажмите «Продлить доступ» — после оплаты подписка обновится автоматически."
    )


async def send_expiry_reminders(session, settings) -> int:
    token = settings.bot_token.get_secret_value()
    if not token:
        return 0

    now = datetime.now(UTC)
    horizon = now + timedelta(days=2, minutes=5)
    expired_since = now - timedelta(days=2)
    rows = (
        await session.execute(
            select(Subscription, Customer)
            .join(Customer, Subscription.customer_id == Customer.id)
            .where(
                Customer.is_blocked.is_(False),
                or_(
                    and_(
                        Subscription.status == SubscriptionStatus.active,
                        Subscription.expires_at > now,
                        Subscription.expires_at <= horizon,
                    ),
                    and_(
                        Subscription.status == SubscriptionStatus.expired,
                        Subscription.expires_at <= now,
                        Subscription.expires_at >= expired_since,
                    ),
                ),
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
            days_left = 0 if seconds_left <= 0 else math.ceil(seconds_left / 86400)
            if days_left not in {0, 1, 2}:
                continue

            phase = "expired" if days_left == 0 else f"{days_left}d"
            expiry_key = expires_at.strftime("%Y%m%dT%H%M%S")
            action = f"subscription.expiry_notice.{phase}.{expiry_key}"
            compatible_actions = [action]
            if days_left:
                # Respect reminders sent by the previous implementation so a
                # deployment cannot duplicate a message already delivered today.
                compatible_actions.append(
                    "subscription.expiry_reminder."
                    f"{days_left}d.{expires_at.strftime('%Y%m%d')}"
                )
            already_sent = await session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.action.in_(compatible_actions),
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
                        "phase": phase,
                    },
                )
            )
            sent += 1
        if sent:
            await session.commit()
    finally:
        await bot.session.close()
    return sent


async def normalize_trial_device_limits(session, gateway, settings) -> int:
    """Converge every trial to the one-device commercial policy.

    This also repairs trials created while production still had the historical
    ``TRIAL_DEVICE_LIMIT=5`` override. Remnawave is updated before the local row,
    so a temporary panel failure is retried without reporting a false limit.
    """

    rows = (
        (
            await session.execute(
                select(Subscription).where(
                    Subscription.plan_code == "trial",
                    Subscription.device_limit != settings.trial_device_limit,
                )
            )
        )
        .scalars()
        .all()
    )
    normalized = 0
    for subscription in rows:
        if subscription.remnawave_uuid and subscription.status == SubscriptionStatus.active:
            await gateway.set_device_limit(
                subscription.remnawave_uuid,
                settings.trial_device_limit,
            )
        previous_limit = subscription.device_limit
        subscription.device_limit = settings.trial_device_limit
        session.add(
            AuditLog(
                actor="system:maintenance",
                action="trial.device_limit_normalized",
                entity_type="subscription",
                entity_id=subscription.id,
                details={
                    "previous_limit": previous_limit,
                    "device_limit": settings.trial_device_limit,
                },
            )
        )
        normalized += 1
    if normalized:
        await session.commit()
    return normalized


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
                normalized = await normalize_trial_device_limits(session, gateway, settings)
                if normalized:
                    logger.info("Normalized %s trial device limits", normalized)
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
