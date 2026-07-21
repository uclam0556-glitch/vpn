import html
import logging
from datetime import UTC, datetime
from urllib.parse import quote

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import distinct, func, select

from .config import get_settings
from .db import SessionFactory
from .models import (
    BalanceTransaction,
    Customer,
    Subscription,
    SubscriptionStatus,
    WithdrawalRequest,
    WithdrawalStatus,
)
from .public_urls import public_connect_base_url
from .telegram_ui import inline_button

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()

REFERRAL_RATE = 0.30
MIN_WITHDRAWAL = 1000
METHODS = {"sbp": "СБП", "card": "Банковская карта", "usdt": "USDT (TRC20)"}
REQ_HINT = {"sbp": "номер телефона + банк", "card": "номер карты", "usdt": "адрес кошелька TRC20"}


class SetupState(StatesGroup):
    waiting_requisites = State()


def _kb(can_withdraw: bool, referral_link: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        inline_button(
            "Открыть бонусы в Mini App",
            icon="rocket",
            style="primary",
            web_app=WebAppInfo(url=f"{public_connect_base_url(settings)}/tma/?screen=bonus"),
        )
    )
    share_url = (
        "https://t.me/share/url?url="
        f"{quote(referral_link, safe='')}&text="
        f"{quote('Попробуй HamaliVPN — по моей ссылке тебя ждёт бонус.', safe='')}"
    )
    b.row(inline_button("Поделиться ссылкой", icon="link", url=share_url))
    b.row(inline_button("Реквизиты", icon="bank", callback_data="ref:setup"))
    if can_withdraw:
        b.row(inline_button("Вывести", icon="money", style="success", callback_data="ref:withdraw"))
    b.row(
        inline_button("Обновить", icon="refresh", callback_data="menu:referrals"),
        inline_button("Назад", icon="home", callback_data="menu:home"),
    )
    return b.as_markup()


def _money(value: int | None) -> str:
    return f"{int(value or 0):,}".replace(",", " ") + " ₽"


def _mask_requisites(value: str | None) -> str:
    if not value:
        return "не указаны"
    value = value.strip()
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}••••{value[-4:]}"


async def _ensure_customer(
    tg_id: int, username: str | None = None, full_name: str = ""
) -> Customer:
    async with SessionFactory() as s:
        customer = await s.scalar(select(Customer).where(Customer.telegram_id == tg_id))
        if customer is None:
            customer = Customer(
                telegram_id=tg_id,
                telegram_username=username,
                full_name=full_name or "",
            )
            s.add(customer)
            await s.commit()
            await s.refresh(customer)
        else:
            changed = False
            if username and customer.telegram_username != username:
                customer.telegram_username = username
                changed = True
            if full_name and customer.full_name != full_name:
                customer.full_name = full_name
                changed = True
            if changed:
                await s.commit()
                await s.refresh(customer)
        return customer


