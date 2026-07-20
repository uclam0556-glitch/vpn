from datetime import UTC, datetime, timedelta

import pytest

from hamalivpn.models import Customer, Subscription, SubscriptionStatus
from hamalivpn.payments import PLANS
from hamalivpn.tma_api import get_me, get_plans


@pytest.mark.asyncio
async def test_tma_plans_match_live_payment_plans() -> None:
    plans = await get_plans(user=Customer(telegram_id=1))

    assert {item["code"] for item in plans} == set(PLANS)
    for item in plans:
        source = PLANS[item["code"]]
        assert item["price"] == source["price"]
        assert item["days"] == source["days"]
        assert item["devices"] == source["devices"]


@pytest.mark.asyncio
async def test_tma_me_prefers_active_subscription() -> None:
    customer = Customer(telegram_id=42, full_name="Test User", telegram_username="test")
    customer.subscriptions = [
        Subscription(
            plan_code="1_month",
            status=SubscriptionStatus.expired,
            access_token="old",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        ),
        Subscription(
            plan_code="6_months",
            status=SubscriptionStatus.active,
            access_token="active",
            device_limit=5,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
    ]

    result = await get_me(customer)

    assert result["status"] == "active"
    assert result["plan_code"] == "6_months"
    assert result["plan_name"] == PLANS["6_months"]["name"]
    assert result["device_limit"] == 5
