import asyncio
import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
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


def home_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Подключить HamaliVpn", callback_data="trial:create")
    builder.button(text="Моя подписка", callback_data="subscription:show")
    builder.button(text="Проверить и обновить", callback_data="subscription:refresh")
    builder.button(text="Как подключиться", callback_data="help:connect")
    builder.adjust(1)
    return builder.as_markup()


def subscription_keyboard(subscription: Subscription) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Подключить устройство",
            url=f"{settings.public_base_url.rstrip('/')}/connect/{subscription.access_token}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Проверить и обновить серверы",
            callback_data="subscription:refresh",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Новая ссылка подключения",
            callback_data="subscription:rotate",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Поддержка",
            url=f"https://t.me/{settings.support_username.lstrip('@')}",
        )
    )
    return builder.as_markup()


def health_summary(subscription: Subscription) -> str:
    labels = {
        "healthy": "готова",
        "degraded": "работает нестабильно",
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


@router.message(CommandStart())
async def start(message: Message) -> None:
    name = html.escape(message.from_user.first_name if message.from_user else "друг")
    text = (
        f"<b>HamaliVpn</b>\n\n"
        f"{name}, здесь можно получить доступ, подключить приложение и управлять "
        "подпиской без ручной настройки.\n\n"
        "На этапе тестирования доступ выдаётся без почасового ограничения."
    )
    await message.answer(text, reply_markup=home_keyboard(), parse_mode=ParseMode.HTML)


@router.message(Command("id"))
async def show_id(message: Message) -> None:
    if message.from_user:
        await message.answer(
            f"Ваш Telegram ID: <code>{message.from_user.id}</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "trial:create")
async def create_trial(callback: CallbackQuery) -> None:
    await callback.answer()
    user = callback.from_user
    if callback.message is None:
        return

    await callback.message.edit_text("Создаю защищённую подписку…")
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
            reply_markup=home_keyboard(),
        )
        return
    except CustomerBlockedError:
        await callback.message.edit_text("Доступ ограничен. Напишите в поддержку.")
        return
    except RemnawaveError:
        logger.exception("Could not create Remnawave user")
        await callback.message.edit_text(
            "Панель временно недоступна. Мы уже видим ошибку — попробуйте немного позже.",
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
        "<b>Тестовый доступ активен</b>\n\n"
        "Срок: без почасового ограничения\n"
        f"Трафик: {traffic_label}\n"
        f"Устройства: {result.device_limit}\n"
        f"Конфигурация: {health_summary(subscription)}\n\n"
        "Нажмите кнопку — страница определит приложение и предложит импорт.",
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
            "У вас пока нет подписки.",
            reply_markup=home_keyboard(),
        )
        return
    await callback.message.edit_text(
        "<b>Ваша подписка</b>\n\n"
        f"Статус: {html.escape(subscription.status.value)}\n"
        "Срок: без почасового ограничения\n"
        f"Устройства: {subscription.device_limit}\n"
        f"Конфигурация: {health_summary(subscription)}",
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
            "Не удалось обновить конфигурацию. Мы сохранили диагностику — "
            "попробуйте ещё раз через минуту.",
            reply_markup=home_keyboard(),
        )
        return

    if result.is_healthy:
        endpoint_names = "\n".join(
            f"• {html.escape(endpoint.name)}" for endpoint in result.endpoints[:6]
        )
        await callback.message.edit_text(
            "<b>Конфигурация обновлена</b>\n\n"
            f"Доступно серверов: {result.endpoint_count}\n"
            f"Ответ панели: {result.response_ms} мс\n\n"
            f"{endpoint_names}\n\n"
            "Теперь нажмите «Подключить устройство». Если приложение было открыто, "
            "обновите подписку внутри него.",
            reply_markup=subscription_keyboard(subscription),
            parse_mode=ParseMode.HTML,
        )
        return

    await callback.message.edit_text(
        "<b>Серверы пока не готовы</b>\n\n"
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
            "Не удалось выпустить новую ссылку. Попробуйте через минуту.",
            reply_markup=home_keyboard(),
        )
        return

    if not result.is_healthy:
        await callback.message.edit_text(
            "<b>Новая ссылка создана, но серверы ещё не готовы</b>\n\n"
            f"{html.escape(result.message)}.",
            reply_markup=subscription_keyboard(subscription),
            parse_mode=ParseMode.HTML,
        )
        return

    await callback.message.edit_text(
        "<b>Новая ссылка готова</b>\n\n"
        f"Проверено серверов: {result.endpoint_count}.\n"
        "Старая ссылка отключена. Нажмите «Подключить устройство» и импортируйте "
        "профиль заново — это очищает старый кэш приложения.",
        reply_markup=subscription_keyboard(subscription),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "help:connect")
async def connection_help(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "<b>Подключение занимает меньше минуты</b>\n\n"
            "1. Установите Hiddify или v2rayTun.\n"
            "2. Откройте «Моя подписка».\n"
            "3. Нажмите «Подключить устройство».\n"
            "4. Выберите приложение и подтвердите импорт.",
            reply_markup=home_keyboard(),
            parse_mode=ParseMode.HTML,
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
