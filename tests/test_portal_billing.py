import pytest
from sqlalchemy import func, select

from hamalivpn.config import Settings
from hamalivpn.portal import services
from hamalivpn.portal.models import (
    LedgerEntry,
    LedgerKind,
    Reseller,
    ResellerLevel,
    Tariff,
    TariffPrice,
    VpnKeyStatus,
)
from hamalivpn.remnawave import MockRemnawaveClient


def make_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        public_base_url="https://vpn.example.com",
        remnawave_mock=True,
    )


async def _seed(session, *, balance_rub=0, level=ResellerLevel.start):
    reseller = Reseller(name="R", level=level, balance_kopecks=int(balance_rub * 100))
    tariff = Tariff(code="m1", name="1 мес", duration_days=30, price_kopecks=10000, device_limit=1)
    session.add_all([reseller, tariff])
    await session.commit()
    return reseller, tariff


@pytest.mark.asyncio
async def test_purchase_debits_balance_and_journals(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        reseller, _ = await _seed(session, balance_rub=500)

        key = await services.purchase_key(
            session,
            gateway,
            settings,
            reseller_id=reseller.id,
            tariff_code="m1",
            client_id=None,
            actor="test",
        )
        assert key.status == VpnKeyStatus.active
        assert key.remnawave_uuid is not None

        await session.refresh(reseller)
        assert reseller.balance_kopecks == 40000  # 500 - 100 rub

        entries = (await session.scalars(select(LedgerEntry))).all()
        assert len(entries) == 1
        assert entries[0].kind == LedgerKind.purchase
        assert entries[0].amount_kopecks == -10000
        assert entries[0].balance_after_kopecks == 40000
        assert entries[0].vpn_key_id == key.id


@pytest.mark.asyncio
async def test_purchase_blocked_on_insufficient_funds(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        reseller, _ = await _seed(session, balance_rub=50)  # tariff costs 100

        with pytest.raises(services.InsufficientFundsError):
            await services.purchase_key(
                session, gateway, settings,
                reseller_id=reseller.id, tariff_code="m1", client_id=None, actor="test",
            )
        await session.rollback()
        await session.refresh(reseller)
        assert reseller.balance_kopecks == 5000  # unchanged
        count = await session.scalar(select(func.count(LedgerEntry.id)))
        assert count == 0


@pytest.mark.asyncio
async def test_purchase_is_idempotent(session_factory) -> None:
    settings = make_settings()
    gateway = MockRemnawaveClient(settings)
    async with session_factory() as session:
        reseller, _ = await _seed(session, balance_rub=500)

        first = await services.purchase_key(
            session, gateway, settings,
            reseller_id=reseller.id, tariff_code="m1", client_id=None,
            actor="test", idempotency_key="abc-123",
        )
        second = await services.purchase_key(
            session, gateway, settings,
            reseller_id=reseller.id, tariff_code="m1", client_id=None,
            actor="test", idempotency_key="abc-123",
        )
        assert first.id == second.id

        await session.refresh(reseller)
        assert reseller.balance_kopecks == 40000  # debited exactly once
        count = await session.scalar(select(func.count(LedgerEntry.id)))
        assert count == 1


@pytest.mark.asyncio
async def test_topup_credits_and_journals(session_factory) -> None:
    make_settings()
    async with session_factory() as session:
        reseller, _ = await _seed(session, balance_rub=0)

        await services.adjust_balance(
            session,
            reseller_id=reseller.id,
            kind=LedgerKind.topup,
            amount_kopecks=services.rubles_to_kopecks(1500),
            actor="admin",
            comment="manual",
        )
        await session.refresh(reseller)
        assert reseller.balance_kopecks == 150000
        entry = await session.scalar(select(LedgerEntry))
        assert entry.kind == LedgerKind.topup
        assert entry.balance_after_kopecks == 150000


@pytest.mark.asyncio
async def test_reseller_specific_price_override_wins(session_factory) -> None:
    async with session_factory() as session:
        reseller, tariff = await _seed(session, balance_rub=0, level=ResellerLevel.vip)
        session.add_all([
            TariffPrice(tariff_id=tariff.id, level=ResellerLevel.vip, price_kopecks=8000),
            TariffPrice(tariff_id=tariff.id, reseller_id=reseller.id, price_kopecks=7000),
        ])
        await session.commit()

        price = await services.resolve_price_kopecks(session, tariff, reseller)
        assert price == 7000  # reseller override beats level override beats base
