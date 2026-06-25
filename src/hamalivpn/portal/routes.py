from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..qr import qr_data_uri
from ..remnawave import RemnawaveError, make_remnawave_gateway
from . import auth, services
from .models import (
    Client,
    LedgerEntry,
    LedgerKind,
    Reseller,
    ResellerLevel,
    SecretKey,
    SecretKeyRole,
    Tariff,
    VpnKey,
    VpnKeyStatus,
)
from .schemas import (
    AdjustRequest,
    BuyKeyRequest,
    ClientRequest,
    CreateResellerRequest,
    CreateTariffRequest,
    ExtendKeyRequest,
    IssueSecretKeyRequest,
    LoginRequest,
    TopupRequest,
    serialize_client,
    serialize_key,
    serialize_ledger,
    serialize_reseller,
    serialize_tariff,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ResellerIdentity = Annotated[auth.Identity, Depends(auth.require_reseller)]
AdminIdentity = Annotated[auth.Identity, Depends(auth.require_admin)]

api_router = APIRouter(prefix="/api")


def _portal_error(error: services.PortalError) -> HTTPException:
    mapping = {
        services.InsufficientFundsError: (status.HTTP_402_PAYMENT_REQUIRED, "Недостаточно средств"),
        services.TariffNotFoundError: (status.HTTP_404_NOT_FOUND, "Тариф не найден"),
        services.ResellerBlockedError: (status.HTTP_403_FORBIDDEN, "Реселлер заблокирован"),
        services.KeyNotFoundError: (status.HTTP_404_NOT_FOUND, "Ключ не найден"),
    }
    code, detail = mapping.get(type(error), (status.HTTP_400_BAD_REQUEST, str(error)))
    return HTTPException(status_code=code, detail=detail)


# ── auth / identity ──────────────────────────────────────────────────────────

@api_router.post("/portal/login")
async def login(request: Request, body: LoginRequest, session: SessionDep) -> dict:
    key = await auth.authenticate(session, body.key)
    if key is None:
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный ключ")
    auth.login_session(request, key)
    await session.commit()
    return {"role": str(key.role), "reseller_id": key.reseller_id}


@api_router.post("/portal/logout")
async def logout(request: Request) -> dict:
    auth.logout_session(request)
    return {"ok": True}


@api_router.get("/portal/me")
async def me(request: Request, session: SessionDep) -> dict:
    identity = auth.require_identity(request)
    payload: dict = {"role": str(identity.role)}
    if identity.reseller_id is not None:
        reseller = await session.get(Reseller, identity.reseller_id)
        if reseller is not None:
            payload["reseller"] = serialize_reseller(reseller)
    return payload


# ── reseller ─────────────────────────────────────────────────────────────────

@api_router.get("/reseller/dashboard")
async def reseller_dashboard(identity: ResellerIdentity, session: SessionDep) -> dict:
    rid = identity.reseller_id
    reseller = await auth.load_reseller(session, identity)
    now = datetime.now(UTC)
    soon = now + timedelta(days=3)

    active_keys = await session.scalar(
        select(func.count(VpnKey.id)).where(
            VpnKey.reseller_id == rid, VpnKey.status == VpnKeyStatus.active
        )
    )
    clients = await session.scalar(
        select(func.count(Client.id)).where(Client.reseller_id == rid)
    )
    expiring = await session.scalar(
        select(func.count(VpnKey.id)).where(
            VpnKey.reseller_id == rid,
            VpnKey.status == VpnKeyStatus.active,
            VpnKey.expires_at.is_not(None),
            VpnKey.expires_at <= soon,
        )
    )
    recent = (
        await session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.reseller_id == rid)
            .order_by(desc(LedgerEntry.created_at))
            .limit(10)
        )
    ).all()
    return {
        "balance_rub": services.kopecks_to_rubles(reseller.balance_kopecks),
        "active_keys": active_keys or 0,
        "clients": clients or 0,
        "expiring_soon": expiring or 0,
        "recent_operations": [serialize_ledger(e) for e in recent],
    }


@api_router.get("/reseller/tariffs")
async def reseller_tariffs(identity: ResellerIdentity, session: SessionDep) -> list[dict]:
    reseller = await auth.load_reseller(session, identity)
    tariffs = (
        await session.scalars(
            select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.sort_order)
        )
    ).all()
    result = []
    for tariff in tariffs:
        price = await services.resolve_price_kopecks(session, tariff, reseller)
        result.append(serialize_tariff(tariff, price))
    return result