async def _render(bot, tg_id: int, username: str | None = None, full_name: str = ""):
    customer = await _ensure_customer(tg_id, username=username, full_name=full_name)
    async with SessionFactory() as s:
        customer = await s.scalar(select(Customer).where(Customer.telegram_id == tg_id))
        if customer is None:
            raise RuntimeError("Customer was not created")

        balance = customer.balance_rub
        method = customer.withdrawal_method
        requisites = customer.withdrawal_requisites

        referred_ids = [
            row[0]
            for row in (
                await s.execute(select(Customer.id).where(Customer.referrer_id == customer.id))
            ).all()
        ]
        ref_count = len(referred_ids)
        active_count = 0
        if referred_ids:
            active_count = int(
                await s.scalar(
                    select(func.count(distinct(Subscription.customer_id))).where(
                        Subscription.customer_id.in_(referred_ids),
                        Subscription.status == SubscriptionStatus.active,
                        Subscription.expires_at > datetime.now(UTC),
                    )
                )
                or 0
            )

        earned_total = int(
            await s.scalar(
                select(func.coalesce(func.sum(BalanceTransaction.amount), 0)).where(
                    BalanceTransaction.customer_id == customer.id,
                    BalanceTransaction.type == "referral_bonus",
                )
            )
            or 0
        )

    info = await bot.me()
    link = f"https://t.me/{info.username}?start=ref_{tg_id}"
    rate = int(REFERRAL_RATE * 100)
    text = (
        "✨ <b>Бонусы HamaliVPN</b>\n\n"
        f"💰 Доступно: <b>{_money(balance)}</b>\n"
        f"👥 Приглашено: <b>{ref_count}</b> · активных: <b>{active_count}</b>\n"
        f"🎁 Начислено: <b>{_money(earned_total)}</b>\n\n"
        f"Получайте <b>{rate}%</b> с оплат друзей.\n"
        f"Вывод — от <b>{_money(MIN_WITHDRAWAL)}</b>.\n\n"
        "🔗 <b>Ваша ссылка</b>\n"
        f"<code>{link}</code>\n\n"
        f"⚙️ {METHODS.get(method, 'Реквизиты не заданы')} · "
        f"<code>{html.escape(_mask_requisites(requisites))}</code>"
    )
    return text, _kb(balance >= MIN_WITHDRAWAL, link)


async def _show(message_or_cb, text: str, kb: InlineKeyboardMarkup) -> None:
    msg = message_or_cb.message if isinstance(message_or_cb, CallbackQuery) else message_or_cb
    if isinstance(message_or_cb, CallbackQuery) and msg.photo:
        await msg.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
    elif isinstance(message_or_cb, CallbackQuery):
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "menu:referrals")
async def show_referrals_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    text, kb = await _render(
        callback.message.bot,
        callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name or "",
    )
    await _show(callback, text, kb)


@router.callback_query(F.data == "ref:link")
async def send_referral_link(callback: CallbackQuery) -> None:
    await callback.answer()
    await _ensure_customer(
        callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name or "",
    )
    info = await callback.message.bot.me()
    link = f"https://t.me/{info.username}?start=ref_{callback.from_user.id}"
    await callback.message.answer(
        "🔗 <b>Ваша реферальная ссылка</b>\n\n"
        f"<code>{link}</code>\n\n"
        "Отправьте её друзьям. Когда появятся подтверждённые оплаты — статистика обновится автоматически.",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "ref:setup")
async def setup_method(callback: CallbackQuery) -> None:
    await callback.answer()
    b = InlineKeyboardBuilder()
    for code, name in METHODS.items():
        b.row(inline_button(name, icon="bank", callback_data=f"ref:method:{code}"))
    b.row(inline_button("Назад", icon="home", callback_data="menu:referrals"))
    await _show(
        callback, "🏦 <b>Способ вывода</b>\n\nВыберите, куда выводить бонусы:", b.as_markup()
    )


@router.callback_query(F.data.startswith("ref:method:"))
async def setup_requisites_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    method = callback.data.split(":")[2]
    await state.set_state(SetupState.waiting_requisites)
    await state.update_data(method=method)
    b = InlineKeyboardBuilder()
    b.row(inline_button("Отмена", icon="home", callback_data="menu:referrals"))
    await callback.message.edit_text(
        f"🧾 <b>{METHODS.get(method, method)}</b>\n\n"
        f"Отправьте {REQ_HINT.get(method, 'реквизиты')} одним сообщением:",
        reply_markup=b.as_markup(),
        parse_mode="HTML",
    )


