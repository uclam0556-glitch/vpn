import hashlib
import hmac
import json
import logging
import urllib.parse

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import get_session
from .models import (
    BalanceTransaction,
    Customer,
    PaymentStatus,
    PaymentTransaction,
    Subscription,
    WithdrawalRequest,
    WithdrawalStatus,
)
from .payments import PLANS, PlategaPaymentError, create_platega_link

tma_router = APIRouter(prefix="/api/tma", tags=["TMA"])
settings = get_settings()
logger = logging.getLogger(__name__)


def validate_init_data(init_data: str, bot_token: str) -> dict:
    if not init_data:
        raise ValueError("No init data provided")

    parsed_data = dict(urllib.parse.parse_qsl(init_data))
    if "hash" not in parsed_data:
        raise ValueError("Hash not found")

    received_hash = parsed_data.pop("hash")

    # Sort keys alphabetically
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

    # Calculate hash
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if calculated_hash != received_hash:
        raise ValueError("Invalid hash")

    if "user" in parsed_data:
        parsed_data["user"] = json.loads(parsed_data["user"])

    return parsed_data


async def get_tma_user(
    x_telegram_init_data: str = Header(None), db: AsyncSession = Depends(get_session)
) -> Customer:
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="No init data provided by Telegram")

    try:
        data = validate_init_data(x_telegram_init_data, settings.bot_token.get_secret_value())
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid auth signature") from exc

    tg_id = data.get("user", {}).get("id")
    if not tg_id:
        raise HTTPException(status_code=401, detail="No user id in init data")

    # Get user from DB with eager loading
    result = await db.execute(
        select(Customer)
        .options(selectinload(Customer.subscriptions).selectinload(Subscription.devices))
        .where(Customer.telegram_id == int(tg_id))
    )
    customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="User not found")

    return customer


def get_display_subscription(user: Customer) -> Subscription | None:
    active = next((item for item in user.subscriptions if item.is_active), None)
    if active is not None:
        return active
    return max(user.subscriptions, key=lambda item: item.expires_at) if user.subscriptions else None


@tma_router.get("/me")
async def get_me(user: Customer = Depends(get_tma_user)):
    sub = get_display_subscription(user)

    if sub:
        expire_at = int(sub.expires_at.timestamp()) if sub.expires_at else None
        active_devices = sum(1 for d in sub.devices if d.is_active)
        limit = sub.device_limit
        used_traffic = 0  # To be calculated from remnawave traffic or kept as 0 for now
        data_limit = sub.traffic_limit_gb * 1024 * 1024 * 1024 if sub.traffic_limit_gb else 0
        status = "active" if sub.is_active else "inactive"
        connect_url = f"{settings.activation_base_url.rstrip('/')}/connect/{sub.access_token}"
        raw_url = f"{settings.subscription_base_url.rstrip('/')}/{sub.access_token}"
    else:
        expire_at = None
        active_devices = 0
        limit = 0
        used_traffic = 0
        data_limit = 0
        status = "inactive"
        connect_url = None

    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "full_name": user.full_name,
        "telegram_username": user.telegram_username,
        "status": status,
        "plan_code": sub.plan_code if sub else None,
        "plan_name": PLANS.get(sub.plan_code, {}).get("name", sub.plan_code) if sub else None,
        "expire_at": expire_at,
        "used_traffic": used_traffic,
        "data_limit": data_limit,
        "active_devices": active_devices,
        "device_limit": limit,
        "connect_url": connect_url,
        "raw_url": raw_url if "raw_url" in locals() else None,
        "health_status": sub.health_status if sub else "unknown",
        "balance_rub": user.balance_rub,
        "bot_username": settings.bot_username,
        "support_username": settings.support_username,
        "payment_available": bool(
            settings.platega_merchant_id.strip()
            and settings.platega_api_key.get_secret_value().strip()
        ),
    }


@tma_router.get("/plans")
async def get_plans(user: Customer = Depends(get_tma_user)):  # noqa: ARG001
    return [
        {
            "code": code,
            "name": plan["name"],
            "price": plan["price"],
            "days": plan["days"],
            "devices": plan["devices"],
            "popular": code == "6_months",
        }
        for code, plan in PLANS.items()
    ]


@tma_router.get("/payments")
async def get_payments(
    user: Customer = Depends(get_tma_user), db: AsyncSession = Depends(get_session)
):
    result = await db.execute(
        select(PaymentTransaction)
        .where(PaymentTransaction.customer_id == user.id)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(12)
    )
    return [
        {
            "id": payment.id,
            "amount": payment.amount,
            "status": payment.status.value,
            "provider": payment.provider,
            "plan_code": payment.payload,
            "plan_name": PLANS.get(payment.payload or "", {}).get("name", payment.payload),
            "created_at": payment.created_at.isoformat(),
        }
        for payment in result.scalars().all()
    ]