@api_router.get("/reseller/keys")
async def reseller_keys(identity: ResellerIdentity, session: SessionDep) -> list[dict]:
    keys = (
        await session.scalars(
            select(VpnKey)
            .where(VpnKey.reseller_id == identity.reseller_id)
            .order_by(desc(VpnKey.created_at))
        )
    ).all()
    return [serialize_key(k) for k in keys]


@api_router.post("/reseller/keys/buy")
async def reseller_buy(
    identity: ResellerIdentity, body: BuyKeyRequest, session: SessionDep
) -> dict:
    settings = get_settings()
    gateway = make_remnawave_gateway(settings)
    if body.client_id is not None:
        client = await session.get(Client, body.client_id)
        if client is None or client.reseller_id != identity.reseller_id:
            raise HTTPException(status_code=404, detail="Клиент не найден")
    try:
        key = await services.purchase_key(
            session,
            gateway,
            settings,
            reseller_id=identity.reseller_id,
            tariff_code=body.tariff_code,
            client_id=body.client_id,
            actor=f"reseller:{identity.reseller_id}",
            idempotency_key=body.idempotency_key,
        )
    except services.PortalError as error:
        await session.rollback()
        raise _portal_error(error) from error
    except RemnawaveError as error:
        await session.rollback()
        raise HTTPException(status_code=502, detail="Remnawave недоступен — деньги не списаны") \
            from error
    return serialize_key(key)


@api_router.post("/reseller/keys/{key_id}/extend")
async def reseller_extend(
    identity: ResellerIdentity, key_id: str, body: ExtendKeyRequest, session: SessionDep
) -> dict:
    settings = get_settings()
    gateway = make_remnawave_gateway(settings)
    try:
        key = await services.extend_key(
            session,
            gateway,
            settings,
            reseller_id=identity.reseller_id,
            key_id=key_id,
            tariff_code=body.tariff_code,
            actor=f"reseller:{identity.reseller_id}",
            idempotency_key=body.idempotency_key,
        )
    except services.PortalError as error:
        await session.rollback()
        raise _portal_error(error) from error
    except RemnawaveError as error:
        await session.rollback()
        raise HTTPException(status_code=502, detail="Remnawave недоступен") from error
    return serialize_key(key)


@api_router.get("/reseller/keys/{key_id}/qr")
async def reseller_key_qr(identity: ResellerIdentity, key_id: str, session: SessionDep) -> dict:
    key = await session.get(VpnKey, key_id)
    if key is None or key.reseller_id != identity.reseller_id:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    url = key.subscription_url or ""
    return {"subscription_url": url, "qr": qr_data_uri(url) if url else None}


@api_router.post("/reseller/keys/{key_id}/disable")
async def reseller_disable(identity: ResellerIdentity, key_id: str, session: SessionDep) -> dict:
    gateway = make_remnawave_gateway(get_settings())
    try:
        key = await services.disable_key(
            session,
            gateway,
            reseller_id=identity.reseller_id,
            key_id=key_id,
            actor=f"reseller:{identity.reseller_id}",
        )
    except services.PortalError as error:
        raise _portal_error(error) from error
    return serialize_key(key)


@api_router.get("/reseller/clients")
async def list_clients(identity: ResellerIdentity, session: SessionDep) -> list[dict]:
    clients = (
        await session.scalars(
            select(Client)
            .where(Client.reseller_id == identity.reseller_id)
            .order_by(desc(Client.created_at))
        )
    ).all()
    return [serialize_client(c) for c in clients]


@api_router.post("/reseller/clients")
async def create_client(
    identity: ResellerIdentity, body: ClientRequest, session: SessionDep
) -> dict:
    client = Client(
        reseller_id=identity.reseller_id,
        name=body.name,
        phone=body.phone,
        telegram=body.telegram,
        note=body.note,
    )
    session.add(client)
    await session.commit()
    return serialize_client(client)


@api_router.get("/reseller/ledger")
async def reseller_ledger(identity: ResellerIdentity, session: SessionDep) -> list[dict]:
    entries = (
        await session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.reseller_id == identity.reseller_id)
            .order_by(desc(LedgerEntry.created_at))
            .limit(100)
        )
    ).all()
    return [serialize_ledger(e) for e in entries]


# ── admin ────────────────────────────────────────────────────────────────────

@api_router.get("/admin/resellers")
async def admin_list_resellers(identity: AdminIdentity, session: SessionDep) -> list[dict]:
    resellers = (
        await session.scalars(select(Reseller).order_by(desc(Reseller.created_at)))
    ).all()
    return [serialize_reseller(r) for r in resellers]


@api_router.post("/admin/resellers")
async def admin_create_reseller(
    identity: AdminIdentity, body: CreateResellerRequest, session: SessionDep
) -> dict:
    try:
        level = ResellerLevel(body.level)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Неизвестный уровень") from error
    reseller = Reseller(
        name=body.name,
        level=level,
        telegram_id=body.telegram_id,
        allow_negative=body.allow_negative,
    )
    session.add(reseller)
    await session.commit()
    return serialize_reseller(reseller)


