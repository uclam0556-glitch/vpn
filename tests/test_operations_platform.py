from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import hamalivpn.api as api_module
from hamalivpn.api import app, get_portal_user, get_session
from hamalivpn.config import Settings
from hamalivpn.feature_flags import flag_is_active, rollout_bucket
from hamalivpn.models import Customer, FeatureFlag, PaymentStatus, PaymentTransaction
from hamalivpn.portal_security import PortalSecurityStore


def memory_security_store() -> PortalSecurityStore:
    store = PortalSecurityStore(Settings(environment="test", portal_session_ttl_seconds=600))
    store.clients = []
    return store


@pytest.mark.asyncio
async def test_portal_session_replaces_browser_key_and_can_be_revoked(
    session_factory, monkeypatch
) -> None:
    async with session_factory() as session:
        session.add(
            Customer(
                telegram_id=700001,
                full_name="Operations",
                role="super_admin",
                portal_access_key="operations-secure-key",
            )
        )
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(api_module, "portal_security", memory_security_store())
    monkeypatch.setattr(
        api_module,
        "settings",
        Settings(environment="test", secure_cookies=False, portal_session_ttl_seconds=600),
    )
    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        login = client.post("/api/portal/session", json={"key": "operations-secure-key"})
        me = client.get("/api/portal/me")
        logout = client.delete("/api/portal/session")
        after_logout = client.get("/api/portal/me")
    finally:
        app.dependency_overrides.clear()

    assert login.status_code == 200
    assert login.json()["role"] == "super_admin"
    assert "HttpOnly" in login.headers["set-cookie"]
    assert me.status_code == 200
    assert me.json()["name"] == "Operations"
    assert logout.status_code == 200
    assert after_logout.status_code == 401


@pytest.mark.asyncio
async def test_redis_rate_limit_has_a_bounded_fallback() -> None:
    store = memory_security_store()
    first = await store.rate_limit("login", "198.51.100.1", limit=2, window=60)
    second = await store.rate_limit("login", "198.51.100.1", limit=2, window=60)
    blocked = await store.rate_limit("login", "198.51.100.1", limit=2, window=60)

    assert first.allowed and second.allowed
    assert not blocked.allowed
    assert blocked.retry_after > 0


def test_feature_flag_rollout_is_stable_and_fail_closed() -> None:
    subject = "subscription-token-1"
    assert rollout_bucket("subscription_output_v2", subject) == rollout_bucket(
        "subscription_output_v2", subject
    )
    disabled = FeatureFlag(
        key="subscription_output_v2", enabled=False, rollout_percent=100, config={}
    )
    assert not flag_is_active(disabled, subject)
    forced = FeatureFlag(
        key="subscription_output_v2",
        enabled=True,
        rollout_percent=0,
        config={"allow_subjects": [subject]},
    )
    assert flag_is_active(forced, subject)


@pytest.mark.asyncio
async def test_admin_dashboard_and_feature_flags_are_operational(
    session_factory, monkeypatch
) -> None:
    async with session_factory() as session:
        admin = Customer(telegram_id=700002, full_name="Owner", role="super_admin")
        session.add(admin)
        await session.flush()
        session.add(
            PaymentTransaction(
                customer_id=admin.id,
                amount=990,
                provider="platega",
                external_id="dashboard-paid-1",
                status=PaymentStatus.paid,
                payload="30d",
            )
        )
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_user() -> dict[str, int]:
        return {"id": 700002}

    class FakeGateway:
        async def list_nodes_summary(self) -> list[dict]:
            return [
                {
                    "name": "France",
                    "country_code": "FR",
                    "connected": True,
                    "disabled": False,
                    "users_online": 3,
                    "traffic_used_bytes": 100,
                    "rx_mbps": 1.2,
                    "tx_mbps": 0.8,
                    "cpu_percent": 4.0,
                    "memory_percent": 12.0,
                    "updated_at": None,
                }
            ]

    monkeypatch.setattr(api_module, "make_remnawave_gateway", lambda _settings: FakeGateway())
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_portal_user] = override_user
    try:
        client = TestClient(app)
        dashboard = client.get("/api/admin/dashboard")
        flags_before = client.get("/api/admin/feature-flags")
        update = client.put(
            "/api/admin/feature-flags/subscription_output_v2",
            json={"enabled": True, "rollout_percent": 5, "allow_subjects": ["pilot"]},
        )
        flags_after = client.get("/api/admin/feature-flags")
    finally:
        app.dependency_overrides.clear()

    assert dashboard.status_code == 200
    assert dashboard.json()["mrr_rub"] == 990
    assert dashboard.json()["nodes"][0]["users_online"] == 3
    assert flags_before.status_code == 200
    assert update.status_code == 200
    updated = next(
        flag for flag in flags_after.json() if flag["key"] == "subscription_output_v2"
    )
    assert updated["enabled"] is True
    assert updated["rollout_percent"] == 5
