import asyncio
import html
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import get_settings
from .db import SessionFactory, create_schema
from .models import Subscription
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

BANNER = Path(__file__).resolve().parent / "static" / "banner.png"


def support_url() -> str:
    return f"https://t.me/{settings.support_username.lstrip('@')}"


# ── тексты ──────────────────────────────────────────────────────────────
def welcome_text(name: str) -> str:
    return (
        "🛡 <b>HamaliVpn</b> — интернет без границ\n\n"
        f"Привет, {name}! 👋\n"
        "Здесь ты получаешь доступ, подключаешь приложение и управляешь "
        "подпиской — без ручной настройки.\n\n"
        "⚡️ Высокая скорость   🔒 Без логов   🌍 Обходит блокировки\n\n"
        "Жми «🚀 Подключиться» — выдам доступ за секунды."
    )


def info_text() -> str:
    return (
        "ℹ️ <b>О HamaliVpn</b>\n\n"
        "⚡️ <b>Скорость</b> — серверы с низким пингом, без лагов\n"
        "🛡 <b>Обход блокировок</b> — работает там, где другие падают\n"
        "🔄 <b>Авто-переключение</b> между серверами\n"
        "🔒 <b>Без логов</b> — твой трафик только твой\n"
        "📱 <b>Все устройства</b> — одна подписка на телефон, ноут, планшет\n\n"
        f"Поддержка: {support_url()}"
    )


def help_text() -> str:
    return (
        "📲 <b>Подключение за минуту</b>\n\n"
        "1️⃣ Установи приложение:\n"
        "  🍎 iPhone — <b>Streisand</b> или <b>v2RayTun</b>\n"
        "  🤖 Android — <b>v2RayTun</b> или <b>Hiddify</b>\n"
        "  💻 Windows / Mac — <b>Hiddify</b>\n\n"
        "2️⃣ Открой «👤 Моя подписка» → «📲 Подключить устройство»\n"
        "3️⃣ Выбери приложение — импорт пройдёт сам\n\n"
        "Кнопки ниже помогут скачать приложение 👇"
    )


# ── клавиатуры ──────────────────────────────────────────────────────────
def home_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🚀 Подключиться", callback_data="trial:create"))
    builder.row(
        InlineKeyboardButton(text="👤 Моя подписка", callback_data="subscription:show"),
        InlineKeyboardButton(text="🔄 Обновить серверы", callback_data="subscription:refresh"),
    )
    builder.row(
        InlineKeyboardButton(text="📲 Инструкция", callback_data="help:connect"),
        InlineKeyboardButton(text="ℹ️ О сервисе", callback_data="info:show"),
    )
    builder.row(InlineKeyboardButton(text="🆘 Поддержка", url=support_url()))
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
        InlineKeyboardButton(text="🔄 Проверить серверы", callback_data="subscription:refresh"),
        InlineKeyboardButton(text="🔁 Новая ссылка", callback_data="subscription:rotate"),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"),
        InlineKeyboardButton(text="🆘 Поддержка", url=support_url()),
    )
    return builder.as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🍎 iPhone (Streisand)",
            url="https://apps.apple.com/app/streisand/id6450534064",
        ),
        InlineKeyboardButton(
            text="🤖 Android (v2RayTun)",
            url="https://play.google.com/store/apps/details?id=com.v2raytun.android",
        ),
    )
    builder.row(
        InlineKeyboardButton(text="💻 Hiddify (PC)", url="https://hiddify.com/")
    )
    builder.row(
        InlineKeyboardButton(text="👤 Моя подписка", callback_data="subscription:show"),
        InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"),
    )
    return builder.as_markup()


def back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"))
    builder.row(InlineKeyboardButton(text="🆘 Поддержка", url=support_url()))
    return builder.as_markup()


def health_summary(subscription: Subscription) -> str:
    labels = {
        "healthy": "готова ✅",
        "degraded": "работает нестабильно ⚠️",
        "empty": "серверы ещё не выданы",
        "unreachable": "временно недоступна",
        "unknown": "ещё не проверена",
    }
    label = labels.get(subscription.health_status, subscription.health_status)
    endpoints = (
        f" · серверов: {subscription.health_endpoint_count}"
        if subscription.health_endpoint_count
        else ""
    )
    return f"{label}{endpoints}"


# ── хендлеры ────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def start(message: Message) -> None:
    name = html.escape(message.from_user.first_name if message.from_user else "друг")
    if BANNER.exists():
        try:
            await message.answer_photo(FSInputFile(BANNER))
        except Exception:  # noqa: BLE001 — баннер не критичен
            logger.warning("Не удалось отправить баннер", exc_info=True)
    await message.answer(welcome_text(name), reply_markup=home_keyboard(), parse_mode=ParseMode.HTML)