@api_router.post("/admin/resellers/{reseller_id}/secret-keys")
async def admin_issue_secret_key(
    identity: AdminIdentity, reseller_id: int, body: IssueSecretKeyRequest, session: SessionDep
) -> dict:
    reseller = await session.get(Reseller, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="Реселлер не найден")
    token = auth.generate_secret_key()
    session.add(
        SecretKey(
            role=SecretKeyRole.reseller,
            reseller_id=reseller_id,
            key_prefix=auth.key_prefix(token),
            key_hash=auth.hash_secret_key(token),
            label=body.label,
        )
    )
    await session.commit()
    # Plaintext token is returned exactly once; only its hash is stored.
    return {"secret_key": token, "warning": "Показывается один раз — сохраните"}


@api_router.post("/admin/resellers/{reseller_id}/topup")
async def admin_topup(
    identity: AdminIdentity, reseller_id: int, body: TopupRequest, session: SessionDep
) -> dict:
    try:
        entry = await services.adjust_balance(
            session,
            reseller_id=reseller_id,
            kind=LedgerKind.topup,
            amount_kopecks=services.rubles_to_kopecks(body.amount_rub),
            actor=f"admin:{identity.secret_key_id}",
            comment=body.comment or "Пополнение баланса",
            idempotency_key=body.idempotency_key,
        )
    except services.PortalError as error:
        await session.rollback()
        raise _portal_error(error) from error
    return serialize_ledger(entry)


@api_router.post("/admin/resellers/{reseller_id}/adjust")
async def admin_adjust(
    identity: AdminIdentity, reseller_id: int, body: AdjustRequest, session: SessionDep
) -> dict:
    try:
        kind = LedgerKind(body.kind)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Неизвестный тип операции") from error
    try:
        entry = await services.adjust_balance(
            session,
            reseller_id=reseller_id,
            kind=kind,
            amount_kopecks=services.rubles_to_kopecks(body.amount_rub),
            actor=f"admin:{identity.secret_key_id}",
            comment=body.comment,
            idempotency_key=body.idempotency_key,
        )
    except services.PortalError as error:
        await session.rollback()
        raise _portal_error(error) from error
    return serialize_ledger(entry)


@api_router.post("/admin/resellers/{reseller_id}/block")
async def admin_block(
    identity: AdminIdentity, reseller_id: int, session: SessionDep, blocked: bool = True
) -> dict:
    reseller = await session.get(Reseller, reseller_id)
    if reseller is None:
        raise HTTPException(status_code=404, detail="Реселлер не найден")
    reseller.is_blocked = blocked
    await session.commit()
    return serialize_reseller(reseller)


@api_router.get("/admin/tariffs")
async def admin_list_tariffs(identity: AdminIdentity, session: SessionDep) -> list[dict]:
    tariffs = (await session.scalars(select(Tariff).order_by(Tariff.sort_order))).all()
    return [serialize_tariff(t) for t in tariffs]


@api_router.post("/admin/tariffs")
async def admin_create_tariff(
    identity: AdminIdentity, body: CreateTariffRequest, session: SessionDep
) -> dict:
    existing = await session.scalar(select(Tariff).where(Tariff.code == body.code))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Тариф с таким кодом уже есть")
    tariff = Tariff(
        code=body.code,
        name=body.name,
        duration_days=body.duration_days,
        price_kopecks=services.rubles_to_kopecks(body.price_rub),
        device_limit=body.device_limit,
        traffic_limit_gb=body.traffic_limit_gb,
        squad_uuids=body.squad_uuids,
        sort_order=body.sort_order,
    )
    session.add(tariff)
    await session.commit()
    return serialize_tariff(tariff)


@api_router.get("/admin/keys")
async def admin_list_keys(identity: AdminIdentity, session: SessionDep) -> list[dict]:
    keys = (
        await session.scalars(select(VpnKey).order_by(desc(VpnKey.created_at)).limit(200))
    ).all()
    return [serialize_key(k) for k in keys]


@api_router.post("/admin/keys/{key_id}/disable")
async def admin_disable_key(identity: AdminIdentity, key_id: str, session: SessionDep) -> dict:
    gateway = make_remnawave_gateway(get_settings())
    try:
        key = await services.disable_key(
            session, gateway, reseller_id=None, key_id=key_id,
            actor=f"admin:{identity.secret_key_id}",
        )
    except services.PortalError as error:
        raise _portal_error(error) from error
    return serialize_key(key)
