import asyncio
import html
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from . import integration, payments, referrals
from .config import get_settings
from .db import SessionFactory, create_schema
from .models import Customer, Subscription, SubscriptionStatus, as_utc
from .premium_emoji import ce, collect_custom_emojis
from .public_urls import public_connect_base_url
from .remnawave import RemnawaveError, make_remnawave_gateway
from .services import (
    CustomerBlockedError,
    SubscriptionNotFoundError,
    TrialAlreadyUsedError,
    get_latest_subscription,
    issue_trial,
    record_subscription_health,
    refresh_subscription_access,
    rotate_subscription_link,
    subscription_connect_url,
)

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()
router.include_router(payments.router)
router.include_router(referrals.router)
router.include_router(integration.integration_router)

BANNER = Path(__file__).resolve().parent / "static" / "banner-v2.png"
PREMIUM_EMOJI_KEYS = [
    "brand",
    "speed",
    "support",
    "connect",
    "active",
    "calendar",
    "gift",
    "card",
    "user",
    "star",
    "book",
    "diamond",
    "chat",
    "doc",
    "phone",
    "refresh",
    "shield",
    "lightning",
    "rocket",
    "money",
    "bank",
    "home",
    "sparkles",
]
_premium_emoji_capture: dict[int, list[dict[str, str]]] = {}


class TapThrottleMiddleware(BaseMiddleware):
    """Small in-memory guard against double taps and command spam.

    It does not replace DB-level idempotency; it only makes the bot feel calmer
    and prevents accidental double-clicks on expensive actions such as trial
    provisioning or key rotation.
    """

    def __init__(self, interval_seconds: float = 0.85) -> None:
        self.interval_seconds = interval_seconds
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        if isinstance(event, Message) and collect_custom_emojis(event):
            return await handler(event, data)

        now = time.monotonic()
        last = self._last_seen.get(user.id, 0.0)
        if now - last < self.interval_seconds:
            if isinstance(event, CallbackQuery):
                await event.answer("Секунду…", show_alert=False)
            return None

        self._last_seen[user.id] = now
        return await handler(event, data)


def support_url() -> str:
    return f"https://t.me/{settings.support_username.lstrip('@')}"


def mini_app_url(*, screen: str = "home", action: str = "") -> str:
    base = f"{public_connect_base_url(settings)}/tma/"
    query = f"screen={screen}"
    if action:
        query += f"&action={action}"
    return f"{base}?{query}"


# ── тексты ─────────────────────────────────────────────────────────────────


def welcome_text(name: str) -> str:
    return (
        f"{ce('brand')} <b>HamaliVPN</b>\n\n"
        f"Привет, <b>{name}</b>! Свободный интернет без сложных настроек.\n\n"
        "Подключение, продление, устройства и бонусы — в одном Mini App.\n\n"
        f"{ce('rocket')} <b>Откройте HamaliVPN и подключайтесь.</b>"
    )


def subscription_text(subscription: Subscription) -> str:
    status_map = {
        SubscriptionStatus.active: f"{ce('active')} Активна",
        SubscriptionStatus.expired: f"{ce('red')} Истекла",
        SubscriptionStatus.pending: f"{ce('yellow')} Инициализируется",
    }
    status = status_map.get(subscription.status, f"{ce('white')} Неизвестно")

    validity = "Бессрочно"
    if subscription.expires_at:
        delta = as_utc(subscription.expires_at) - datetime.now(UTC)
        days = delta.days
        if days < 0:
            validity = "Срок действия завершён"
        elif days == 0:
            validity = "Действует до конца дня"
        else:
            validity = f"До {as_utc(subscription.expires_at).strftime('%d.%m.%Y')}"

    return (
        f"{ce('shield')} <b>Моя подписка</b>\n\n"
        f"{status}\n"
        f"{ce('calendar')} <b>{validity}</b>\n"
        f"{ce('phone')} До <b>{subscription.device_limit}</b> устройств\n\n"
        "Подключение и управление устройствами доступны в Mini App."
    )


def trial_success_text(traffic_label: str, device_limit: int) -> str:
    return (
        f"{ce('check')} <b>Пробный доступ активирован</b>\n\n"
        f"{traffic_label} · {device_limit} устройство\n\n"
        "Откройте Mini App — подберём приложение и подключим VPN."
    )


