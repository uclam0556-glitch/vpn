from datetime import UTC, datetime, timedelta

import aiogram
import pytest
from sqlalchemy import select

from hamalivpn.config import Settings
from hamalivpn.maintenance import normalize_trial_device_limits, send_expiry_reminders
from hamalivpn.models import AuditLog, Customer, Subscription, SubscriptionStatus


def make_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        public_base_url="https://vpn.example.com",
        bot_token="test-token",
        remnawave_mock=True,
        trial_access_days=2,
        trial_device_limit=1,
    )


class FakeTelegramSession:
    async def close(self) -> None:
        return None


class FakeBot:
    messages: list[dict] = []

    def __init__(self, *, token: str) -> None:
        assert token == "test-token"
        self.session = FakeTelegramSession()

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})


@pytest.mark.asyncio
async def test_expiry_notifications_are_sent_once_for_2d_1d_and_expired(
    session_factory, monkeypatch
) -> None:
    FakeBot.messages = []
    monkeypatch.setattr(aiogram, "Bot", FakeBot)
    settings = make_settings()
    now = datetime.now(UTC)

    async with session_factory() as session:
        for index, (status, expires_at) in enumerate(
            (
                (SubscriptionStatus.active, now + timedelta(hours=47)),
                (SubscriptionStatus.active, now + timedelta(hours=23)),
                (SubscriptionStatus.expired, now - timedelta(hours=1)),
            ),
            start=1,
        ):
            customer = Customer(telegram_id=5000 + index, full_name=f"User {index}")
            session.add(customer)
            await session.flush()
            session.add(
                Subscription(
                    customer_id=customer.id,
                    plan_code="trial" if index == 1 else "1_month",
                    status=status,
                    access_token=f"notice-token-{index}",
                    device_limit=1,
                    traffic_limit_gb=0,
                    expires_at=expires_at,
                )
            )
        await session.commit()

        assert await send_expiry_reminders(session, settings) == 3
        assert await send_expiry_reminders(session, settings) == 0

        actions = list(await session.scalars(select(AuditLog.action)))

    texts = "\n".join(item["text"] for item in FakeBot.messages)
    assert len(FakeBot.messages) == 3
    assert "осталось 2 дня" in texts
    assert "заканчивается завтра" in texts
    assert "Подписка закончилась" in texts
    assert "МСК" in texts
    assert sum(action.startswith("subscription.expiry_notice.") for action in actions) == 3


class RecordingGateway:
    def __init__(self) -> None:
        self.limits: list[tuple[str, int]] = []

    async def set_device_limit(self, user_uuid: str, device_limit: int) -> None:
        self.limits.append((user_uuid, device_limit))


@pytest.mark.asyncio
async def test_existing_trials_are_normalized_to_one_device(session_factory) -> None:
    settings = make_settings()
    gateway = RecordingGateway()
    async with session_factory() as session:
        customer = Customer(telegram_id=6001, full_name="Legacy Trial", trial_used=True)
        session.add(customer)
        await session.flush()
        subscription = Subscription(
            customer_id=customer.id,
            plan_code="trial",
            status=SubscriptionStatus.active,
            remnawave_uuid="00000000-0000-0000-0000-000000000001",
            access_token="legacy-trial-token",
            device_limit=5,
            traffic_limit_gb=0,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(subscription)
        await session.commit()

        assert await normalize_trial_device_limits(session, gateway, settings) == 1
        await session.refresh(subscription)
        assert subscription.device_limit == 1
        assert gateway.limits == [(subscription.remnawave_uuid, 1)]
        assert await normalize_trial_device_limits(session, gateway, settings) == 0
