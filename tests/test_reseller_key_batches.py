from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import hamalivpn.api as api_module
from hamalivpn.api import app, get_portal_user, get_session
from hamalivpn.models import (
    BalanceTransaction,
    Customer,
    ResellerKeyBatch,
    Subscription,
    Tariff,
)
from hamalivpn.schemas import RemoteUser


class FakeGateway:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.created: list[str] = []
        self.disabled: list[str] = []

    async def create_user(self, **kwargs) -> RemoteUser:
        call_number = len(self.created) + 1
        if self.fail_at == call_number:
            self.fail_at = None
            raise RuntimeError("simulated Remnawave failure")
        user_uuid = f"00000000-0000-4000-8000-{call_number:012d}"
        self.created.append(user_uuid)
        return RemoteUser(
            uuid=user_uuid,
            short_uuid=f"seat-{call_number}",
            username=kwargs["username"],
            subscription_url=f"https://panel.example.test/api/sub/seat-{call_number}",
            expire_at=kwargs["expires_at"],
            device_limit=kwargs["device_limit"],
        )

    async def disable_user(self, user_uuid: str) -> None:
        self.disabled.append(user_uuid)


async def seed_reseller_and_tariff(session_factory, *, device_limit: int = 5) -> tuple[int, int]:
    async with session_factory() as session:
        reseller = Customer(
            telegram_id=880001,
            full_name="Reseller",
            role="reseller",
            balance_rub=2_000,
        )
        tariff = Tariff(
            name="6 месяцев · 5 устройств",
            duration_days=180,
            price_rub=600,
            device_limit=device_limit,
            traffic_limit_gb=0,
            is_active=True,
        )
        session.add_all([reseller, tariff])
        await session.commit()
        return reseller.id, tariff.id


def configure_dependencies(session_factory) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_user() -> dict[str, int]:
        return {"id": 880001}

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_portal_user] = override_user