def info_text() -> str:
    return (
        f"{ce('shield')} <b>О HamaliVPN</b>\n\n"
        f"{ce('lightning')} Быстрые локации и автоматическое обновление\n"
        f"{ce('lock')} Современные протоколы и резервные подключения\n"
        f"{ce('phone')} iPhone, Android, Windows и macOS\n\n"
        "Настройка занимает пару минут в Mini App."
    )


def help_text() -> str:
    return (
        f"{ce('support')} <b>Помощь с подключением</b>\n\n"
        "<b>1.</b> Откройте HamaliVPN.\n"
        "<b>2.</b> Нажмите «Подключить VPN».\n"
        "<b>3.</b> Следуйте инструкции для своего устройства.\n\n"
        "Если не получилось — поддержка ответит и поможет."
    )


def refresh_success_text(response_ms: int) -> str:
    return (
        f"{ce('check')} <b>Профиль подключения обновлён</b>\n\n"
        f"Отклик панели — {response_ms} мс\n\n"
        "Если приложение открыто — обновите подписку внутри него."
    )


# ── клавиатуры ─────────────────────────────────────────────────────────────


def home_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🚀 Открыть HamaliVPN",
            web_app=WebAppInfo(url=mini_app_url()),
        )
    )
    builder.row(
        InlineKeyboardButton(text="⚡ Подключить", callback_data="subscription:show"),
        InlineKeyboardButton(text="💳 Тарифы", callback_data="menu:buy"),
    )
    builder.row(
        InlineKeyboardButton(text="🎁 Пробный доступ", callback_data="trial:create"),
        InlineKeyboardButton(text="✨ Бонусы", callback_data="menu:referrals"),
    )
    builder.row(
        InlineKeyboardButton(text="🛟 Помощь", callback_data="help:connect"),
    )
    return builder.as_markup()


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🚀 Открыть HamaliVPN",
                    web_app=WebAppInfo(url=mini_app_url()),
                )
            ],
            [KeyboardButton(text="⚡ Подключить"), KeyboardButton(text="🛟 Помощь")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def subscription_keyboard(subscription: Subscription) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🚀 Подключить в Mini App",
            web_app=WebAppInfo(url=mini_app_url(screen="subscription", action="connect")),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔗 Открыть настройку",
            url=subscription_connect_url(settings, subscription),
        )
    )
    builder.row(
        InlineKeyboardButton(text="💳 Продлить", callback_data="menu:buy"),
        InlineKeyboardButton(text="↻ Обновить ссылку", callback_data="subscription:rotate"),
    )
    builder.row(
        InlineKeyboardButton(text="← Назад", callback_data="menu:home"),
        InlineKeyboardButton(text="🛟 Помощь", url=support_url()),
    )
    return builder.as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🚀 Открыть инструкцию",
            web_app=WebAppInfo(url=mini_app_url(screen="support")),
        )
    )
    builder.row(InlineKeyboardButton(text="🛟 Написать в поддержку", url=support_url()))
    builder.row(
        InlineKeyboardButton(text="⚡ Моя подписка", callback_data="subscription:show"),
        InlineKeyboardButton(text="← Назад", callback_data="menu:home"),
    )
    return builder.as_markup()


def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="← Назад", callback_data="menu:home"),
        InlineKeyboardButton(text="🛟 Помощь", url=support_url()),
    )
    return builder.as_markup()


# ── хендлеры ───────────────────────────────────────────────────────────────


