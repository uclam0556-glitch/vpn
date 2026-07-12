import hashlib
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from .config import get_settings
from .db import SessionFactory
from .device_limits import prune_hwid_devices_to_limit
from .device_slots import sync_subscription_device_slots
from .models import (
    BalanceTransaction,
    Customer,
    PaymentStatus,
    PaymentTransaction,
    Subscription,
    SubscriptionStatus,
    as_utc,
)
from .remnawave import RemnawaveError, RemnawaveNotFoundError, make_remnawave_gateway
from .services import get_latest_subscription, issue_trial

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()

PLANS = {
    "1_month": {"name": "1 месяц · 1 устройство", "price": 150, "days": 30, "devices": 1},
    "2_months": {"name": "2 месяца · 3 устройства", "price": 300, "days": 60, "devices": 3},
    "3_months": {"name": "3 месяца · 5 устройств", "price": 450, "days": 90, "devices": 5},
    "6_months": {"name": "6 месяцев · 5 устройств", "price": 700, "days": 180, "devices": 5},
    "12_months": {"name": "12 месяцев · 5 устройств", "price": 1000, "days": 365, "devices": 5},
}

REFERRAL_RATE = 0.30  # доля пополнения, начисляемая пригласившему


def buy_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, plan in PLANS.items():
        builder.row(
            InlineKeyboardButton(
                text=f"💳 {plan['name']} — {plan['price']} ₽",
                callback_data=f"platega:{code}",
            )
        )
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    return builder.as_markup()


@router.callback_query(F.data == "menu:buy")
async def show_buy_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    text = (
        "💳 <b>Оформление подписки</b>\n\n"
        "Выберите тариф и оплатите картой или через СБП.\n"
        "Доступ активируется автоматически сразу после оплаты."
    )
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=buy_keyboard())
    else:
        await callback.message.edit_text(text, reply_markup=buy_keyboard())


class PlategaPaymentError(RuntimeError):
    pass


def _platega_is_configured() -> bool:
    return bool(
        settings.platega_merchant_id.strip()
        and settings.platega_api_key.get_secret_value().strip()
    )


async def create_platega_link(
    *,
    order_id: str,
    amount: int,
    description: str,
    telegram_id: int,
    username: str | None,
) -> dict:
    merchant_id = settings.platega_merchant_id.strip()
    api_key = settings.platega_api_key.get_secret_value().strip()
    if not merchant_id or not api_key:
        raise PlategaPaymentError("Platega is not configured")

    base_url = settings.platega_api_base_url.rstrip("/")
    bot_url = f"https://t.me/{settings.bot_username}"
    payload = {
        "paymentDetails": {"amount": int(amount), "currency": "RUB"},
        "description": description,
        "return": bot_url,
        "failedUrl": bot_url,
        "payload": order_id,
        "metadata": {
            "userId": str(telegram_id),
            "userName": f"@{username}" if username else str(telegram_id),
        },
    }
    headers = {"X-MerchantId": merchant_id, "X-Secret": api_key}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{base_url}/v2/transaction/process",
                json=payload,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise PlategaPaymentError(f"Platega request failed: {exc}") from exc

    if response.is_error:
        raise PlategaPaymentError(
            f"Platega returned {response.status_code}: {response.text[:500]}"
        )
    data = response.json()
    transaction_id = data.get("transactionId") or data.get("id")
    payment_url = data.get("url") or data.get("redirect")
    if not transaction_id or not payment_url:
        raise PlategaPaymentError("Platega response does not contain payment URL")
    data["transactionId"] = transaction_id
    data["url"] = payment_url
    return data