@tma_router.post("/payments/{plan_code}")
async def create_tma_payment(
    plan_code: str,
    user: Customer = Depends(get_tma_user),
    db: AsyncSession = Depends(get_session),
):
    plan = PLANS.get(plan_code)
    if not plan:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    if not (
        settings.platega_merchant_id.strip()
        and settings.platega_api_key.get_secret_value().strip()
    ):
        raise HTTPException(status_code=503, detail="Оплата временно недоступна")

    transaction = PaymentTransaction(
        customer_id=user.id,
        amount=plan["price"],
        currency="RUB",
        provider="platega",
        payload=plan_code,
        status=PaymentStatus.pending,
    )
    db.add(transaction)
    await db.flush()
    try:
        payment = await create_platega_link(
            order_id=transaction.id,
            amount=plan["price"],
            description=f"HamaliVPN · {plan['name']}",
            telegram_id=user.telegram_id,
            username=user.telegram_username,
        )
    except PlategaPaymentError as exc:
        transaction.status = PaymentStatus.cancelled
        await db.commit()
        logger.exception("Could not create TMA Platega payment")
        raise HTTPException(
            status_code=502, detail="Не получилось создать платёж. Попробуйте ещё раз."
        ) from exc

    transaction.external_id = payment["transactionId"]
    await db.commit()
    return {"url": payment["url"], "transaction_id": transaction.id}


@tma_router.get("/devices")
async def get_devices(user: Customer = Depends(get_tma_user)):
    sub = get_display_subscription(user)
    if not sub:
        return []

    return [
        {
            "id": str(d.id),
            "name": d.label,
            "last_ip": d.last_ip,
            "platform": d.platform,
            "activated_at": int(d.activated_at.timestamp()) if d.activated_at else None,
        }
        for d in sub.devices
        if d.is_active
    ]


@tma_router.delete("/devices/{device_id}")
async def delete_device(
    device_id: int, user: Customer = Depends(get_tma_user), db: AsyncSession = Depends(get_session)
):
    sub = get_display_subscription(user)
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription")

    device = next((d for d in sub.devices if d.id == device_id), None)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    device.is_active = False
    await db.commit()
    return {"status": "ok"}


@tma_router.get("/referrals")
async def get_referrals(
    user: Customer = Depends(get_tma_user), db: AsyncSession = Depends(get_session)
):
    # Load referrals explicitly
    result = await db.execute(select(Customer).where(Customer.referrer_id == user.id))
    referrals = result.scalars().all()

    earned_result = await db.execute(
        select(func.coalesce(func.sum(BalanceTransaction.amount), 0)).where(
            BalanceTransaction.customer_id == user.id,
            BalanceTransaction.type == "referral_bonus",
        )
    )
    pending_result = await db.execute(
        select(WithdrawalRequest).where(
            WithdrawalRequest.customer_id == user.id,
            WithdrawalRequest.status == WithdrawalStatus.pending,
        )
    )
    pending = pending_result.scalars().first()
    return {
        "balance": user.balance_rub,
        "total_referrals": len(referrals),
        "total_earned": int(earned_result.scalar_one() or 0),
        "commission_percent": 30,
        "minimum_withdrawal": 100,
        "pending_withdrawal": (
            {"amount": pending.amount, "status": pending.status.value} if pending else None
        ),
        "bot_username": settings.bot_username,
    }


@tma_router.post("/withdraw")
async def request_withdraw(
    user: Customer = Depends(get_tma_user), db: AsyncSession = Depends(get_session)
):
    if user.balance_rub < 100:
        raise HTTPException(status_code=400, detail="Minimum withdrawal amount is 100 RUB")

    # Check if pending request exists
    pending = await db.execute(
        select(WithdrawalRequest).where(
            WithdrawalRequest.customer_id == user.id,
            WithdrawalRequest.status == WithdrawalStatus.pending,
        )
    )
    if pending.scalars().first():
        raise HTTPException(status_code=400, detail="You already have a pending request")

    req = WithdrawalRequest(
        customer_id=user.id,
        amount=user.balance_rub,
        requisites=user.withdrawal_requisites or "Not specified",
        status=WithdrawalStatus.pending,
    )
    db.add(req)
    # Deduct balance immediately
    user.balance_rub = 0
    await db.commit()

    return {"status": "ok"}
