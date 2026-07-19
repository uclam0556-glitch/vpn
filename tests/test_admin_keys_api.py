from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from hamalivpn.api import app, get_portal_user, get_session
from hamalivpn.models import Customer, Subscription, SubscriptionStatus
from hamalivpn.services import subscription_short_code


@pytest.mark.asyncio
async def test_admin_keys_include_client_management_fields(session_factory) -> None:
    async with session_factory() as session:
        admin = Customer(telegram_id=5392719643, full_name="KhmD", role="super_admin")
        reseller = Customer(telegram_id=777000, full_name="Partner", role="reseller")
        session.add_all([admin, reseller])
        await session.flush()

        client = Customer(
            telegram_id=900001,
            full_name="Client One",
            role="client",
            referrer_id=reseller.id,
        )
        session.add(client)
        await session.flush()

        session.add(
            Subscription(
                customer_id=client.id,
                plan_code="1 месяц · 1 устройство",
                status=SubscriptionStatus.active,
                remnawave_uuid="11111111-1111-4111-8111-111111111111",
                remnawave_short_uuid="short-one",
                subscription_url="https://panel.example.test/api/sub/short-one",
                access_token="access-token-admin-list",
                device_limit=1,
                traffic_limit_gb=0,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_user() -> dict[str, int]:
        return {"id": 5392719643}

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_portal_user] = override_user
    try:
        response = TestClient(app).get("/api/admin/keys")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["name"] == "Client One"
    assert payload[0]["sub_status"] == "active"
    assert payload[0]["device_limit"] == 1
    assert payload[0]["remnawave_uuid"] == "11111111-1111-4111-8111-111111111111"
    assert payload[0]["short_code"] == subscription_short_code("access-token-admin-list")
    assert payload[0]["connect_url"].endswith(f"/{payload[0]['short_code']}")
    assert payload[0]["reseller_id"] == reseller.id


@pytest.mark.asyncio
async def test_super_admin_can_create_and_list_separate_subadmins(session_factory) -> None:
    async with session_factory() as session:
        session.add(Customer(telegram_id=5392719643, full_name="Owner", role="super_admin"))
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_user() -> dict[str, int]:
        return {"id": 5392719643}

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_portal_user] = override_user
    try:
        client = TestClient(app)
        created = client.post(
            "/api/admin/subadmins",
            json={"name": "Support Operator", "telegram_id": 900002},
        )
        listed = client.get("/api/admin/subadmins")
        resellers = client.get("/api/admin/resellers")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 200
    assert created.json()["role"] == "admin"
    assert len(created.json()["portal_access_key"]) >= 32
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["Support Operator"]
    assert resellers.status_code == 200
    assert resellers.json() == []


@pytest.mark.asyncio
async def test_super_admin_can_choose_and_replace_subadmin_key(session_factory) -> None:
    async with session_factory() as session:
        session.add(Customer(telegram_id=5392719643, full_name="Owner", role="super_admin"))
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_user() -> dict[str, int]:
        return {"id": 5392719643}

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_portal_user] = override_user
    try:
        client = TestClient(app)
        created = client.post(
            "/api/admin/subadmins",
            json={"name": "Support", "telegram_id": 900010, "key": "support-team"},
        )
        duplicate = client.post(
            "/api/admin/subadmins",
            json={"name": "Second", "telegram_id": 900011, "key": "support-team"},
        )
        invalid = client.post(
            "/api/admin/subadmins",
            json={"name": "Third", "telegram_id": 900012, "key": "ключ с пробелами"},
        )
        replaced = client.post(
            f"/api/admin/resellers/{created.json()['id']}/key",
            json={"key": "support-night"},
        )
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 200
    assert created.json()["portal_access_key"] == "support-team"
    assert duplicate.status_code == 409
    assert invalid.status_code == 400
    assert replaced.status_code == 200
    assert replaced.json()["portal_access_key"] == "support-night"
