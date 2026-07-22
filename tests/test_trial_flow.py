from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from hamalivpn.config import Settings
from hamalivpn.models import Customer, Subscription, SubscriptionStatus, as_utc
from hamalivpn.remnawave import MockRemnawaveClient
from hamalivpn.services import (
    TrialAlreadyUsedError,
    expire_due_subscriptions,
    get_subscription_by_token,
    issue_trial,
    subscription_short_code,
)


def make_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        public_base_url="https://vpn.example.com",
        remnawave_mock=True,
        trial_access_days=2,
        trial_traffic_gb=0,
        trial_device_limit=1,
    )


@pytest.mark.asyncio
async def test_repeated_trial_tap_is_rejected_without_changing_expiry(session_factory) -> None:
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
        assert subscription.traffic_limit_gb == 0
        assert result.connect_url.host == "vpn.example.com"
        assert result.connect_url.path == f"/{subscription_short_code(subscription)}"
        original_subscription_id = result.subscription_id
        original_expiry = subscription.expires_at

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
        subscription = await session.get(Subscription, original_subscription_id)
        assert subscription is not None
        assert subscription.status == SubscriptionStatus.active
        assert subscription.traffic_limit_gb == 0
        assert subscription.expires_at == original_expiry
        assert (
            timedelta(days=1, hours=23)
            <= as_utc(subscription.expires_at) - datetime.now(UTC)
            <= timedelta(days=2)
        )


@pytest.mark.asyncio
async def test_trial_history_repairs_false_customer_flag_and_blocks_reissue(
    session_factory,
) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        customer = Customer(telegram_id=779, full_name="Imported User", trial_used=False)
        session.add(customer)
        await session.flush()
        session.add(
            Subscription(
                customer_id=customer.id,
                plan_code="trial",
                status=SubscriptionStatus.expired,
                access_token="historical-trial-token",
                device_limit=1,
                traffic_limit_gb=0,
                expires_at=datetime.now(UTC) - timedelta(days=10),
            )
        )
        await session.commit()

        with pytest.raises(TrialAlreadyUsedError):
            await issue_trial(
                session,
                gateway,
                settings,
                telegram_id=779,
                telegram_username="imported",
                full_name="Imported User",
            )

        await session.refresh(customer)
        trials = (
            (
                await session.execute(
                    select(Subscription).where(Subscription.customer_id == customer.id)
                )
            )
            .scalars()
            .all()
        )
        assert customer.trial_used is True
        assert len(trials) == 1


@pytest.mark.asyncio
async def test_subscription_can_be_resolved_by_full_or_short_token(session_factory) -> None:
    async with session_factory() as session:
        customer = Customer(telegram_id=10001, full_name="Short Link User")
        session.add(customer)
        await session.flush()
        subscription = Subscription(
            customer_id=customer.id,
            plan_code="test",
            status=SubscriptionStatus.active,
            access_token="short-link-token-abcdef123456",
            device_limit=1,
            traffic_limit_gb=0,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        session.add(subscription)
        await session.commit()

        by_full = await get_subscription_by_token(session, "short-link-token-abcdef123456")
        by_short = await get_subscription_by_token(session, subscription_short_code(subscription))
        by_too_short = await get_subscription_by_token(session, "short")

        assert by_full is not None and by_full.id == subscription.id
        assert by_short is not None and by_short.id == subscription.id
        assert by_too_short is None


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
