import asyncio
import html
import logging
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
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

# Premium Emojis tags (fallback to unicode since custom emojis require specific valid document IDs)
EMOJI_SHIELD = '🛡'
EMOJI_LIGHTNING = '⚡️'
EMOJI_STAR = '⭐️'
EMOJI_DIAMOND = '💎'
EMOJI_ROCKET = '🚀'
EMOJI_GIFT = '🎁'

def support_url() -> str:
    return f"https://t.me/{settings.support_username.lstrip('@')}"


# ── тексты ─────────────────────────────────────────────────────────────────

def welcome_text(name: str) -> str:
    return (
        f"👋 <b>{name}</b>, добро пожаловать в <b>HamaliVPN</b>\n\n"
        "Премиальный VPN для свободного интернета — быстрый, приватный, стабильный.\n\n"
        "⚡️  Скорость без ограничений\n"
        "🛡  Шифрование и полная приватность\n"
        "🌍  Серверы Европы — открыт весь мир\n"
        "📱  До 5 устройств на одной подписке\n\n"
        "Начните с пробного доступа или оформите подписку 👇"
    )


def subscription_text(subscription: Subscription, health: str) -> str:
    status_map = {
        SubscriptionStatus.active: "🟢 Активна",
        SubscriptionStatus.expired: "🔴 Истекла",
        SubscriptionStatus.pending: "🟡 Инициализируется",
    }
    status = status_map.get(subscription.status, "⚪️ Неизвестно")

    expires = "∞ Бессрочно"
    if subscription.expires_at:
        delta = as_utc(subscription.expires_at) - datetime.now(UTC)
        days = delta.days
        if days < 0:
            expires = "🔴 Истекла"
        elif days == 0:
            expires = "⚠️ Истекает сегодня"
        else:
            expires = f"📅 {days} дн."

    return (
        "👤 <b>Моя подписка</b>\n\n"
        f"Статус — {status}\n"
        f"Действует — {expires}\n"
        f"Серверы — {health}\n"
        f"Устройств — {subscription.device_limit}\n\n"
        "Нажмите «Подключить устройство» — приложение настроится автоматически."
    )


def trial_success_text(traffic_label: str, device_limit: int, health: str) -> str:
    return (
        "✅ <b>Доступ активирован</b>\n\n"
        f"Трафик — {traffic_label}\n"
        f"Устройств — {device_limit}\n"
        f"Серверы — {health}\n\n"
        "Нажмите <b>«📲 Подключить устройство»</b> — настройка пройдёт автоматически."
    )


def info_text() -> str:
    return (
        "💎 <b>Почему HamaliVPN?</b>\n\n"
        "⚡️ <b>Скорость</b>\n"
        "   Серверы в Европе с пингом &lt;20 мс\n\n"
        "🛡 <b>Протокол VLESS + REALITY</b>\n"
        "   Невидим для блокировок и DPI\n\n"
        "🔒 <b>Нулевые логи</b>\n"
        "   Мы не храним ни байта твоего трафика\n\n"
        "🔄 <b>Авто-балансировка</b>\n"
        "   Приложение само выбирает быстрый сервер\n\n"
        "📱 <b>Все платформы</b>\n"
        "   iOS, Android, Windows, macOS, Linux\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Поддержка: {support_url()}"
    )


def help_text() -> str:
    return (
        "📘 <b>Как подключиться</b>\n\n"
        "<b>1.</b> Установите приложение:\n"
        "   • iPhone — Streisand\n"
        "   • Android — v2RayTun\n"
        "   • Windows / macOS — Hiddify\n\n"
        "<b>2.</b> Откройте «Моя подписка» → «Подключить устройство».\n\n"
        "<b>3.</b> Выберите приложение — профиль добавится автоматически.\n\n"
        "Скачать приложения 👇"
    )


def refresh_success_text(endpoint_count: int, response_ms: int, endpoint_names: str) -> str:
    return (
        "🔄 <b>Серверы обновлены</b>\n\n"
        f"Доступно серверов — {endpoint_count}\n"
        f"Отклик панели — {response_ms} мс\n\n"
        f"{endpoint_names}\n\n"
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
        InlineKeyboardButton(text="💬 Поддержка", url=support_url()),
    )
    builder.row(
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
        InlineKeyboardButton(text="🔄 Обновить серверы", callback_data="subscription:refresh"),
        InlineKeyboardButton(text="🔁 Новая ссылка", callback_data="subscription:rotate"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"),
        InlineKeyboardButton(text="💬 Поддержка", url=support_url()),
    )
    return builder.as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🍎 Streisand (iPhone)",
            url="https://apps.apple.com/app/streisand/id6450534064",
        ),
        InlineKeyboardButton(
            text="🤖 v2RayTun (Android)",
            url="https://play.google.com/store/apps/details?id=com.v2raytun.android",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💻 Hiddify (PC/Mac)",
            url="https://hiddify.com/",
        )
    )
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


def health_summary(subscription: Subscription) -> str:
    labels = {
        "healthy": "🟢 Готова",
        "degraded": "🟡 Нестабильна",
        "empty": "⚪️ Ещё не выданы",
        "unreachable": "🔴 Недоступна",
        "unknown": "⏳ Проверяется",
    }
    label = labels.get(subscription.health_status, subscription.health_status)
    if subscription.health_endpoint_count:
        label += f"  ({subscription.health_endpoint_count} сервера)"
    return label


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
            f"🔑 Ваш Telegram ID: <code>{message.from_user.id}</code>",
            parse_mode="HTML",
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
        text = "⚠️ Сервер временно недоступен. Уже чиним — попробуй через минуту."
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
    text = trial_success_text(traffic_label, result.device_limit, health_summary(subscription))
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

    text = subscription_text(subscription, health_summary(subscription))
    kb = subscription_keyboard(subscription)
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "subscription:refresh")
async def refresh_subscription(callback: CallbackQuery) -> None:
    await callback.answer("🔄 Проверяю серверы…")
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
            "⚠️ <b>Не удалось обновить серверы</b>\n\n"
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
        endpoint_names = "\n".join(
            f"   ✦ {html.escape(ep.name)}" for ep in result.endpoints[:6]
        )
        text = refresh_success_text(result.endpoint_count, result.response_ms, endpoint_names)
        kb = subscription_keyboard(subscription)
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    text = (
        "⚠️ <b>Серверы ещё не готовы</b>\n\n"
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
            "Серверы ещё инициализируются — попробуй подключиться через минуту."
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
        f"🌍 Серверов: {result.endpoint_count}\n\n"
        "Старая ссылка отключена.\n"
        "Нажми «📲 Подключить устройство» и импортируй профиль заново."
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
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
