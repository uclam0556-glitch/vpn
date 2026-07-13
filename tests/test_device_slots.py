from datetime import UTC, datetime, timedelta

import pytest

from hamalivpn.config import Settings
from hamalivpn.device_slots import (
    DeviceLimitReached,
    active_device_slot_count,
    device_subscription_url,
    ensure_device_slot,
)
from hamalivpn.models import Customer, Subscription, SubscriptionDevice, SubscriptionStatus
from hamalivpn.remnawave import MockRemnawaveClient


def make_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        public_base_url="https://app.example.com",
        panel_base_url="https://panel.example.com",
        remnawave_mock=True,
    )


async def create_subscription(session, *, device_limit: int) -> Subscription:
    customer = Customer(
        telegram_id=9000 + device_limit,
        telegram_username="limit_tester",
        full_name="Limit Tester",
    )
    session.add(customer)
    await session.flush()
    subscription = Subscription(
        customer_id=customer.id,
        plan_code=f"test_{device_limit}",
        status=SubscriptionStatus.active,
        access_token=f"access-token-{device_limit}",
        device_limit=device_limit,
        traffic_limit_gb=0,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(subscription)
    await session.flush()
    return subscription


def test_device_subscription_url_uses_public_host() -> None:
    settings = make_settings()
    subscription = Subscription(
        customer_id="customer-id",
        plan_code="test",
        status=SubscriptionStatus.active,
        access_token="access-token",
        device_limit=1,
        traffic_limit_gb=0,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        subscription_url="https://panel.example.com/api/sub/origin-token",
    )
    slot = SubscriptionDevice(
        subscription_id="subscription-id",
        device_token="opaque-device-token",
        is_active=True,
    )

    assert device_subscription_url(settings, subscription, slot) == (
        "https://app.example.com/api/sub/opaque-device-token"
    )


def test_device_subscription_url_uses_direct_portal_in_production() -> None:
    settings = make_settings()
    settings.environment = "production"
    subscription = Subscription(
        customer_id="customer-id",
        plan_code="test",
        status=SubscriptionStatus.active,
        access_token="access-token",
        device_limit=1,
        traffic_limit_gb=0,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    slot = SubscriptionDevice(
        subscription_id="subscription-id",
        device_token="opaque-device-token",
        is_active=True,
    )

    assert device_subscription_url(settings, subscription, slot) == (
        "https://portal.hamali.ru/api/sub/opaque-device-token"
    )


@pytest.mark.asyncio
async def test_one_device_plan_allows_same_slot_reuse(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        subscription = await create_subscription(session, device_limit=1)

        first = await ensure_device_slot(
            session,
            gateway,
            settings,
            subscription,
            client_ip="10.0.0.1",
            user_agent="Mozilla/5.0 (iPhone)",
        )
        same = await ensure_device_slot(
            session,
            gateway,
            settings,
            subscription,
            existing_token=first.device_token,
            client_ip="10.0.0.2",
            user_agent="Mozilla/5.0 (iPhone)",
        )

        assert same.id == first.id
        assert await active_device_slot_count(session, subscription.id) == 1
        assert first.remnawave_uuid
        assert first.remnawave_short_uuid


@pytest.mark.asyncio
async def test_one_device_plan_rejects_second_distinct_slot(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        subscription = await create_subscription(session, device_limit=1)

        await ensure_device_slot(
            session,
            gateway,
            settings,
            subscription,
            client_ip="10.0.0.1",
            user_agent="Mozilla/5.0 (iPhone)",
        )

        with pytest.raises(DeviceLimitReached):
            await ensure_device_slot(
                session,
                gateway,
                settings,
                subscription,
                client_ip="10.0.0.2",
                user_agent="Mozilla/5.0 (Macintosh)",
            )

        assert await active_device_slot_count(session, subscription.id) == 1


@pytest.mark.asyncio
async def test_multi_device_plan_allows_limit_and_rejects_extra(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        subscription = await create_subscription(session, device_limit=3)

        for index in range(3):
            slot = await ensure_device_slot(
                session,
                gateway,
                settings,
                subscription,
                client_ip=f"10.0.0.{index + 1}",
                user_agent=f"Mozilla/5.0 device-{index}",
            )
            assert slot.remnawave_uuid

        with pytest.raises(DeviceLimitReached):
            await ensure_device_slot(
                session,
                gateway,
                settings,
                subscription,
                client_ip="10.0.0.4",
                user_agent="Mozilla/5.0 extra-device",
            )

        assert await active_device_slot_count(session, subscription.id) == 3


@pytest.mark.asyncio
async def test_stale_preview_slot_can_be_replaced_without_burning_limit(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        subscription = await create_subscription(session, device_limit=1)
        activated_at = datetime.now(UTC) - timedelta(minutes=5)
        preview_slot = SubscriptionDevice(
            subscription_id=subscription.id,
            device_token="preview-token",
            label="Preview",
            remnawave_uuid="11111111-1111-4111-8111-111111111111",
            remnawave_short_uuid="preview-short",
            is_active=True,
            first_ip="10.0.0.1",
            last_ip="10.0.0.1",
            user_agent="TelegramBot (link preview)",
            activated_at=activated_at,
            last_seen_at=activated_at + timedelta(seconds=10),
        )
        session.add(preview_slot)
        await session.flush()

        real_slot = await ensure_device_slot(
            session,
            gateway,
            settings,
            subscription,
            client_ip="10.0.0.2",
            user_agent="Mozilla/5.0 (iPhone)",
        )

        assert real_slot.id != preview_slot.id
        assert real_slot.is_active is True
        assert preview_slot.is_active is False
        assert await active_device_slot_count(session, subscription.id) == 1
