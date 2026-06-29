import asyncio
import html
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    BotCommand,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonDefault,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from . import payments, referrals
from .config import get_settings
from .db import SessionFactory, create_schema
from .models import Customer, Subscription, SubscriptionStatus, as_utc
from .premium_emoji import ce, collect_custom_emojis
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
)

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()
router.include_router(payments.router)
router.include_router(referrals.router)

BANNER = Path(__file__).resolve().parent / "static" / "banner.png"
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


# ── тексты ─────────────────────────────────────────────────────────────────

def welcome_text(name: str) -> str:
    return (
        f"{ce('brand')} <b>{name}</b>, добро пожаловать в <b>HamaliVPN</b>\n\n"
        "Премиальный доступ к свободному интернету — быстро, стабильно и без сложных настроек.\n\n"
        f"{ce('speed')} <b>Быстро</b> — оптимизированная сеть Европы\n"
        f"{ce('shield')} <b>Надёжно</b> — несколько протоколов и резервные направления\n"
        f"{ce('connect')} <b>Просто</b> — подключение в 1–2 клика\n"
        f"{ce('support')} <b>Рядом</b> — поддержка, если что-то не открывается\n\n"
        "Выберите действие ниже 👇"
    )


def subscription_text(subscription: Subscription) -> str:
    status_map = {
        SubscriptionStatus.active: f"{ce('active')} Активна",
        SubscriptionStatus.expired: f"{ce('red')} Истекла",
        SubscriptionStatus.pending: f"{ce('yellow')} Инициализируется",
    }
    status = status_map.get(subscription.status, f"{ce('white')} Неизвестно")

    expires = "∞ Бессрочно"
    if subscription.expires_at:
        delta = as_utc(subscription.expires_at) - datetime.now(UTC)
        days = delta.days
        if days < 0:
            expires = f"{ce('red')} Истекла"
        elif days == 0:
            expires = f"{ce('warning')} Истекает сегодня"
        else:
            expires = f"{ce('calendar')} {days} дн."

    date_line = ""
    if subscription.expires_at:
        date_line = (
            f"\n{ce('calendar')} Дата окончания — "
            f"{as_utc(subscription.expires_at).strftime('%d.%m.%Y')}"
        )

    return (
        f"{ce('user')} <b>Моя подписка</b>\n\n"
        f"Статус — {status}\n"
        f"Осталось — {expires}{date_line}\n"
        f"Лимит устройств — {subscription.device_limit}\n\n"
        f"Нажмите <b>«{ce('connect')} Подключить устройство»</b> — откроется страница быстрой настройки."
    )


def trial_success_text(traffic_label: str, device_limit: int) -> str:
    return (
        f"{ce('check')} <b>Пробный доступ активирован</b>\n\n"
        f"Трафик — {traffic_label}\n"
        f"Лимит устройств — {device_limit}\n\n"
        f"Теперь нажмите <b>«{ce('connect')} Подключить устройство»</b> и импортируйте профиль в приложение."
    )


def info_text() -> str:
    return (
        f"{ce('diamond')} <b>HamaliVPN — коротко</b>\n\n"
        f"{ce('speed')} <b>Скорость</b>\n"
        "   Подбираем локации под мобильные сети и стабильный отклик.\n\n"
        f"{ce('shield')} <b>Устойчивость</b>\n"
        "   VLESS Reality + быстрые LTE/Hysteria направления как резерв.\n\n"
        f"{ce('connect')} <b>Удобство</b>\n"
        "   Подключение через готовую ссылку без ручной настройки.\n\n"
        f"{ce('refresh')} <b>Запас</b>\n"
        "   Несколько резервных направлений внутри одной подписки.\n\n"
        "📱 <b>Платформы</b>\n"
        "   iPhone, Android, Windows, macOS.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{ce('support')} Поддержка: {support_url()}"
    )


def help_text() -> str:
    return (
        f"{ce('connect')} <b>Подключение HamaliVPN</b>\n\n"
        "<b>1.</b> Установите подходящее приложение для VPN на своё устройство.\n"
        "Если не знаете какое выбрать — напишите в поддержку, подскажем быстро.\n\n"
        f"<b>2.</b> Нажмите «👤 Моя подписка» → «{ce('connect')} Подключить устройство».\n\n"
        "<b>3.</b> Откройте ссылку в приложении и включите VPN.\n\n"
        "Если приложение не импортирует профиль или интернет не открывается — напишите в поддержку.\n\n"
        "Нужные кнопки ниже 👇"
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
        InlineKeyboardButton(text="🎁 Пробный доступ", callback_data="trial:create"),
        InlineKeyboardButton(text="💳 Купить", callback_data="menu:buy"),
    )
    builder.row(
        InlineKeyboardButton(text="👤 Моя подписка", callback_data="subscription:show"),
        InlineKeyboardButton(text="⭐️ Бонусы", callback_data="menu:referrals"),
    )
    builder.row(
        InlineKeyboardButton(text="📘 Инструкция", callback_data="help:connect"),
        InlineKeyboardButton(text="💎 О сервисе", callback_data="info:show"),
    )
    builder.row(
        InlineKeyboardButton(text="💬 Поддержка", url=support_url()),
        InlineKeyboardButton(text="📄 Документы", callback_data="docs:menu"),
    )
    return builder.as_markup()


def subscription_keyboard(subscription: Subscription) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📲 Подключить устройство",
            url=f"{settings.public_base_url.rstrip('/')}/connect/{subscription.access_token}",
        )
    )
    builder.row(
        InlineKeyboardButton(text="🔁 Новая ссылка", callback_data="subscription:rotate"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"),
        InlineKeyboardButton(text="💬 Поддержка", url=support_url()),
    )
    return builder.as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Написать в поддержку", url=support_url()))
    builder.row(
        InlineKeyboardButton(text="👤 Моя подписка", callback_data="subscription:show"),
        InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"),
    )
    return builder.as_markup()


def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    builder.row(InlineKeyboardButton(text="💬 Поддержка", url=support_url()))
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
                    referrer_id=None # We need the local DB ID of the referrer, not telegram_id
                )

                # Resolve referrer DB ID
                ref_stmt = select(Customer).where(Customer.telegram_id == referrer_id)
                ref_customer = await session.scalar(ref_stmt)
                if ref_customer:
                    new_customer.referrer_id = ref_customer.id
                    session.add(new_customer)
                    await session.commit()

    text = welcome_text(name)
    kb = home_keyboard()
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


@router.message(Command("status"))
async def show_status(message: Message) -> None:
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
        await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode=ParseMode.HTML)
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
    # Убрать web-app кнопку «Личный кабинет» слева от поля ввода (menu button).
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось сбросить menu button", exc_info=True)
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Открыть HamaliVpn"),
                BotCommand(command="help", description="Помощь и поддержка"),
                BotCommand(command="status", description="Моя подписка"),
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
