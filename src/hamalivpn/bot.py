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
    TrialAlreadyUsedError,
    get_latest_subscription,
    issue_trial,
)

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()


def home_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Получить тест на 90 минут", callback_data="trial:create")
    builder.button(text="Моя подписка", callback_data="subscription:show")
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
            text="Поддержка",
            url=f"https://t.me/{settings.support_username.lstrip('@')}",
        )
    )
    return builder.as_markup()


@router.message(CommandStart())
async def start(message: Message) -> None:
    name = html.escape(message.from_user.first_name if message.from_user else "друг")
    text = (
        f"<b>HamaliVpn</b>\n\n"
        f"{name}, здесь можно получить доступ, подключить приложение и управлять "
        "подпиской без ручной настройки.\n\n"
        "Для первого теста доступно 90 минут, 30 ГБ и одно устройство."
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
    except TrialAlreadyUsedError:
        await callback.message.edit_text(
            "Тестовый период уже использован. Откройте «Моя подписка», "
            "если доступ ещё действует.",
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
    await callback.message.edit_text(
        "<b>Тестовый доступ готов</b>\n\n"
        f"Срок: до {result.expires_at:%d.%m %H:%M} UTC\n"
        f"Трафик: {result.traffic_limit_gb} ГБ\n"
        f"Устройства: {result.device_limit}\n\n"
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
    if subscription is None:
        await callback.message.edit_text(
            "У вас пока нет подписки.",
            reply_markup=home_keyboard(),
        )
        return
    await callback.message.edit_text(
        "<b>Ваша подписка</b>\n\n"
        f"Статус: {html.escape(subscription.status.value)}\n"
        f"Действует до: {subscription.expires_at:%d.%m.%Y %H:%M} UTC\n"
        f"Устройства: {subscription.device_limit}",
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
