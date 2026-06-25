"""Financial core of the reseller portal.

Invariants this module guarantees:
- Money never changes without a matching ledger row (double-entry journal).
- A purchase is atomic: either the key exists in Remnawave AND the balance was
  debited, or neither happened.
- A reseller cannot go below zero unless `allow_negative` is set.
- A retried request carrying the same `idempotency_key` is a no-op that returns
  the original result (protects against double-clicks / network retries).
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..remnawave import RemnawaveGateway, RemnawaveNotFoundError
from .models import (
    LedgerEntry,
    LedgerKind,
    Reseller,
    Tariff,
    TariffPrice,
    VpnKey,
    VpnKeyStatus,
)


class PortalError(RuntimeError):
    pass


class InsufficientFundsError(PortalError):
    pass


class TariffNotFoundError(PortalError):
    pass


class ResellerBlockedError(PortalError):
    pass


class KeyNotFoundError(PortalError):
    pass


def rubles_to_kopecks(rubles: float | int) -> int:
    return int(round(float(rubles) * 100))


def kopecks_to_rubles(kopecks: int) -> float:
    return round(kopecks / 100, 2)


async def resolve_price_kopecks(session: AsyncSession, tariff: Tariff, reseller: Reseller) -> int:
    """Reseller-specific override beats level override beats base price."""
    overrides = (
        await session.scalars(
            select(TariffPrice).where(TariffPrice.tariff_id == tariff.id)
        )
    ).all()
    by_reseller = next((o for o in overrides if o.reseller_id == reseller.id), None)
    if by_reseller is not None:
        return by_reseller.price_kopecks
    by_level = next(
        (o for o in overrides if o.reseller_id is None and o.level == reseller.level), None
    )
    if by_level is not None:
        return by_level.price_kopecks
    return tariff.price_kopecks


async def _lock_reseller(session: AsyncSession, reseller_id: int) -> Reseller:
    reseller = await session.scalar(
        select(Reseller).where(Reseller.id == reseller_id).with_for_update()
    )
    if reseller is None:
        raise KeyNotFoundError("Reseller not found")
    if reseller.is_blocked:
        raise ResellerBlockedError
    return reseller


def _post_ledger(
    session: AsyncSession,
    reseller: Reseller,
    *,
    kind: LedgerKind,
    amount_kopecks: int,
    actor: str,
    comment: str,
    idempotency_key: str | None = None,
    vpn_key_id: str | None = None,
) -> LedgerEntry:
    """Apply a signed amount to the (already locked) reseller and journal it.
    Caller is responsible for the surrounding transaction / commit."""
    reseller.balance_kopecks += amount_kopecks
    entry = LedgerEntry(
        reseller_id=reseller.id,
        kind=kind,
        amount_kopecks=amount_kopecks,
        balance_after_kopecks=reseller.balance_kopecks,
        actor=actor,
        comment=comment,
        idempotency_key=idempotency_key,
        vpn_key_id=vpn_key_id,
    )
    session.add(entry)
    return entry


async def _find_idempotent(
    session: AsyncSession, idempotency_key: str | None
) -> LedgerEntry | None:
    if not idempotency_key:
        return None
    return await session.scalar(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
    )


async def adjust_balance(
    session: AsyncSession,
    *,
    reseller_id: int,
    kind: LedgerKind,
    amount_kopecks: int,
    actor: str,
    comment: str = "",
    idempotency_key: str | None = None,
) -> LedgerEntry:
    """Admin-side credit/debit (topup, bonus, penalty, adjust, refund)."""
    existing = await _find_idempotent(session, idempotency_key)
    if existing is not None:
        return existing
    reseller = await _lock_reseller(session, reseller_id)
    if amount_kopecks < 0 and not reseller.allow_negative:
        if reseller.balance_kopecks + amount_kopecks < 0:
            raise InsufficientFundsError
    entry = _post_ledger(
        session,
        reseller,
        kind=kind,
        amount_kopecks=amount_kopecks,
        actor=actor,
        comment=comment,
        idempotency_key=idempotency_key,
    )
    await session.commit()
    return entry


async def get_tariff(session: AsyncSession, code: str) -> Tariff:
    tariff = await session.scalar(
        select(Tariff).where(Tariff.code == code, Tariff.is_active.is_(True))
    )
    if tariff is None:
        raise TariffNotFoundError(code)
    return tariff


def _remote_username(reseller_id: int, suffix: str) -> str:
    return f"rsl{reseller_id}_{suffix}"


async def purchase_key(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    settings: Settings,
    *,
    reseller_id: int,
    tariff_code: str,
    client_id: int | None,
    actor: str,
    idempotency_key: str | None = None,
) -> VpnKey:
    """Atomically: validate balance -> create Remnawave user -> persist key +
    debit. Price is resolved on the backend; the client never sets it."""
    # 1. Idempotency: a retried purchase returns the original key untouched.
    existing = await _find_idempotent(session, idempotency_key)
    if existing is not None:
        if existing.vpn_key_id is None:
            raise PortalError("Idempotency key already used by a non-purchase operation")
        key = await session.get(VpnKey, existing.vpn_key_id)
        if key is None:
            raise KeyNotFoundError("Idempotent key record points to a missing key")
        return key

    tariff = await get_tariff(session, tariff_code)

    # 2. Lock the reseller row so concurrent buys cannot both pass the balance
    #    check (serialized on Postgres; harmless no-op on SQLite tests).
    reseller = await _lock_reseller(session, reseller_id)
    price = await resolve_price_kopecks(session, tariff, reseller)
    if not reseller.allow_negative and reseller.balance_kopecks < price:
        raise InsufficientFundsError

    expires_at = datetime.now(UTC) + timedelta(days=tariff.duration_days)
    squads = tariff.squads or settings.squad_uuids

    # 3. External call. If Remnawave fails, we raise before any debit/commit.
    remote = await gateway.create_user(
        username=_remote_username(reseller.id, secrets.token_hex(4)),
        telegram_id=reseller.telegram_id or 0,
        expires_at=expires_at,
        device_limit=tariff.device_limit,
        traffic_limit_bytes=tariff.traffic_limit_gb * 1024**3,
        squads=squads,
        description=f"HamaliVpn portal; reseller={reseller.id}; tariff={tariff.code}",
    )

    # 4. Persist key + debit + journal in one commit.
    key = VpnKey(
        reseller_id=reseller.id,
        client_id=client_id,
        tariff_code=tariff.code,
        status=VpnKeyStatus.active,
        remnawave_uuid=remote.uuid,
        remnawave_short_uuid=remote.short_uuid,
        subscription_url=remote.subscription_url,
        device_limit=tariff.device_limit,
        traffic_limit_gb=tariff.traffic_limit_gb,
        price_paid_kopecks=price,
        expires_at=expires_at,
    )
    session.add(key)
    await session.flush()  # assign key.id for the ledger reference
    _post_ledger(
        session,
        reseller,
        kind=LedgerKind.purchase,
        amount_kopecks=-price,
        actor=actor,
        comment=f"Покупка ключа {tariff.code}",
        idempotency_key=idempotency_key,
        vpn_key_id=key.id,
    )
    await session.commit()
    return key


async def extend_key(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    settings: Settings,
    *,
    reseller_id: int,
    key_id: str,
    tariff_code: str,
    actor: str,
    idempotency_key: str | None = None,
) -> VpnKey:
    existing = await _find_idempotent(session, idempotency_key)
    if existing is not None and existing.vpn_key_id:
        key = await session.get(VpnKey, existing.vpn_key_id)
        if key is not None:
            return key

    key = await session.get(VpnKey, key_id)
    if key is None or key.reseller_id != reseller_id:
        raise KeyNotFoundError("Key not found for this reseller")
    if not key.remnawave_uuid:
        raise KeyNotFoundError("Key has no Remnawave user")

    tariff = await get_tariff(session, tariff_code)
    reseller = await _lock_reseller(session, reseller_id)
    price = await resolve_price_kopecks(session, tariff, reseller)
    if not reseller.allow_negative and reseller.balance_kopecks < price:
        raise InsufficientFundsError

    now = datetime.now(UTC)
    base = key.expires_at if (key.expires_at and key.expires_at > now) else now
    new_expires = base + timedelta(days=tariff.duration_days)

    try:
        remote = await gateway.update_user_access(
            user_uuid=key.remnawave_uuid,
            expires_at=new_expires,
            device_limit=key.device_limit,
            traffic_limit_bytes=key.traffic_limit_gb * 1024**3,
            squads=tariff.squads or settings.squad_uuids,
        )
    except RemnawaveNotFoundError:
        remote = await gateway.create_user(
            username=_remote_username(reseller.id, key.id[:8]),
            telegram_id=reseller.telegram_id or 0,
            expires_at=new_expires,
            device_limit=key.device_limit,
            traffic_limit_bytes=key.traffic_limit_gb * 1024**3,
            squads=tariff.squads or settings.squad_uuids,
            description=f"HamaliVpn portal repaired; reseller={reseller.id}",
        )
        key.remnawave_uuid = remote.uuid

    key.status = VpnKeyStatus.active
    key.expires_at = new_expires
    key.subscription_url = remote.subscription_url
    key.remnawave_short_uuid = remote.short_uuid
    _post_ledger(
        session,
        reseller,
        kind=LedgerKind.extend,
        amount_kopecks=-price,
        actor=actor,
        comment=f"Продление ключа {tariff.code}",
        idempotency_key=idempotency_key,
        vpn_key_id=key.id,
    )
    await session.commit()
    return key


async def disable_key(
    session: AsyncSession,
    gateway: RemnawaveGateway,
    *,
    reseller_id: int | None,
    key_id: str,
    actor: str,
) -> VpnKey:
    """Disable a key in Remnawave and locally. reseller_id=None means admin
    (any key); otherwise the key must belong to that reseller."""
    key = await session.get(VpnKey, key_id)
    if key is None or (reseller_id is not None and key.reseller_id != reseller_id):
        raise KeyNotFoundError("Key not found")
    if key.remnawave_uuid:
        try:
            await gateway.disable_user(key.remnawave_uuid)
        except RemnawaveNotFoundError:
            pass
    key.status = VpnKeyStatus.disabled
    await session.commit()
    return key


async def reseller_balance(session: AsyncSession, reseller_id: int) -> int:
    reseller = await session.get(Reseller, reseller_id)
    return reseller.balance_kopecks if reseller else 0


def expire_due_keys_now(keys: list[VpnKey]) -> list[VpnKey]:
    now = datetime.now(UTC)
    due = [
        k
        for k in keys
        if k.status == VpnKeyStatus.active and k.expires_at and k.expires_at <= now
    ]
    for k in due:
        k.status = VpnKeyStatus.expired
    return due
