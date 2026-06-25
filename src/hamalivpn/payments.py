import base64
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import aiohttp
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from .config import get_settings
from .db import SessionFactory
from .models import (
    Customer,
    PaymentStatus,
    PaymentTransaction,
    Subscription,
    SubscriptionStatus,
    as_utc,
)
from .remnawave import RemnawaveError, make_remnawave_gateway
from .services import get_latest_subscription, issue_trial

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()

PLANS = {
    "1_month": {"name": "1 Месяц", "price": 100, "days": 30},
    "3_months": {"name": "3 Месяца", "price": 250, "days": 90},
    "6_months": {"name": "6 Месяцев", "price": 450, "days": 180},
}

def buy_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, plan in PLANS.items():
        builder.row(
            InlineKeyboardButton(
                text=f"💎 {plan['name']} — {plan['price']} ₽",
                callback_data=f"buy:{code}",
            )
        )
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    return builder.as_markup()

@router.callback_query(F.data == "menu:buy")
async def show_buy_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    text = (
        "💎 <b>Премиум доступ HamaliVPN</b>\n\n"
        "Выбери тариф для оплаты. Поддерживаются банковские карты РФ, СБП и криптовалюта.\n"
        "Подписка выдаётся автоматически после зачисления средств!"
    )
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=buy_keyboard())
    else:
        await callback.message.edit_text(text, reply_markup=buy_keyboard())

async def create_cryptomus_payment(transaction_id: str, amount: int) -> str | None:
    api_key = settings.cryptomus_api_key.get_secret_value()
    merchant_id = settings.cryptomus_merchant_id
    if not api_key or not merchant_id:
        return None

    payload = {
        "amount": str(amount),
        "currency": "RUB",
        "order_id": transaction_id,
        "url_return": f"https://t.me/{settings.bot_username.lstrip('@')}",
        "url_callback": f"{settings.public_base_url.rstrip('/')}/api/webhooks/cryptomus",
        "is_payment_multiple": False,
        "lifetime": "3600"
    }
    
    json_payload = json.dumps(payload).encode("utf-8")
    base64_payload = base64.b64encode(json_payload).decode("utf-8")
    sign = hashlib.md5((base64_payload + api_key).encode("utf-8")).hexdigest()

    headers = {
        "merchant": merchant_id,
        "sign": sign,
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.cryptomus.com/v1/payment", json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["result"]["url"]
            else:
                logger.error(f"Cryptomus error: {await resp.text()}")
                return None

@router.callback_query(F.data.startswith("buy:"))
async def process_buy(callback: CallbackQuery) -> None:
    code = callback.data.split(":")[1]
    plan = PLANS.get(code)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await callback.message.edit_text("⏳ Генерируем ссылку на оплату...")

    async with SessionFactory() as session:
        stmt = select(Customer).where(Customer.telegram_id == callback.from_user.id)
        customer = await session.scalar(stmt)
        if not customer:
            customer = Customer(
                telegram_id=callback.from_user.id,
                telegram_username=callback.from_user.username,
                full_name=callback.from_user.full_name or ""
            )
            session.add(customer)
            await session.commit()
            
        transaction = PaymentTransaction(
            customer_id=customer.id,
            amount=plan["price"],
            currency="RUB",
            provider="cryptomus" if settings.cryptomus_api_key.get_secret_value() else "manual",
            payload=code
        )
        session.add(transaction)
        await session.commit()
        tx_id = transaction.id

    if settings.cryptomus_api_key.get_secret_value():
        url = await create_cryptomus_payment(tx_id, plan["price"])
        if url:
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="💳 Оплатить", url=url))
            kb.row(InlineKeyboardButton(text="🏠 Отмена", callback_data="menu:home"))
            await callback.message.edit_text(
                f"Оплата тарифа <b>{plan['name']}</b> ({plan['price']} ₽)\n\n"
                f"Нажмите кнопку ниже для перехода к оплате (СБП, Карты, Криптовалюта).",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                "❌ Ошибка шлюза оплаты. Попробуйте позже.",
                reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="Назад", callback_data="menu:buy")).as_markup()
            )
    else:
        if not settings.manual_sbp_card:
            await callback.message.edit_text("Оплата временно недоступна. Админ не настроил реквизиты.")
            return

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"manual_paid:{tx_id}"))
        kb.row(InlineKeyboardButton(text="🏠 Отмена", callback_data="menu:home"))
        
        await callback.message.edit_text(
            f"Оплата тарифа <b>{plan['name']}</b> ({plan['price']} ₽)\n\n"
            f"Переведите <b>{plan['price']} ₽</b> по номеру:\n"
            f"<code>{settings.manual_sbp_card}</code> ({settings.manual_sbp_bank})\n\n"
            f"После перевода нажмите кнопку <b>✅ Я оплатил</b>.",
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )

