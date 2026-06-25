import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from .config import get_settings
from .db import SessionFactory
from .models import Customer, WithdrawalRequest

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()

class WithdrawState(StatesGroup):
    waiting_for_requisites = State()

def referrals_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 Купить подписку", callback_data="menu:buy"))
    builder.row(InlineKeyboardButton(text="💸 Вывести средства", callback_data="referrals:withdraw"))
    builder.row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    return builder.as_markup()

@router.callback_query(F.data == "menu:referrals")
async def show_referrals_menu(callback: CallbackQuery) -> None:
    async with SessionFactory() as session:
        stmt = select(Customer).where(Customer.telegram_id == callback.from_user.id)
        customer = await session.scalar(stmt)
        
        balance = customer.balance_rub if customer else 0
        
        ref_count = 0
        if customer:
            ref_stmt = select(Customer).where(Customer.referrer_id == customer.id)
            ref_res = await session.execute(ref_stmt)
            ref_count = len(ref_res.scalars().all())

    bot_info = await callback.message.bot.me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{callback.from_user.id}"

    text = (
        "🎁 <b>Реферальная система</b>\n\n"
        f"Приглашайте друзей и получайте <b>10%</b> от суммы их покупок на ваш баланс!\n\n"
        f"Ваша ссылка для приглашения:\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено друзей: <b>{ref_count}</b>\n"
        f"💰 Ваш баланс: <b>{balance} ₽</b>\n\n"
        "<i>Вы можете вывести средства на карту РФ или использовать их для оплаты подписки.</i>"
    )

    kb = referrals_keyboard()
    if callback.message.photo:
        await callback.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
    else:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "referrals:withdraw")
async def start_withdraw(callback: CallbackQuery, state: FSMContext) -> None:
    async with SessionFactory() as session:
        stmt = select(Customer).where(Customer.telegram_id == callback.from_user.id)
        customer = await session.scalar(stmt)
        balance = customer.balance_rub if customer else 0

    if balance < 300:
        await callback.answer(f"Минимальная сумма вывода 300 ₽. У вас {balance} ₽.", show_alert=True)
        return

    await state.set_state(WithdrawState.waiting_for_requisites)
    await state.update_data(balance=balance)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🏠 Отмена", callback_data="menu:referrals"))
    
    await callback.message.edit_text(
        f"💸 <b>Вывод средств</b>\n\n"
        f"Доступно для вывода: <b>{balance} ₽</b>\n\n"
        f"Напишите реквизиты для перевода (Номер карты или номер телефона СБП + название банка):",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )

@router.message(WithdrawState.waiting_for_requisites)
async def process_withdraw_requisites(message: Message, state: FSMContext) -> None:
    requisites = message.text
    data = await state.get_data()
    balance = data["balance"]
    
    async with SessionFactory() as session:
        stmt = select(Customer).where(Customer.telegram_id == message.from_user.id)
        customer = await session.scalar(stmt)
        
        if customer.balance_rub < balance:
            await message.answer("Ошибка: недостаточно средств.")
            await state.clear()
            return
            
        # Deduct balance
        customer.balance_rub -= balance
        
        # Create request
        req = WithdrawalRequest(
            customer_id=customer.id,
            amount=balance,
            requisites=requisites
        )
        session.add(req)
        await session.commit()
        req_id = req.id

    await state.clear()
    
    # Notify admins
    for admin_id in settings.admin_ids:
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="✅ Выплачено", callback_data=f"admin_approve_wd:{req_id}"),
            InlineKeyboardButton(text="❌ Отказать", callback_data=f"admin_reject_wd:{req_id}")
        )
        try:
            await message.bot.send_message(
                admin_id,
                f"💸 <b>Новая заявка на вывод!</b>\n"
                f"Пользователь: @{message.from_user.username} ({message.from_user.id})\n"
                f"Сумма: {balance} ₽\n"
                f"Реквизиты:\n<code>{requisites}</code>",
                reply_markup=kb.as_markup(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🏠 Главная", callback_data="menu:home"))
    await message.answer(
        f"✅ <b>Заявка на {balance} ₽ отправлена!</b>\n\nОжидайте поступления средств.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
