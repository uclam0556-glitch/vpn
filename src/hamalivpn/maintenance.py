import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .config import get_settings
from .db import SessionFactory, create_schema
from .models import AuditLog, Customer, Subscription, SubscriptionStatus, as_utc
from .premium_emoji import ce
from .remnawave import RemnawaveError, make_remnawave_gateway
from .services import check_due_subscription_health, expire_due_subscriptions

logger = logging.getLogger(__name__)


def _connect_url(settings, subscription: Subscription) -> str:
    return f"{settings.public_base_url.rstrip('/')}/connect/{subscription.access_token}"


def _support_url(settings) -> str:
    return f"https://t.me/{settings.support_username.lstrip('@')}"


def _expiry_reminder_keyboard(settings, subscription: Subscription):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Продлить доступ",
                    callback_data="menu:buy",
                ),
                InlineKeyboardButton(
                    text="📲 Подключить",
                    url=_connect_url(settings, subscription),
                ),
            ],
            [
                InlineKeyboardButton(text="👤 Моя подписка", callback_data="subscription:show"),
                InlineKeyboardButton(text="💬 Поддержка", url=_support_url(settings)),
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
        except RemnawaveError:
            logger.exception("Maintenance could not reach Remnawave")
        except Exception:
            logger.exception("Maintenance loop failed")
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