@router.callback_query(F.data.startswith("platega:"))
async def process_platega_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    code = callback.data.split(":")[1]
    plan = PLANS.get(code)
    if not plan:
        return
    if callback.message is None:
        return
    if not _platega_is_configured():
        await callback.message.answer("Оплата временно недоступна. Напишите в поддержку.")
        return

    async with SessionFactory() as session:
        customer = (
            await session.execute(
                select(Customer).where(Customer.telegram_id == callback.from_user.id)
            )
        ).scalars().first()
        if not customer:
            customer = Customer(
                telegram_id=callback.from_user.id,
                telegram_username=callback.from_user.username,
                full_name=callback.from_user.full_name or "",
            )
            session.add(customer)
            await session.flush()
        tx = PaymentTransaction(
            customer_id=customer.id,
            amount=plan["price"],
            currency="RUB",
            provider="platega",
            payload=code,
            status=PaymentStatus.pending,
        )
        session.add(tx)
        await session.commit()
        order_id = tx.id

    try:
        payment = await create_platega_link(
            order_id=order_id,
            amount=plan["price"],
            description=f"HamaliVPN · {plan['name']}",
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
        )
    except PlategaPaymentError:
        logger.exception("Could not create Platega payment")
        async with SessionFactory() as session:
            tx = await session.get(PaymentTransaction, order_id)
            if tx:
                tx.status = PaymentStatus.cancelled
                await session.commit()
        await callback.message.answer(
            "Не получилось создать платёжную ссылку. Напишите в поддержку — быстро поможем."
        )
        return

    async with SessionFactory() as session:
        tx = await session.get(PaymentTransaction, order_id)
        if tx:
            tx.external_id = payment["transactionId"]
            await session.commit()

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"💳 Оплатить {plan['price']} ₽", url=payment["url"]))
    kb.row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    text = (
        f"💳 <b>Оплата тарифа {plan['name']}</b>\n\n"
        f"Сумма: <b>{plan['price']} ₽</b>\n"
        "После оплаты доступ активируется автоматически.\n\n"
        "Нажмите кнопку ниже и завершите оплату."
    )
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb.as_markup(), parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


FK_PAY_URL = "https://pay.freekassa.ru/"


def freekassa_link(order_id: str, amount: int) -> str | None:
    merchant = os.getenv("FREEKASSA_MERCHANT_ID", "")
    secret1 = os.getenv("FREEKASSA_SECRET1", "")
    if not merchant or not secret1:
        return None
    sign = hashlib.md5(f"{merchant}:{amount}:{secret1}:RUB:{order_id}".encode()).hexdigest()
    return FK_PAY_URL + "?" + urlencode(
        {"m": merchant, "oa": amount, "currency": "RUB", "o": order_id, "s": sign}
    )