@router.callback_query(F.data.startswith("manual_paid:"))
async def process_manual_paid(callback: CallbackQuery) -> None:
    tx_id = callback.data.split(":")[1]
    
    for admin_id in settings.admin_ids:
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_approve_tx:{tx_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_tx:{tx_id}")
        )
        try:
            await callback.bot.send_message(
                admin_id,
                f"📝 <b>Новый платеж (ручной)!</b>\n"
                f"Пользователь: @{callback.from_user.username} ({callback.from_user.id})\n"
                f"Транзакция: {tx_id}",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await callback.message.edit_text(
        "✅ Заявка отправлена администратору.\nПодписка будет выдана сразу после проверки (обычно 5-10 минут).",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home")).as_markup()
    )

async def fulfill_payment(session, bot, transaction: PaymentTransaction):
    transaction.status = PaymentStatus.paid
    
    plan_code = transaction.payload
    plan = PLANS.get(plan_code)
    if not plan:
        return

    customer = await session.get(Customer, transaction.customer_id)
    if customer.referrer_id:
        referrer = await session.get(Customer, customer.referrer_id)
        if referrer:
            bonus = int(plan["price"] * 0.1)
            referrer.balance_rub += bonus
            try:
                await bot.send_message(
                    referrer.telegram_id,
                    f"🎁 Ваш реферал только что оплатил подписку!\n"
                    f"Вам начислено <b>{bonus} ₽</b> на баланс.",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    gateway = make_remnawave_gateway(settings)
    subscription = await get_latest_subscription(session, customer.telegram_id)
    provisioned_now = False

    if not subscription:
        try:
            sub_result = await issue_trial(
                session,
                gateway,
                settings,
                telegram_id=customer.telegram_id,
                telegram_username=customer.telegram_username,
                full_name=customer.full_name,
            )
            subscription = await session.get(Subscription, sub_result.subscription_id)
            provisioned_now = True
        except RemnawaveError:
            logger.exception("Failed to provision subscription")

    if subscription:
        now = datetime.now(UTC)
        if provisioned_now:
            base = now
        else:
            current_expiry = as_utc(subscription.expires_at) if subscription.expires_at else now
            base = max(current_expiry, now)
        new_expires = base + timedelta(days=plan["days"])

        subscription.expires_at = new_expires
        subscription.status = SubscriptionStatus.active
        await session.commit()

        if subscription.remnawave_uuid:
            try:
                remote = await gateway.update_user_access(
                    user_uuid=subscription.remnawave_uuid,
                    expires_at=new_expires,
                    device_limit=subscription.device_limit,
                    traffic_limit_bytes=subscription.traffic_limit_gb * 1024**3,
                    squads=settings.squad_uuids,
                )
                subscription.subscription_url = remote.subscription_url
                subscription.remnawave_short_uuid = remote.short_uuid
                await session.commit()
            except Exception:
                logger.exception("Failed to update remnawave user expiration")

        try:
            await bot.send_message(
                customer.telegram_id,
                f"✅ <b>Оплата успешно получена!</b>\n\n"
                f"Вы приобрели тариф <b>{plan['name']}</b>.\n"
                f"Подписка продлена до {subscription.expires_at.strftime('%d.%m.%Y')}.\n\n"
                "Перейдите в «👤 Моя подписка», чтобы посмотреть статус.",
                parse_mode="HTML"
            )
        except Exception:
            pass
