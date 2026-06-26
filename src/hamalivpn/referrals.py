import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from .config import get_settings
from .db import SessionFactory
from .models import Customer, WithdrawalRequest, WithdrawalStatus

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()

REFERRAL_RATE = 0.30
MIN_WITHDRAWAL = 1000
METHODS = {"sbp": "СБП", "card": "Банковская карта", "usdt": "USDT (TRC20)"}
REQ_HINT = {"sbp": "номер телефона + банк", "card": "номер карты", "usdt": "адрес кошелька TRC20"}


class SetupState(StatesGroup):
    waiting_requisites = State()


def _kb(can_withdraw: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🏦 Настроить реквизиты", callback_data="ref:setup"))
    if can_withdraw:
        b.row(InlineKeyboardButton(text="💸 Вывести средства", callback_data="ref:withdraw"))
    b.row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    return b.as_markup()


async def _render(bot, tg_id: int):
    async with SessionFactory() as s:
        customer = await s.scalar(select(Customer).where(Customer.telegram_id == tg_id))
        balance = customer.balance_rub if customer else 0
        method = customer.withdrawal_method if customer else None
        requisites = customer.withdrawal_requisites if customer else None
        ref_count = 0
        if customer:
            rows = (await s.execute(select(Customer.id).where(Customer.referrer_id == customer.id))).all()
            ref_count = len(rows)
    info = await bot.me()
    link = f"https://t.me/{info.username}?start=ref_{tg_id}"
    rate = int(REFERRAL_RATE * 100)
    text = (
        "👥 <b>Партнёрская программа</b>\n\n"
        "💼 Зарабатывай вместе с нами!\n"
        f"1) Приглашай друзей по своей ссылке и получай <b>{rate}%</b> с каждого их пополнения.\n"
        "2) Выводи заработок удобным способом.\n\n"
        "🔗 Твоя ссылка:\n"
        f"<code>{link}</code>\n\n"
        "📊 <b>Статистика:</b>\n"
        f"👤 Приглашено: <b>{ref_count}</b>\n"
        f"💰 Баланс: <b>{balance} ₽</b>\n"
        f"🏦 Способ вывода: {METHODS.get(method, 'не задан')}\n"
        f"🧾 Реквизиты: {requisites or 'не указаны'}\n\n"
        f"💸 Вывод доступен от <b>{MIN_WITHDRAWAL} ₽</b>. Ставка: <b>{rate}%</b>\n"
        f"Пример: платёж 540 ₽ → бонус {int(540 * REFERRAL_RATE)} ₽"
    )
    return text, _kb(balance >= MIN_WITHDRAWAL)


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
    text, kb = await _render(callback.message.bot, callback.from_user.id)
    await _show(callback, text, kb)


@router.callback_query(F.data == "ref:setup")
async def setup_method(callback: CallbackQuery) -> None:
    await callback.answer()
    b = InlineKeyboardBuilder()
    for code, name in METHODS.items():
        b.row(InlineKeyboardButton(text=name, callback_data=f"ref:method:{code}"))
    b.row(InlineKeyboardButton(text="← Назад", callback_data="menu:referrals"))
    await _show(callback, "🏦 <b>Способ вывода</b>\n\nВыберите, куда выводить заработок:", b.as_markup())


@router.callback_query(F.data.startswith("ref:method:"))
async def setup_requisites_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    method = callback.data.split(":")[2]
    await state.set_state(SetupState.waiting_requisites)
    await state.update_data(method=method)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="← Отмена", callback_data="menu:referrals"))
    await callback.message.edit_text(
        f"🧾 <b>{METHODS.get(method, method)}</b>\n\n"
        f"Отправьте {REQ_HINT.get(method, 'реквизиты')} одним сообщением:",
        reply_markup=b.as_markup(), parse_mode="HTML",
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
        customer = await s.scalar(select(Customer).where(Customer.telegram_id == message.from_user.id))
        if customer:
            customer.withdrawal_method = method
            customer.withdrawal_requisites = requisites
            await s.commit()
    await message.answer("✅ Реквизиты сохранены.")
    text, kb = await _render(message.bot, message.from_user.id)
    await _show(message, text, kb)


@router.callback_query(F.data == "ref:withdraw")
async def withdraw(callback: CallbackQuery) -> None:
    async with SessionFactory() as s:
        customer = await s.scalar(select(Customer).where(Customer.telegram_id == callback.from_user.id))
        if not customer or customer.balance_rub < MIN_WITHDRAWAL:
            await callback.answer(f"Минимум для вывода — {MIN_WITHDRAWAL} ₽.", show_alert=True)
            return
        if not customer.withdrawal_requisites:
            await callback.answer("Сначала настройте реквизиты.", show_alert=True)
            return
        amount = customer.balance_rub
        customer.balance_rub = 0
        req = WithdrawalRequest(
            customer_id=customer.id, amount=amount,
            requisites=f"{METHODS.get(customer.withdrawal_method, '')}: {customer.withdrawal_requisites}",
        )
        s.add(req)
        await s.commit()
        req_id, requisites = req.id, req.requisites
    await callback.answer("Заявка отправлена!")
    for admin_id in settings.admin_ids:
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="✅ Выплачено", callback_data=f"wd:ok:{req_id}"),
            InlineKeyboardButton(text="❌ Отказать", callback_data=f"wd:no:{req_id}"),
        )
        try:
            await callback.message.bot.send_message(
                admin_id,
                f"💸 <b>Заявка на вывод #{req_id}</b>\n"
                f"От: @{callback.from_user.username} ({callback.from_user.id})\n"
                f"Сумма: <b>{amount} ₽</b>\n"
                f"Реквизиты: <code>{requisites}</code>",
                reply_markup=b.as_markup(), parse_mode="HTML",
            )
        except Exception:
            pass
    b = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    await callback.message.edit_text(
        f"✅ <b>Заявка на {amount} ₽ отправлена!</b>\n\nОбычно выплата в течение 24 часов.",
        reply_markup=b.as_markup(), parse_mode="HTML",
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