@router.message(CommandStart())
async def start(message: Message, command: CommandObject) -> None:
    name = html.escape(message.from_user.first_name if message.from_user else "друг")

    # Process referral
    referrer_id = None
    args = command.args
    if args and args.startswith("ref_"):
        try:
            referrer_id = int(args.split("_")[1])
        except ValueError:
            pass

    # Link referral if user is new
    if referrer_id and message.from_user:
        async with SessionFactory() as session:
            stmt = select(Customer).where(Customer.telegram_id == message.from_user.id)
            existing = await session.scalar(stmt)
            if not existing and referrer_id != message.from_user.id:
                # Save mapping to memory/temp or create partial Customer here
                # Our issue_trial function automatically creates the Customer
                # For a full implementation, we'd need to create the Customer row now
                # Since issue_trial handles it, we can create the customer right here.
                new_customer = Customer(
                    telegram_id=message.from_user.id,
                    telegram_username=message.from_user.username,
                    full_name=message.from_user.full_name or "",
                    referrer_id=None,  # We need the local DB ID of the referrer, not telegram_id
                )

                # Resolve referrer DB ID
                ref_stmt = select(Customer).where(Customer.telegram_id == referrer_id)
                ref_customer = await session.scalar(ref_stmt)
                if ref_customer:
                    new_customer.referrer_id = ref_customer.id
                    session.add(new_customer)
                    await session.commit()

    text = welcome_text(name)
    kb = main_reply_keyboard()
    if BANNER.exists():
        try:
            await message.answer_photo(
                FSInputFile(BANNER),
                caption=text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось отправить баннер", exc_info=True)
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.message(Command("id"))
async def show_id(message: Message) -> None:
    if message.from_user:
        await message.answer(
            f"{ce('key')} Ваш Telegram ID: <code>{message.from_user.id}</code>",
            parse_mode="HTML",
        )


@router.message(Command("emoji"))
async def premium_emoji_help(message: Message) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        await message.answer(help_text(), reply_markup=help_keyboard(), parse_mode=ParseMode.HTML)
        return
    _premium_emoji_capture[message.from_user.id] = []
    await message.answer(
        f"{ce('sparkles')} <b>Премиум-иконки HamaliVPN</b>\n\n"
        "Сбор начат заново. Отправляйте красивые Telegram Premium emoji — можно пачкой или отдельными сообщениями.\n"
        "Я буду собирать их по порядку и давать готовую строку для настройки.\n\n"
        "Лучший порядок для текущего дизайна:\n"
        f"<code>{' '.join(PREMIUM_EMOJI_KEYS)}</code>",
        parse_mode=ParseMode.HTML,
    )


@router.message(lambda message: bool(collect_custom_emojis(message)))
async def collect_premium_emoji_ids(message: Message) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_ids:
        return

    items = collect_custom_emojis(message)
    if not items:
        return

    captured = _premium_emoji_capture.setdefault(message.from_user.id, [])
    known_ids = {item["custom_emoji_id"] for item in captured}
    added = 0
    for item in items:
        if item["custom_emoji_id"] in known_ids:
            continue
        captured.append(item)
        known_ids.add(item["custom_emoji_id"])
        added += 1

    suggested = {
        key: item["custom_emoji_id"]
        for key, item in zip(PREMIUM_EMOJI_KEYS, captured, strict=False)
    }
    rows = [
        f"{index}. <code>{PREMIUM_EMOJI_KEYS[index - 1] if index <= len(PREMIUM_EMOJI_KEYS) else 'extra'}</code> "
        f"{html.escape(item['fallback'] or 'emoji')} — <code>{item['custom_emoji_id']}</code>"
        for index, item in enumerate(captured, start=1)
    ]
    env_line = "PREMIUM_EMOJI_JSON=" + json.dumps(suggested, ensure_ascii=False)
    await message.answer(
        f"{ce('sparkles')} <b>Поймал premium emoji</b>\n"
        f"Новых: <b>{added}</b> · всего: <b>{len(captured)}</b> из <b>{len(PREMIUM_EMOJI_KEYS)}</b>\n\n"
        + "\n".join(rows)
        + "\n\n"
        "Готовая строка для <code>.env</code>:\n"
        f"<code>{html.escape(env_line)}</code>\n\n"
        "Когда список будет полный, напишите мне — я применю её и перезапущу бота.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("help"))
async def show_help(message: Message) -> None:
    await message.answer(help_text(), reply_markup=help_keyboard(), parse_mode=ParseMode.HTML)


async def send_subscription(message: Message) -> None:
    if message.from_user is None:
        return
    async with SessionFactory() as session:
        subscription = await get_latest_subscription(session, message.from_user.id)
        if subscription is not None:
            await record_subscription_health(
                session,
                subscription,
                settings,
                actor=f"telegram:{message.from_user.id}",
            )
    if subscription is None:
        await message.answer(
            f"{ce('user')} <b>Подписка не найдена</b>\n\n"
            "Активируйте пробный доступ или оформите подписку.",
            reply_markup=home_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return
    await message.answer(
        subscription_text(subscription),
        reply_markup=subscription_keyboard(subscription),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("status"))
async def show_status(message: Message) -> None:
    await send_subscription(message)


@router.message(F.text == "⚡ Подключить")
async def quick_connect(message: Message) -> None:
    await send_subscription(message)


@router.message(F.text == "🛟 Помощь")
async def quick_help(message: Message) -> None:
    await message.answer(help_text(), reply_markup=help_keyboard(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    name = html.escape(callback.from_user.first_name or "друг")
    text = welcome_text(name)
    kb = home_keyboard()
    # Если текущее сообщение — фото, редактируем подпись, иначе текст
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "info:show")
async def info_show(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    text = info_text()
    kb = back_keyboard()
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "trial:create")
async def create_trial(callback: CallbackQuery) -> None:
    await callback.answer()
    user = callback.from_user
    if callback.message is None:
        return

    loading_text = "⏳ Активирую подписку…"
    if callback.message.photo:
        await callback.message.edit_caption(caption=loading_text)
    else:
        await callback.message.edit_text(loading_text)

    gateway = make_remnawave_gateway(settings)
    try:
        async with SessionFactory() as session:
            result = await issue_trial(
                session,
                gateway,
                settings,
                telegram_id=user.id,
                telegram_username=user.username,
                full_name=user.full_name,
            )
            subscription = await session.get(Subscription, result.subscription_id)
            if subscription is not None:
                await record_subscription_health(
                    session,
                    subscription,
                    settings,
                    actor=f"telegram:{user.id}",
                )
    except TrialAlreadyUsedError:
        text = (
            "⚠️ <b>Пробный период уже использован</b>\n\n"
            "Откройте «👤 Моя подписка», чтобы управлять доступом, "
            "или оформите подписку кнопкой «💳 Купить»."
        )
        kb = back_keyboard()
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return
    except CustomerBlockedError:
        text = "🚫 <b>Доступ ограничен</b>\n\nНапишите в поддержку."
        kb = back_keyboard()
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return
    except RemnawaveError:
        logger.exception("Could not create Remnawave user")
        text = "⚠️ Сервис временно недоступен. Уже чиним — попробуй через минуту."
        kb = home_keyboard()
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    if subscription is None:
        await callback.message.edit_text("Не удалось сохранить подписку.")
        return

    traffic_label = (
        "∞ Безлимит" if result.traffic_limit_gb == 0 else f"{result.traffic_limit_gb} ГБ"
    )
    text = trial_success_text(traffic_label, result.device_limit)
    kb = subscription_keyboard(subscription)
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "subscription:show")
async def show_subscription(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    async with SessionFactory() as session:
        subscription = await get_latest_subscription(session, callback.from_user.id)
        if subscription is not None:
            await record_subscription_health(
                session,
                subscription,
                settings,
                actor=f"telegram:{callback.from_user.id}",
            )
    if subscription is None:
        text = (
            "👤 <b>Подписка не найдена</b>\n\n"
            "Нажмите «🎁 Пробный доступ», чтобы активировать тест, "
            "или «💳 Купить» для полной подписки."
        )
        kb = home_keyboard()
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    text = subscription_text(subscription)
    kb = subscription_keyboard(subscription)
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "subscription:refresh")
async def refresh_subscription(callback: CallbackQuery) -> None:
    await callback.answer("🔄 Обновляю профиль…")
    if callback.message is None:
        return
    gateway = make_remnawave_gateway(settings)
    try:
        async with SessionFactory() as session:
            subscription = await get_latest_subscription(session, callback.from_user.id)
            if subscription is None:
                text = "Подписка ещё не создана."
                kb = home_keyboard()
                if callback.message.photo:
                    await callback.message.edit_caption(
                        caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
                    )
                else:
                    await callback.message.edit_text(
                        text, reply_markup=kb, parse_mode=ParseMode.HTML
                    )
                return
            result = await refresh_subscription_access(
                session,
                gateway,
                settings,
                subscription,
                actor=f"telegram:{callback.from_user.id}",
            )
    except (RemnawaveError, SubscriptionNotFoundError):
        logger.exception("Could not refresh subscription")
        text = (
            "⚠️ <b>Не удалось обновить профиль подключения</b>\n\n"
            "Уже смотрим на проблему. Попробуй через минуту."
        )
        kb = back_keyboard()
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    if result.is_healthy:
        text = refresh_success_text(result.response_ms)
        kb = subscription_keyboard(subscription)
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    text = (
        "⚠️ <b>Профиль ещё обновляется</b>\n\n"
        f"{html.escape(result.message)}.\n\n"
        "Повтори проверку через минуту."
    )
    kb = subscription_keyboard(subscription)
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "subscription:rotate")
async def rotate_subscription(callback: CallbackQuery) -> None:
    await callback.answer("🔁 Генерирую новую ссылку…")
    if callback.message is None:
        return
    gateway = make_remnawave_gateway(settings)
    try:
        async with SessionFactory() as session:
            subscription = await get_latest_subscription(session, callback.from_user.id)
            if subscription is None:
                text = "Подписка ещё не создана."
                kb = home_keyboard()
                if callback.message.photo:
                    await callback.message.edit_caption(
                        caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
                    )
                else:
                    await callback.message.edit_text(
                        text, reply_markup=kb, parse_mode=ParseMode.HTML
                    )
                return
            result = await rotate_subscription_link(
                session,
                gateway,
                settings,
                subscription,
                actor=f"telegram:{callback.from_user.id}",
            )
    except (RemnawaveError, SubscriptionNotFoundError):
        logger.exception("Could not rotate subscription link")
        text = "⚠️ <b>Не удалось создать новую ссылку.</b>\n\nПопробуй через минуту."
        kb = back_keyboard()
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    if not result.is_healthy:
        text = (
            "🔁 <b>Новая ссылка создана</b>\n\n"
            f"⚠️ {html.escape(result.message)}.\n\n"
            "Профиль ещё инициализируется — попробуй подключиться через минуту."
        )
        kb = subscription_keyboard(subscription)
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    text = (
        "✅ <b>Новая ссылка готова!</b>\n\n"
        "Старая ссылка отключена.\n"
        f"Нажми «{ce('connect')} Подключить устройство» и импортируй профиль заново."
    )
    kb = subscription_keyboard(subscription)
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "help:connect")
async def connection_help(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    text = help_text()
    kb = help_keyboard()
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


# ── документы (Политика и Соглашение) ───────────────────────────────────────


def docs_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔒 Политика конфиденциальности", url="https://app.hamali.ru/privacy"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📜 Пользовательское соглашение", url="https://app.hamali.ru/terms"
        )
    )
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    return builder.as_markup()


