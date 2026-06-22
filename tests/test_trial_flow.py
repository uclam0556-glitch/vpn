from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from hamalivpn.config import Settings
from hamalivpn.models import Customer, Subscription, SubscriptionStatus
from hamalivpn.remnawave import MockRemnawaveClient
from hamalivpn.services import (
    TrialAlreadyUsedError,
    expire_due_subscriptions,
    issue_trial,
)


def make_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        public_base_url="https://vpn.example.com",
        remnawave_mock=True,
        trial_duration_minutes=90,
        trial_traffic_gb=30,
        trial_device_limit=1,
    )


@pytest.mark.asyncio
async def test_trial_is_issued_once_with_limits(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        result = await issue_trial(
            session,
            gateway,
            settings,
            telegram_id=777,
            telegram_username="tester",
            full_name="Test User",
        )
        subscription = await session.get(Subscription, result.subscription_id)
        customer = await session.scalar(select(Customer).where(Customer.telegram_id == 777))

        assert customer is not None and customer.trial_used is True
        assert subscription is not None
        assert subscription.status == SubscriptionStatus.active
        assert subscription.device_limit == 1
        assert subscription.traffic_limit_gb == 30
        assert result.connect_url.host == "vpn.example.com"

    async with session_factory() as session:
        with pytest.raises(TrialAlreadyUsedError):
            await issue_trial(
                session,
                gateway,
                settings,
                telegram_id=777,
                telegram_username="tester",
                full_name="Test User",
            )


@pytest.mark.asyncio
async def test_maintenance_expires_due_subscription(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        result = await issue_trial(
            session,
            gateway,
            settings,
            telegram_id=888,
            telegram_username="late",
            full_name="Late User",
        )
        subscription = await session.get(Subscription, result.subscription_id)
        assert subscription is not None
        subscription.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

        count = await expire_due_subscriptions(session, gateway)
        await session.refresh(subscription)
        assert count == 1
        assert subscription.status == SubscriptionStatus.expired