@router.message(SetupState.waiting_requisites)
async def save_requisites(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    method = data.get("method")
    requisites = (message.text or "").strip()[:255]
    await state.clear()
    if not requisites:
        await message.answer("Реквизиты пустые. Откройте «Бонусы» и попробуйте снова.")
        return
    async with SessionFactory() as s:
        customer = await s.scalar(
            select(Customer).where(Customer.telegram_id == message.from_user.id)
        )
        if customer:
            customer.withdrawal_method = method
            customer.withdrawal_requisites = requisites
            await s.commit()
    await message.answer("✅ Реквизиты сохранены.")
    text, kb = await _render(
        message.bot,
        message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name or "",
    )
    await _show(message, text, kb)


@router.callback_query(F.data == "ref:withdraw")
async def withdraw(callback: CallbackQuery) -> None:
    async with SessionFactory() as s:
        customer = await s.scalar(
            select(Customer).where(Customer.telegram_id == callback.from_user.id)
        )
        if not customer or customer.balance_rub < MIN_WITHDRAWAL:
            await callback.answer(f"Минимум для вывода — {MIN_WITHDRAWAL} ₽.", show_alert=True)
            return
        if not customer.withdrawal_requisites:
            await callback.answer("Сначала настройте реквизиты.", show_alert=True)
            return
        amount = customer.balance_rub
        customer.balance_rub = 0
        req = WithdrawalRequest(
            customer_id=customer.id,
            amount=amount,
            requisites=f"{METHODS.get(customer.withdrawal_method, '')}: {customer.withdrawal_requisites}",
        )
        s.add(req)
        await s.commit()
        req_id, requisites = req.id, req.requisites
    await callback.answer("Заявка отправлена!")
    for admin_id in settings.admin_ids:
        b = InlineKeyboardBuilder()
        b.row(
            inline_button(
                "Выплачено", icon="check", style="success", callback_data=f"wd:ok:{req_id}"
            ),
            inline_button(
                "Отказать", icon="blocked", style="danger", callback_data=f"wd:no:{req_id}"
            ),
        )
        try:
            await callback.message.bot.send_message(
                admin_id,
                f"💸 <b>Заявка на вывод #{req_id}</b>\n"
                f"От: @{callback.from_user.username} ({callback.from_user.id})\n"
                f"Сумма: <b>{amount} ₽</b>\n"
                f"Реквизиты: <code>{requisites}</code>",
                reply_markup=b.as_markup(),
                parse_mode="HTML",
            )
        except Exception:
            pass
    b = InlineKeyboardBuilder().row(
        inline_button("Главная", icon="home", callback_data="menu:home")
    )
    await callback.message.edit_text(
        f"✅ <b>Заявка на {amount} ₽ отправлена!</b>\n\nОбычно выплата в течение 24 часов.",
        reply_markup=b.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("wd:"))
async def admin_withdraw_decision(callback: CallbackQuery) -> None:
    if callback.from_user.id not in settings.admin_ids:
        await callback.answer("Нет прав.", show_alert=True)
        return
    _, action, rid = callback.data.split(":")
    async with SessionFactory() as s:
        req = await s.get(WithdrawalRequest, int(rid))
        if not req or req.status != WithdrawalStatus.pending:
            await callback.answer("Заявка уже обработана.", show_alert=True)
            return
        customer = await s.get(Customer, req.customer_id)
        if action == "ok":
            req.status = WithdrawalStatus.approved
            user_msg = f"✅ Вывод <b>{req.amount} ₽</b> выплачен."
            admin_note = f"✅ Заявка #{rid} — выплачено."
        else:
            req.status = WithdrawalStatus.rejected
            if customer:
                customer.balance_rub += req.amount
            user_msg = f"❌ Заявка на вывод <b>{req.amount} ₽</b> отклонена. Средства возвращены на баланс."
            admin_note = f"❌ Заявка #{rid} — отклонена, средства возвращены."
        await s.commit()
        user_tg = customer.telegram_id if customer else None
    await callback.answer("Готово")
    await callback.message.edit_text(admin_note, parse_mode="HTML")
    if user_tg:
        try:
            await callback.message.bot.send_message(user_tg, user_msg, parse_mode="HTML")
        except Exception:
            pass