@router.callback_query(F.data == "docs:menu")
async def docs_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    text = (
        "📄 <b>Правовая информация</b>\n\n"
        "Используя HamaliVPN, вы принимаете условия сервиса:\n"
        "• мы не храним логи вашего трафика;\n"
        "• доступ — цифровая услуга, выдаётся сразу после оплаты;\n"
        "• оплаченный доступ — для личного использования в пределах лимита устройств.\n\n"
        "Полные документы — по ссылкам ниже 👇"
    )
    kb = docs_menu_keyboard()
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = settings.bot_token.get_secret_value()
    if not token:
        raise RuntimeError("BOT_TOKEN is empty")
    if settings.auto_create_schema:
        await create_schema()
    bot = Bot(token=token)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Открыть HamaliVPN",
                web_app=WebAppInfo(url=mini_app_url()),
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось сбросить menu button", exc_info=True)
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="status", description="Подключение и подписка"),
                BotCommand(command="help", description="Помощь"),
                BotCommand(command="id", description="Мой Telegram ID"),
            ]
        )
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось обновить команды бота", exc_info=True)
    dispatcher = Dispatcher()
    dispatcher.message.middleware(TapThrottleMiddleware(interval_seconds=0.45))
    dispatcher.callback_query.middleware(TapThrottleMiddleware(interval_seconds=0.85))
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