@router.callback_query(F.data.startswith("fk:"))
async def process_fk_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    code = callback.data.split(":")[1]
    plan = PLANS.get(code)
    if not plan:
        return
    async with SessionFactory() as session:
        customer = (
            await session.execute(
                select(Customer).where(Customer.telegram_id == callback.from_user.id)
            )
        ).scalars().first()
        if not customer:
            customer = Customer(
                telegram_id=callback.from_user.id,
                telegram_username=callback.from_user.username,
                full_name=callback.from_user.full_name or "",
            )
            session.add(customer)
            await session.flush()
        tx = PaymentTransaction(
            customer_id=customer.id,
            amount=plan["price"],
            currency="RUB",
            provider="freekassa",
            payload=code,
            status=PaymentStatus.pending,
        )
        session.add(tx)
        await session.commit()
        order_id = tx.id

    link = freekassa_link(order_id, plan["price"])
    if not link:
        await callback.message.answer("Оплата картой временно недоступна. Напишите в поддержку.")
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"💳 Оплатить {plan['price']} ₽", url=link))
    kb.row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    text = (
        f"💳 <b>Оплата тарифа {plan['name']}</b>\n\n"
        f"Сумма: <b>{plan['price']} ₽</b>\n"
        "Способы: банковская карта, СБП.\n\n"
        "Нажмите кнопку ниже. После оплаты подписка активируется автоматически."
    )
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=text, reply_markup=kb.as_markup(), parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("buy:"))
async def process_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    code = callback.data.split(":")[1]
    plan = PLANS.get(code)
    if not plan:
        return

    # LabeledPrice is required for Telegram Stars, currency must be "XTR"
    prices = [LabeledPrice(label=plan["name"], amount=plan["price"])]

    await callback.message.answer_invoice(
        title=f"Подписка {plan['name']}",
        description="Безлимитный доступ к HamaliVPN на максимальной скорости.",
        payload=f"sub:{code}",
        provider_token="",  # Empty for Telegram Stars
        currency="XTR",
        prices=prices,
    )


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery) -> None:
    await pre_checkout_q.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    payment = message.successful_payment
    payload = payment.invoice_payload
    if not payload.startswith("sub:"):
        return

    code = payload.split(":")[1]
    plan = PLANS.get(code)
    if not plan:
        return

    gateway = make_remnawave_gateway(settings)

    async with SessionFactory() as session:
        # Give referral bonus back to referrer if exists.
        customer_stmt = select(Customer).where(Customer.telegram_id == message.from_user.id)
        customer_result = await session.execute(customer_stmt)
        customer = customer_result.scalars().first()

        if customer and customer.referrer_id:
            referrer = await session.get(Customer, customer.referrer_id)
            if referrer:
                bonus = int(plan["price"] * REFERRAL_RATE)
                referrer.balance_rub += bonus
                session.add(
                    BalanceTransaction(
                        customer_id=referrer.id,
                        amount=bonus,
                        type="referral_bonus",
                        description=f"Бонус за оплату реферала: {plan['name']}",
                    )
                )
                try:
                    await message.bot.send_message(
                        referrer.telegram_id,
                        f"🎁 Ваш реферал оплатил подписку!\n"
                        f"Начислено <b>{bonus} ₽</b> на партнёрский баланс.",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

        # Extend or create subscription
        subscription = await get_latest_subscription(session, message.from_user.id)
        provisioned_now = False
        if not subscription:
            # No subscription yet — provision a Remnawave user via the standard flow.
            try:
                sub_result = await issue_trial(
                    session,
                    gateway,
                    settings,
                    telegram_id=message.from_user.id,
                    telegram_username=message.from_user.username,
                    full_name=message.from_user.full_name,
                )
                subscription = await session.get(Subscription, sub_result.subscription_id)
                provisioned_now = True
            except RemnawaveError:
                logger.exception("Failed to provision subscription after payment")

        if subscription:
            now = datetime.now(UTC)
            if provisioned_now:
                # issue_trial grants the long-lived test window (test_access_days);
                # a paid plan must measure its term from "now", not from that sentinel.
                base = now
            else:
                # Top up an existing subscription: extend from the later of "now"
                # and the current expiry so active time is never lost.
                current_expiry = (
                    as_utc(subscription.expires_at) if subscription.expires_at else now
                )
                base = max(current_expiry, now)
            new_expires = base + timedelta(days=plan["days"])

            subscription.expires_at = new_expires
            subscription.status = SubscriptionStatus.active
            subscription.device_limit = plan.get("devices", subscription.device_limit)
            await session.commit()

            # Extend in Remnawave
            if subscription.remnawave_uuid:
                try:
                    try:
                        remote = await gateway.update_user_access(
                            user_uuid=subscription.remnawave_uuid,
                            expires_at=new_expires,
                            device_limit=subscription.device_limit,
                            traffic_limit_bytes=subscription.traffic_limit_gb * 1024**3,
                            squads=settings.squad_uuids,
                        )
                    except RemnawaveNotFoundError:
                        remote = await gateway.create_user(
                            username=f"tg_{message.from_user.id}_{secrets.token_hex(3)}",
                            telegram_id=message.from_user.id,
                            expires_at=new_expires,
                            device_limit=subscription.device_limit,
                            traffic_limit_bytes=subscription.traffic_limit_gb * 1024**3,
                            squads=settings.squad_uuids,
                            description=f"HamaliVPN payment recovery; local_subscription={subscription.id}",
                        )
                        subscription.remnawave_uuid = remote.uuid
                    subscription.subscription_url = remote.subscription_url
                    subscription.remnawave_short_uuid = remote.short_uuid
                    await prune_hwid_devices_to_limit(
                        user_uuid=subscription.remnawave_uuid,
                        device_limit=subscription.device_limit,
                        list_devices=gateway.list_hwid_devices,
                        delete_device=gateway.delete_hwid_device,
                    )
                    await sync_subscription_device_slots(
                        session,
                        gateway,
                        settings,
                        subscription,
                        actor="system:payment:telegram",
                    )
                    await session.commit()
                except Exception:
                    logger.exception("Failed to update remnawave user expiration")
        else:
            logger.error(
                "Payment succeeded, but subscription was not created",
                extra={"telegram_id": message.from_user.id, "payload": payload},
            )
            await message.answer(
                "✅ Оплата прошла, но доступ не выдался автоматически.\n"
                "Напишите в поддержку — мы вручную активируем подписку.",
                parse_mode="HTML",
            )
            return

    text = (
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"Вы приобрели тариф <b>{plan['name']}</b>.\n"
        f"Подписка продлена до {subscription.expires_at.strftime('%d.%m.%Y')}."
    )
    await message.answer(text, parse_mode="HTML")
