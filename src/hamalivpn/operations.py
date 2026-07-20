import asyncio
import logging
from datetime import timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AuditLog,
    Customer,
    PaymentStatus,
    PaymentTransaction,
    Subscription,
    SubscriptionDevice,
    SubscriptionStatus,
    WithdrawalRequest,
    WithdrawalStatus,
    utcnow,
)
from .remnawave import RemnawaveGateway

logger = logging.getLogger(__name__)


async def operations_dashboard(db: AsyncSession, gateway: RemnawaveGateway) -> dict:
    now = utcnow()
    cutoff_30d = now - timedelta(days=30)
    cutoff_60d = now - timedelta(days=60)
    stale_payment_cutoff = now - timedelta(hours=24)

    resellers = await db.scalar(
        select(func.count()).select_from(Customer).where(Customer.role == "reseller")
    ) or 0
    clients = await db.scalar(
        select(func.count()).select_from(Customer).where(Customer.referrer_id.is_not(None))
    ) or 0
    active_subs = await db.scalar(
        select(func.count())
        .select_from(Subscription)
        .where(Subscription.status == SubscriptionStatus.active)
    ) or 0
    active_users_30d = await db.scalar(
        select(func.count(func.distinct(Subscription.customer_id)))
        .select_from(Subscription)
        .join(SubscriptionDevice, SubscriptionDevice.subscription_id == Subscription.id)
        .where(SubscriptionDevice.last_seen_at >= cutoff_30d)
    ) or 0
    revenue = await db.scalar(
        select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(
            PaymentTransaction.status == PaymentStatus.paid
        )
    ) or 0
    revenue_30d = await db.scalar(
        select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(
            PaymentTransaction.status == PaymentStatus.paid,
            PaymentTransaction.updated_at >= cutoff_30d,
        )
    ) or 0
    revenue_previous_30d = await db.scalar(
        select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(
            PaymentTransaction.status == PaymentStatus.paid,
            PaymentTransaction.updated_at >= cutoff_60d,
            PaymentTransaction.updated_at < cutoff_30d,
        )
    ) or 0
    renewals_30d = await db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.created_at >= cutoff_30d,
            AuditLog.action.in_(["reseller.key.renewed", "trial.extended"]),
        )
    ) or 0
    payment_errors_30d = await db.scalar(
        select(func.count())
        .select_from(PaymentTransaction)
        .where(
            PaymentTransaction.updated_at >= cutoff_30d,
            PaymentTransaction.status.in_([PaymentStatus.cancelled, PaymentStatus.expired]),
        )
    ) or 0
    fulfillment_errors_30d = await db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.created_at >= cutoff_30d,
            AuditLog.action == "payment.fulfillment.failed",
        )
    ) or 0
    stale_pending_payments = await db.scalar(
        select(func.count())
        .select_from(PaymentTransaction)
        .where(
            PaymentTransaction.status == PaymentStatus.pending,
            PaymentTransaction.created_at < stale_payment_cutoff,
        )
    ) or 0
    reseller_balance = await db.scalar(
        select(func.coalesce(func.sum(Customer.balance_rub), 0)).where(
            Customer.role.in_(["reseller", "admin", "super_admin"])
        )
    ) or 0

    recent = (
        (
            await db.execute(
                select(PaymentTransaction)
                .where(PaymentTransaction.status == PaymentStatus.paid)
                .order_by(desc(PaymentTransaction.created_at))
                .limit(8)
            )
        )
        .scalars()
        .all()
    )
    paid_30d = (
        (
            await db.execute(
                select(PaymentTransaction).where(
                    PaymentTransaction.status == PaymentStatus.paid,
                    PaymentTransaction.updated_at >= cutoff_30d,
                )
            )
        )
        .scalars()
        .all()
    )
    daily_revenue: dict[str, int] = {}
    for transaction in paid_30d:
        day = (transaction.updated_at or transaction.created_at).date().isoformat()
        daily_revenue[day] = daily_revenue.get(day, 0) + int(transaction.amount)

    withdrawal_rows = (
        (
            await db.execute(
                select(WithdrawalRequest)
                .order_by(desc(WithdrawalRequest.created_at))
                .limit(12)
            )
        )
        .scalars()
        .all()
    )
    withdrawal_summary: dict[str, dict[str, int]] = {}
    for status in WithdrawalStatus:
        count, amount = (
            await db.execute(
                select(func.count(), func.coalesce(func.sum(WithdrawalRequest.amount), 0)).where(
                    WithdrawalRequest.status == status
                )
            )
        ).one()
        withdrawal_summary[status.value] = {"count": int(count), "amount_rub": int(amount)}

    nodes: list[dict] = []
    nodes_error: str | None = None
    try:
        nodes = await asyncio.wait_for(gateway.list_nodes_summary(), timeout=4.0)
    except Exception as exc:
        logger.warning("Could not load node summaries for admin dashboard: %s", exc)
        nodes_error = "Метрики Remnawave временно недоступны"

    mrr_change_percent: float | None = None
    if revenue_previous_30d:
        mrr_change_percent = round(
            (int(revenue_30d) - int(revenue_previous_30d)) / int(revenue_previous_30d) * 100,
            1,
        )
    return {
        "resellers": resellers,
        "clients": clients,
        "active_subs": active_subs,
        "active_users_30d": active_users_30d,
        "revenue_rub": int(revenue),
        "mrr_rub": int(revenue_30d),
        "mrr_change_percent": mrr_change_percent,
        "renewals_30d": int(renewals_30d),
        "payment_errors_30d": int(payment_errors_30d) + int(fulfillment_errors_30d),
        "stale_pending_payments": int(stale_pending_payments),
        "reseller_balance_rub": int(reseller_balance),
        "daily_revenue": [
            {"date": day, "amount_rub": amount}
            for day, amount in sorted(daily_revenue.items())
        ],
        "withdrawals": withdrawal_summary,
        "recent_withdrawals": [
            {
                "id": row.id,
                "customer_id": row.customer_id,
                "amount_rub": row.amount,
                "status": row.status.value,
                "requisites": row.requisites,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in withdrawal_rows
        ],
        "nodes": nodes,
        "nodes_error": nodes_error,
        "recent_payments": [
            {
                "amount": transaction.amount,
                "provider": transaction.provider,
                "payload": transaction.payload,
                "date": transaction.created_at.isoformat() if transaction.created_at else None,
            }
            for transaction in recent
        ],
    }