@router.message(Command("id"))
async def show_id(message: Message) -> None:
    if message.from_user:
        await message.answer(
            f"Ваш Telegram ID: <code>{message.from_user.id}</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is None:
        return
    name = html.escape(callback.from_user.first_name or "друг")
    await callback.message.edit_text(
        welcome_text(name), reply_markup=home_keyboard(), parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "info:show")
async def info_show(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            info_text(), reply_markup=back_keyboard(), parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == "trial:create")
async def create_trial(callback: CallbackQuery) -> None:
    await callback.answer()
    user = callback.from_user
    if callback.message is None:
        return

    await callback.message.edit_text("⏳ Создаю защищённую подписку…")
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
        await callback.message.edit_text(
            "Не удалось восстановить старую подписку. Напишите в поддержку.",
            reply_markup=back_keyboard(),
        )
        return
    except CustomerBlockedError:
        await callback.message.edit_text(
            "🚫 Доступ ограничен. Напишите в поддержку.", reply_markup=back_keyboard()
        )
        return
    except RemnawaveError:
        logger.exception("Could not create Remnawave user")
        await callback.message.edit_text(
            "⚠️ Панель временно недоступна. Мы уже видим ошибку — попробуйте немного позже.",
            reply_markup=home_keyboard(),
        )
        return

    if subscription is None:
        await callback.message.edit_text("Не удалось сохранить подписку.")
        return
    traffic_label = (
        "безлимитно" if result.traffic_limit_gb == 0 else f"{result.traffic_limit_gb} ГБ"
    )
    await callback.message.edit_text(
        "✅ <b>Доступ активен!</b>\n\n"
        "📅 Срок: без ограничения\n"
        f"📊 Трафик: {traffic_label}\n"
        f"📱 Устройства: {result.device_limit}\n"
        f"🔧 Конфигурация: {health_summary(subscription)}\n\n"
        "Жми «📲 Подключить устройство» — страница сама определит приложение и предложит импорт.",
        reply_markup=subscription_keyboard(subscription),
        parse_mode=ParseMode.HTML,
    )


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
        await callback.message.edit_text(
            "У вас пока нет подписки.\nЖми «🚀 Подключиться», чтобы получить доступ.",
            reply_markup=home_keyboard(),
        )
        return
    await callback.message.edit_text(
        "👤 <b>Ваша подписка</b>\n\n"
        f"📡 Статус: {html.escape(subscription.status.value)}\n"
        "📅 Срок: без ограничения\n"
        f"📱 Устройства: {subscription.device_limit}\n"
        f"🔧 Конфигурация: {health_summary(subscription)}",
        reply_markup=subscription_keyboard(subscription),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "subscription:refresh")
async def refresh_subscription(callback: CallbackQuery) -> None:
    await callback.answer("Проверяю подписку…")
    if callback.message is None:
        return
    gateway = make_remnawave_gateway(settings)
    try:
        async with SessionFactory() as session:
            subscription = await get_latest_subscription(session, callback.from_user.id)
            if subscription is None:
                await callback.message.edit_text(
                    "Подписка ещё не создана.",
                    reply_markup=home_keyboard(),
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
        await callback.message.edit_text(
            "⚠️ Не удалось обновить конфигурацию. Мы сохранили диагностику — "
            "попробуйте ещё раз через минуту.",
            reply_markup=back_keyboard(),
        )
        return

    if result.is_healthy:
        endpoint_names = "\n".join(
            f"• {html.escape(endpoint.name)}" for endpoint in result.endpoints[:6]
        )
        await callback.message.edit_text(
            "✅ <b>Конфигурация обновлена</b>\n\n"
            f"🌍 Доступно серверов: {result.endpoint_count}\n"
            f"⚡️ Ответ панели: {result.response_ms} мс\n\n"
            f"{endpoint_names}\n\n"
            "Жми «📲 Подключить устройство». Если приложение было открыто — "
            "обнови подписку внутри него.",
            reply_markup=subscription_keyboard(subscription),
            parse_mode=ParseMode.HTML,
        )
        return

    await callback.message.edit_text(
        "⚠️ <b>Серверы пока не готовы</b>\n\n"
        f"{html.escape(result.message)}.\n"
        "Повторите проверку через минуту или откройте поддержку.",
        reply_markup=subscription_keyboard(subscription),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "subscription:rotate")
async def rotate_subscription(callback: CallbackQuery) -> None:
    await callback.answer("Выпускаю новую ссылку…")
    if callback.message is None:
        return
    gateway = make_remnawave_gateway(settings)
    try:
        async with SessionFactory() as session:
            subscription = await get_latest_subscription(session, callback.from_user.id)
            if subscription is None:
                await callback.message.edit_text(
                    "Подписка ещё не создана.",
                    reply_markup=home_keyboard(),
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
        await callback.message.edit_text(
            "⚠️ Не удалось выпустить новую ссылку. Попробуйте через минуту.",
            reply_markup=back_keyboard(),
        )
        return

    if not result.is_healthy:
        await callback.message.edit_text(
            "🔁 <b>Новая ссылка создана, но серверы ещё не готовы</b>\n\n"
            f"{html.escape(result.message)}.",
            reply_markup=subscription_keyboard(subscription),
            parse_mode=ParseMode.HTML,
        )
        return

    await callback.message.edit_text(
        "✅ <b>Новая ссылка готова</b>\n\n"
        f"🌍 Проверено серверов: {result.endpoint_count}\n"
        "Старая ссылка отключена. Жми «📲 Подключить устройство» и импортируй "
        "профиль заново — это очищает старый кэш приложения.",
        reply_markup=subscription_keyboard(subscription),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "help:connect")
async def connection_help(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            help_text(), reply_markup=help_keyboard(), parse_mode=ParseMode.HTML
        )


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
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