@pytest.mark.asyncio
async def test_seat_pack_creates_independent_keys_and_charges_once(
    session_factory, monkeypatch
) -> None:
    reseller_id, tariff_id = await seed_reseller_and_tariff(session_factory)
    gateway = FakeGateway()
    monkeypatch.setattr(api_module, "make_remnawave_gateway", lambda _settings: gateway)
    configure_dependencies(session_factory)
    try:
        client = TestClient(app)
        body = {
            "tariff_id": tariff_id,
            "product_mode": "seat_pack",
            "request_id": "pack-order-001",
            "client_name": "Пять клиентов",
        }
        created = client.post("/api/reseller/keys/buy", json=body)
        repeated = client.post("/api/reseller/keys/buy", json=body)
        packages = client.get("/api/reseller/key-batches")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["product_mode"] == "seat_pack"
    assert payload["total_seats"] == 5
    assert payload["assigned_seats"] == 0
    assert payload["free_seats"] == 5
    assert [seat["seat_number"] for seat in payload["seats"]] == [1, 2, 3, 4, 5]
    assert {seat["device_limit"] for seat in payload["seats"]} == {1}
    assert len({seat["connect_url"] for seat in payload["seats"]}) == 5
    assert repeated.status_code == 200
    assert repeated.json()["id"] == payload["id"]
    assert len(gateway.created) == 5
    assert packages.status_code == 200
    assert packages.json()[0]["id"] == payload["id"]

    async with session_factory() as session:
        reseller = await session.get(Customer, reseller_id)
        batch_count = await session.scalar(select(func.count()).select_from(ResellerKeyBatch))
        subs = (await session.execute(select(Subscription))).scalars().all()
        transactions = (
            (
                await session.execute(
                    select(BalanceTransaction).where(
                        BalanceTransaction.customer_id == reseller_id,
                        BalanceTransaction.type == "purchase",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert reseller.balance_rub == 1_400
    assert batch_count == 1
    assert len(subs) == 5
    assert {sub.device_limit for sub in subs} == {1}
    assert len(transactions) == 1
    assert transactions[0].amount == -600


@pytest.mark.asyncio
async def test_seat_assignment_and_device_limit_guard(session_factory, monkeypatch) -> None:
    _, tariff_id = await seed_reseller_and_tariff(session_factory)
    gateway = FakeGateway()
    monkeypatch.setattr(api_module, "make_remnawave_gateway", lambda _settings: gateway)
    configure_dependencies(session_factory)
    try:
        client = TestClient(app)
        created = client.post(
            "/api/reseller/keys/buy",
            json={
                "tariff_id": tariff_id,
                "product_mode": "seat_pack",
                "request_id": "pack-order-002",
                "client_name": "Команда",
            },
        ).json()
        assigned = client.patch(
            f"/api/reseller/key-batches/{created['id']}/seats/1",
            json={"client_name": "Иван", "client_telegram": "@ivan"},
        )
        client_list = client.get("/api/reseller/clients")
        limit_change = client.put(
            f"/api/reseller/clients/{created['seats'][0]['remnawave_uuid']}",
            json={"devices_limit": 5},
        )
    finally:
        app.dependency_overrides.clear()

    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assigned"] is True
    assert assigned.json()["client_name"] == "Иван"
    assert assigned.json()["client_telegram"] == "ivan"
    assert client_list.status_code == 200
    assert len(client_list.json()) == 1
    assert client_list.json()[0]["product_mode"] == "seat_pack"
    assert client_list.json()[0]["device_limit"] == 1
    assert client_list.json()[0]["telegram_id"] == "ivan"
    assert limit_change.status_code == 403
    assert "задаётся тарифом" in limit_change.json()["detail"]


@pytest.mark.asyncio
async def test_partial_remote_failure_rolls_back_pack_and_balance(
    session_factory, monkeypatch
) -> None:
    reseller_id, tariff_id = await seed_reseller_and_tariff(session_factory)
    gateway = FakeGateway(fail_at=3)
    monkeypatch.setattr(api_module, "make_remnawave_gateway", lambda _settings: gateway)
    configure_dependencies(session_factory)
    try:
        response = TestClient(app).post(
            "/api/reseller/keys/buy",
            json={
                "tariff_id": tariff_id,
                "product_mode": "seat_pack",
                "request_id": "pack-order-failed",
                "client_name": "Rollback check",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert sorted(gateway.disabled) == sorted(gateway.created)
    async with session_factory() as session:
        reseller = await session.get(Customer, reseller_id)
        batch_count = await session.scalar(select(func.count()).select_from(ResellerKeyBatch))
        sub_count = await session.scalar(select(func.count()).select_from(Subscription))
        customer_count = await session.scalar(select(func.count()).select_from(Customer))
    assert reseller.balance_rub == 2_000
    assert batch_count == 0
    assert sub_count == 0
    assert customer_count == 1


@pytest.mark.asyncio
async def test_family_purchase_keeps_original_multi_device_behavior(
    session_factory, monkeypatch
) -> None:
    reseller_id, tariff_id = await seed_reseller_and_tariff(session_factory)
    gateway = FakeGateway()
    monkeypatch.setattr(api_module, "make_remnawave_gateway", lambda _settings: gateway)
    configure_dependencies(session_factory)
    try:
        response = TestClient(app).post(
            "/api/reseller/keys/buy",
            json={
                "tariff_id": tariff_id,
                "product_mode": "family",
                "request_id": "family-order-001",
                "client_name": "Семья",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json()["product_mode"] == "family"
    assert len(gateway.created) == 1
    async with session_factory() as session:
        reseller = await session.get(Customer, reseller_id)
        subs = (await session.execute(select(Subscription))).scalars().all()
        batch_count = await session.scalar(select(func.count()).select_from(ResellerKeyBatch))
    assert reseller.balance_rub == 1_400
    assert len(subs) == 1
    assert subs[0].device_limit == 5
    assert subs[0].reseller_batch_id is None
    assert batch_count == 0
