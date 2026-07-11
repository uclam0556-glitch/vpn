import os
import hmac
import hashlib
import logging
import time
from urllib.parse import parse_qsl
from fastapi import BackgroundTasks, FastAPI, Depends, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, or_, select
from .config import get_settings
from .db import get_session
from .models import Customer, PaymentStatus, PaymentTransaction
import json
import secrets

settings = get_settings()
_docs_enabled = settings.debug and not settings.is_production

app = FastAPI(
    title="HamaliVPN TWA API",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.hamali.ru"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def redirect_legacy_portal_host(request: Request, call_next):
    """Keep app.hamali.ru as the only public portal entry point.

    portal.hamali.ru used to be exposed directly to the origin VPS. That works
    from some networks, but in Russia/Caucasus direct OVH routes can fail while
    the Cloudflare-proxied app.hamali.ru stays reachable. Preserve the path and
    query so old client/admin links continue to work after DNS is proxied.
    """
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    if host == "portal.hamali.ru":
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(
            url=f"https://app.hamali.ru{request.url.path}{query}",
            status_code=308,
        )
    return await call_next(request)


@app.middleware("http")
async def portal_cache_policy(request: Request, call_next):
    """Avoid stale portal/connect pages on mobile clients and VPN networks.

    The public portal is often opened inside in-app browsers and through
    unstable VPN paths. Dynamic HTML/API must never be cached by Cloudflare or
    a browser, while versioned assets may be cached shortly.
    """
    response = await call_next(request)
    path = request.url.path

    if (
        path == "/"
        or path.startswith("/api/")
        or path.startswith("/portal")
        or path.startswith("/connect/")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "public, max-age=900")

    return response


if not _docs_enabled:
    @app.get("/docs", include_in_schema=False)
    @app.get("/redoc", include_in_schema=False)
    @app.get("/openapi.json", include_in_schema=False)
    async def disabled_api_docs() -> PlainTextResponse:
        return PlainTextResponse("Not found", status_code=404)


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, str]:
    """Cheap liveness check for Caddy/Cloudflare/monitoring.

    Keep this route in the portal API too. The Docker control app already has
    /api/health, but portal.hamali.ru/app.hamali.ru are served by this host
    process on :8001. Without an explicit route here, monitors get redirected
    to /portal and cannot distinguish "API is alive" from "SPA fallback works".
    """
    return {
        "status": "ok",
        "app": "hamalivpn-portal",
        "environment": settings.environment,
    }

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

def validate_telegram_data(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="No initData provided")
    
    try:
        parsed_data = dict(parse_qsl(init_data))
        hash_val = parsed_data.pop('hash', None)
        if not hash_val:
            raise HTTPException(status_code=401, detail="Invalid initData")
            
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != hash_val:
            raise HTTPException(status_code=401, detail="Invalid hash")
            
        return json.loads(parsed_data.get('user', '{}'))
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
security = HTTPBearer()

# In-memory brute-force throttle for portal-key auth (single uvicorn process).
_AUTH_FAILS: dict[str, tuple[int, float]] = {}
_AUTH_MAX_FAILS = 15
_AUTH_WINDOW = 300.0


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else "?")
    )


def _auth_allowed(ip: str) -> bool:
    count, first = _AUTH_FAILS.get(ip, (0, time.time()))
    if time.time() - first > _AUTH_WINDOW:
        return True
    return count < _AUTH_MAX_FAILS


def _auth_record_fail(ip: str) -> None:
    now = time.time()
    count, first = _AUTH_FAILS.get(ip, (0, now))
    if now - first > _AUTH_WINDOW:
        count, first = 0, now
    _AUTH_FAILS[ip] = (count + 1, first)


async def get_portal_user(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security), db: AsyncSession = Depends(get_session)):
    ip = _client_ip(request)
    if not _auth_allowed(ip):
        raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже.")
    key = credentials.credentials
    customer = (await db.execute(select(Customer).filter_by(portal_access_key=key))).scalars().first()
    if not customer or not key:
        _auth_record_fail(ip)
        raise HTTPException(status_code=401, detail="Invalid access key")
    if customer.is_blocked and customer.role != "super_admin":
        _auth_record_fail(ip)
        raise HTTPException(status_code=403, detail="Доступ заблокирован. Обратитесь к администратору.")
    return {
        "id": customer.telegram_id,
        "db_id": customer.id,
        "role": customer.role,
        "is_blocked": customer.is_blocked,
    }

class SetKeyRequest(BaseModel):
    key: str | None = None


@app.post("/api/admin/resellers/{reseller_id}/key")
async def generate_reseller_key(reseller_id: int, req: SetKeyRequest = SetKeyRequest(), user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    customer = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not customer or customer.role != "super_admin":
        raise HTTPException(403, "Not an admin")

    reseller = await db.get(Customer, reseller_id)
    if not reseller:
        raise HTTPException(404, "Reseller not found")

    custom = (req.key or "").strip()
    if custom:
        if len(custom) < 6:
            raise HTTPException(400, "Ключ слишком короткий (минимум 6 символов)")
        clash = (await db.execute(select(Customer).filter_by(portal_access_key=custom))).scalars().first()
        if clash and clash.id != reseller.id:
            raise HTTPException(409, "Такой ключ уже используется другим пользователем")
        new_key = custom
    else:
        new_key = secrets.token_urlsafe(32)

    reseller.portal_access_key = new_key
    await _audit(
        db,
        customer,
        "admin.reseller.key.updated",
        "customer",
        reseller.id,
        {"reseller_name": reseller.full_name, "custom_key": bool(custom)},
    )
    await db.commit()
    return {"status": "ok", "portal_access_key": new_key}



def get_current_user(x_telegram_init_data: str = Header(None)):
    user_data = validate_telegram_data(x_telegram_init_data)
    if not user_data or 'id' not in user_data:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user_data

from sqlalchemy.orm import selectinload

@app.get("/api/user/profile")
async def get_user_profile(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    result = await db.execute(select(Customer).options(selectinload(Customer.subscriptions)).filter(Customer.telegram_id == tg_id))
    customer = result.scalars().first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="User not found")
        
    active_sub = next((s for s in customer.subscriptions if str(s.status).split('.')[-1] == "active"), None)
    if not active_sub and customer.subscriptions:
        active_sub = customer.subscriptions[0]
        
    uuid_str = active_sub.remnawave_uuid if active_sub else ""
    status_str = str(active_sub.status).split('.')[-1] if active_sub else "inactive"
        
    return {
        "telegram_id": customer.telegram_id,
        "uuid": str(uuid_str),
        "status": status_str,
        "trial_used": customer.trial_used,
        "balance": float(customer.balance_rub),
        "referral_code": str(customer.telegram_id),
        "referred_by": customer.referrer_id,
        "role": customer.role,
        "reseller_level": customer.reseller_level
    }

@app.get("/api/user/referrals")
async def get_referral_stats(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    result = await db.execute(select(Customer).filter(Customer.telegram_id == tg_id))
    customer = result.scalars().first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="User not found")
        
    invited_result = await db.execute(select(Customer).filter(Customer.referrer_id == customer.id))
    invited = len(invited_result.scalars().all())
    
    return {
        "referral_code": str(customer.telegram_id),
        "total_invited": invited,
        "bonus_earned": 0
    }

import httpx
import base64
import urllib.parse

@app.get("/api/user/usage")
async def get_user_usage(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    result = await db.execute(select(Customer).options(selectinload(Customer.subscriptions)).filter(Customer.telegram_id == tg_id))
    customer = result.scalars().first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="User not found")
        
    active_sub = next((s for s in customer.subscriptions if str(s.status).split('.')[-1] == "active"), None)
    if not active_sub and customer.subscriptions:
        active_sub = customer.subscriptions[0]
        
    if not active_sub or not active_sub.remnawave_uuid:
        return {"used_bytes": 0, "limit_bytes": 0, "expire_at": None, "error": "No active sub"}
        
    settings = get_settings()
    token = settings.remnawave_api_token.get_secret_value()
    base_url = settings.panel_base_url.rstrip("/")
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/api/users/{active_sub.remnawave_uuid}",
                headers={"Authorization": f"Bearer {token}"}
            )
            if resp.status_code == 200:
                data = resp.json()
                if "response" in data:
                    data = data["response"]
                used = data.get("trafficUsedBytes", 0)
                limit = data.get("trafficLimitBytes", 0)
                return {
                    "used_bytes": used,
                    "limit_bytes": limit,
                    "expire_at": data.get("expireAt")
                }
            else:
                return {"used_bytes": 0, "limit_bytes": 0, "expire_at": None, "error": resp.status_code}
    except Exception as e:
        return {"used_bytes": 0, "limit_bytes": 0, "expire_at": None, "error": str(e)}

@app.get("/api/user/servers")
async def get_user_servers(request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    result = await db.execute(select(Customer).options(selectinload(Customer.subscriptions)).filter(Customer.telegram_id == tg_id))
    customer = result.scalars().first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="User not found")
        
    active_sub = next((s for s in customer.subscriptions if str(s.status).split('.')[-1] == "active"), None)
    if not active_sub and customer.subscriptions:
        active_sub = customer.subscriptions[0]
        
    if not active_sub or not active_sub.remnawave_uuid:
        return {"servers": [], "error": "No active sub"}
        
    try:
        client_ip = request.headers.get(
            "x-forwarded-for",
            request.client.host if request.client else "127.0.0.1",
        ).split(",")[0].strip()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"http://127.0.0.1:8000/api/sub/{active_sub.remnawave_uuid}",
                headers={"User-Agent": "HamaliVPN-WebApp", "X-Forwarded-For": client_ip},
            )
            if resp.status_code == 200:
                decoded = base64.b64decode(resp.content).decode('utf-8')
                links = [l for l in decoded.split('\n') if l.strip()]
                servers = []
                for link in links:
                    try:
                        dec_link = urllib.parse.unquote(link)
                        name = dec_link.split('#')[1] if '#' in dec_link else "Unknown Node"
                        proto = link.split('://')[0].lower() if '://' in link else "vless"
                        
                        protocol_name = "VLESS"
                        if "hy2" in proto or "hysteria2" in proto:
                            protocol_name = "Hysteria2"
                            
                        servers.append({
                            "raw_name": name,
                            "protocol": protocol_name,
                            "link": link
                        })
                    except:
                        pass
                return {"servers": servers}
            else:
                return {"servers": []}
    except Exception as e:
        return {"servers": [], "error": str(e)}

from sqlalchemy import desc
from datetime import timedelta
from .deeplinks import (
    happ_deeplink,
    hiddify_deeplink,
    incy_deeplink,
    streisand_deeplink,
    v2raytun_deeplink,
)
from .device_limits import prune_hwid_devices_to_limit
from .device_slots import (
    DeviceLimitReached,
    active_device_slots,
    active_device_slot_count,
    deactivate_device_slot,
    deactivate_subscription_slots,
    device_subscription_url,
    ensure_device_slot,
    sync_subscription_device_slots,
)
from .models import (
    AuditLog,
    BalanceTransaction,
    Tariff,
    Subscription,
    SubscriptionDevice,
    SubscriptionStatus,
    as_utc,
    utcnow,
)
from .qr import qr_data_uri
from .remnawave import make_remnawave_gateway
from .services import (
    get_subscription_by_token,
    subscription_connect_url,
    subscription_short_code,
)

class BuyKeyRequest(BaseModel):
    tariff_id: int
    client_name: str = ""
    client_phone: str = ""
    client_telegram: str = ""
    note: str = ""


async def _portal_customer(user: dict, db: AsyncSession, *, lock: bool = False) -> Customer:
    stmt = select(Customer).where(Customer.telegram_id == user["id"])
    if lock:
        stmt = stmt.with_for_update()
    customer = (await db.execute(stmt)).scalars().first()
    if not customer:
        raise HTTPException(401, "Portal user not found")
    if customer.is_blocked and customer.role != "super_admin":
        raise HTTPException(403, "Доступ заблокирован")
    return customer


async def _reseller_or_admin(user: dict, db: AsyncSession, *, lock: bool = False) -> Customer:
    customer = await _portal_customer(user, db, lock=lock)
    if customer.role not in ["reseller", "super_admin"]:
        raise HTTPException(403, "Not a reseller")
    return customer


async def _admin_or_403(user: dict, db: AsyncSession) -> Customer:
    customer = await _portal_customer(user, db)
    if customer.role != "super_admin":
        raise HTTPException(403, "Not an admin")
    return customer


def _actor(customer: Customer) -> str:
    return f"{customer.role}:{customer.id}:tg{customer.telegram_id}"


def _safe_details(details: dict | None) -> dict:
    if not details:
        return {}
    blocked_keys = {"portal_access_key", "key", "token", "password", "secret", "sub_url"}
    safe: dict = {}
    for key, value in details.items():
        if key in blocked_keys:
            continue
        safe[key] = value
    return safe


async def _audit(
    db: AsyncSession,
    actor: Customer,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor=_actor(actor),
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=_safe_details(details),
        )
    )


@app.get("/api/portal/me")
async def get_portal_me(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    customer = await _portal_customer(user, db)
    return {
        "role": customer.role,
        "name": customer.full_name,
        "level": customer.reseller_level,
        "is_blocked": customer.is_blocked,
    }

@app.get("/api/reseller/dashboard")
async def get_reseller_dashboard(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    customer = await _reseller_or_admin(user, db)
    
    clients_count = (await db.execute(select(Customer).filter_by(referrer_id=customer.id))).scalars().all()
    txs = (await db.execute(select(BalanceTransaction).filter_by(customer_id=customer.id).order_by(desc(BalanceTransaction.created_at)).limit(10))).scalars().all()
    
    return {
        "balance": float(customer.balance_rub),
        "clients_count": len(clients_count),
        "transactions": [{"id": t.id, "amount": t.amount, "type": t.type, "desc": t.description, "date": t.created_at.isoformat()} for t in txs]
    }

@app.get("/api/reseller/clients")
async def get_reseller_clients(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    customer = await _reseller_or_admin(user, db)
        
    clients = (await db.execute(select(Customer).options(selectinload(Customer.subscriptions)).filter_by(referrer_id=customer.id).order_by(desc(Customer.created_at)))).scalars().all()

    res = []
    for c in clients:
        sub = next((s for s in c.subscriptions if str(s.status).split('.')[-1] == "active"), None)
        if not sub and c.subscriptions: sub = c.subscriptions[0]
        res.append({
            "id": c.id,
            "name": c.full_name,
            "telegram_id": c.telegram_id,
            "sub_status": str(sub.status).split('.')[-1] if sub else "none",
            "expires_at": sub.expires_at.isoformat() if sub and sub.expires_at else None,
            "sub_url": sub.subscription_url if sub else None,
            "connect_url": subscription_connect_url(get_settings(), sub) if sub and sub.access_token else None,
            "short_code": subscription_short_code(sub) if sub and sub.access_token else None,
            "remnawave_uuid": sub.remnawave_uuid if sub else None,
            "device_limit": sub.device_limit if sub else 0
        })
    return res

@app.get("/api/reseller/tariffs")
async def get_tariffs(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    await _reseller_or_admin(user, db)
    
    tariffs = (await db.execute(select(Tariff).filter_by(is_active=True))).scalars().all()
    return [{"id": t.id, "name": t.name, "duration_days": t.duration_days, "price_rub": t.price_rub, "device_limit": t.device_limit, "traffic_limit_gb": t.traffic_limit_gb} for t in tariffs]

@app.post("/api/reseller/keys/buy")
async def buy_key(req: BuyKeyRequest, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    customer = await _reseller_or_admin(user, db, lock=True)
        
    tariff = await db.get(Tariff, req.tariff_id)
    if not tariff or not tariff.is_active:
        raise HTTPException(404, "Tariff not found")
        
    if customer.balance_rub < tariff.price_rub:
        raise HTTPException(400, "Insufficient funds")
        
    from datetime import datetime, UTC
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=tariff.duration_days)
    
    # Create client Customer
    fake_tg_id = int(secrets.token_hex(6), 16) # random id since they don't auth via tg yet
    new_client = Customer(
        telegram_id=fake_tg_id,
        full_name=req.client_name or f"Client of {customer.id}",
        telegram_username=req.client_telegram,
        referrer_id=customer.id
    )
    db.add(new_client)
    await db.flush()
    
    # Call Remnawave
    settings = get_settings()
    gateway = make_remnawave_gateway(settings)
    try:
        remote_user = await gateway.create_user(
            username=f"hamali_{new_client.id}_{secrets.token_hex(2)}",
            telegram_id=fake_tg_id,
            expires_at=expires_at,
            device_limit=tariff.device_limit,
            traffic_limit_bytes=tariff.traffic_limit_gb * 1024**3,
            squads=settings.squad_uuids,
            description=req.note
        )
    except Exception as e:
        raise HTTPException(500, f"Remnawave Error: {str(e)}")
        
    # Create Sub
    access_token = secrets.token_urlsafe(32)
    sub = Subscription(
        customer_id=new_client.id,
        plan_code=tariff.name,
        status=SubscriptionStatus.active,
        remnawave_uuid=remote_user.uuid,
        remnawave_short_uuid=remote_user.short_uuid,
        subscription_url=remote_user.subscription_url,
        access_token=access_token,
        device_limit=tariff.device_limit,
        traffic_limit_gb=tariff.traffic_limit_gb,
        expires_at=expires_at
    )
    db.add(sub)

    # Deduct balance
    customer.balance_rub -= tariff.price_rub

    # Ledger
    tx = BalanceTransaction(
        customer_id=customer.id,
        amount=-tariff.price_rub,
        type="purchase",
        description=f"Purchased {tariff.name} for {new_client.full_name}"
    )
    db.add(tx)
    await _audit(
        db,
        customer,
        "reseller.key.created",
        "subscription",
        sub.id,
        {
            "client_id": new_client.id,
            "client_name": new_client.full_name,
            "tariff_id": tariff.id,
            "tariff_name": tariff.name,
            "amount": -tariff.price_rub,
            "device_limit": tariff.device_limit,
            "expires_at": expires_at.isoformat(),
        },
    )

    await db.commit()
    connect_url = subscription_connect_url(settings, sub)
    return {
        "status": "ok",
        "client_id": new_client.id,
        "sub_url": sub.subscription_url,
        "connect_url": connect_url,
        "short_code": subscription_short_code(sub),
    }

class AdminTopupRequest(BaseModel):
    amount: int

@app.get("/api/admin/resellers")
async def get_all_resellers(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    customer = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not customer or customer.role != "super_admin":
        raise HTTPException(403, "Not an admin")
        
    resellers = (await db.execute(select(Customer).filter(Customer.role.in_(["reseller", "super_admin"])))).scalars().all()
    return [{"id": r.id, "telegram_id": r.telegram_id, "name": r.full_name, "balance": r.balance_rub, "level": r.reseller_level, "is_blocked": r.is_blocked, "portal_access_key": r.portal_access_key} for r in resellers]

@app.post("/api/admin/resellers/{reseller_id}/topup")
async def topup_reseller(reseller_id: int, req: AdminTopupRequest, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    customer = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not customer or customer.role != "super_admin":
        raise HTTPException(403, "Not an admin")
        
    reseller = await db.get(Customer, reseller_id)
    if not reseller:
        raise HTTPException(404, "Reseller not found")
        
    reseller.balance_rub += req.amount

    tx = BalanceTransaction(
        customer_id=reseller.id,
        amount=req.amount,
        type="topup",
        description=f"Manual topup by admin {customer.id}"
    )
    db.add(tx)
    await _audit(db, customer, "admin.reseller.balance.topped_up", "customer", reseller.id, {
        "amount": req.amount,
        "new_balance": reseller.balance_rub,
    })
    await db.commit()
    return {"status": "ok", "new_balance": reseller.balance_rub}


class CreateResellerRequest(BaseModel):
    name: str
    telegram_id: int | None = None
    level: int = 1

@app.post("/api/admin/resellers")
async def create_reseller(req: CreateResellerRequest, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    admin = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not admin or admin.role != "super_admin":
        raise HTTPException(403, "Not an admin")

    new_key = secrets.token_urlsafe(24)
    customer = None
    if req.telegram_id:
        customer = (await db.execute(select(Customer).filter_by(telegram_id=req.telegram_id))).scalars().first()
    if customer:
        if req.name:
            customer.full_name = req.name
    else:
        # No real Telegram id yet -> synthetic id well above the real id range.
        new_tg = req.telegram_id or int(secrets.token_hex(6), 16)
        customer = Customer(telegram_id=new_tg, full_name=req.name or "Реселлер")
        db.add(customer)
    customer.role = "reseller"
    customer.reseller_level = req.level or 1
    customer.portal_access_key = new_key
    await _audit(db, admin, "admin.reseller.created", "customer", customer.id, {
        "name": customer.full_name,
        "telegram_id": customer.telegram_id,
        "level": customer.reseller_level,
    })
    await db.commit()
    return {"status": "ok", "id": customer.id, "name": customer.full_name, "portal_access_key": new_key}


# ── Admin: полное управление из панели ───────────────────────────────────────

async def _admin_or_403(user: dict, db: AsyncSession) -> Customer:
    c = await _portal_customer(user, db)
    if c.role != "super_admin":
        raise HTTPException(403, "Not an admin")
    return c


@app.get("/api/admin/dashboard")
async def admin_dashboard(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    await _admin_or_403(user, db)
    resellers = (await db.execute(
        select(func.count()).select_from(Customer).where(Customer.role.in_(["reseller", "super_admin"]))
    )).scalar() or 0
    clients = (await db.execute(
        select(func.count()).select_from(Customer).where(Customer.referrer_id.is_not(None))
    )).scalar() or 0
    active_subs = (await db.execute(
        select(func.count()).select_from(Subscription).where(Subscription.status == SubscriptionStatus.active)
    )).scalar() or 0
    revenue = (await db.execute(
        select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(PaymentTransaction.status == PaymentStatus.paid)
    )).scalar() or 0
    reseller_balance = (await db.execute(
        select(func.coalesce(func.sum(Customer.balance_rub), 0)).where(Customer.role.in_(["reseller", "super_admin"]))
    )).scalar() or 0
    recent = (await db.execute(
        select(PaymentTransaction).where(PaymentTransaction.status == PaymentStatus.paid)
        .order_by(desc(PaymentTransaction.created_at)).limit(8)
    )).scalars().all()
    return {
        "resellers": resellers,
        "clients": clients,
        "active_subs": active_subs,
        "revenue_rub": int(revenue),
        "reseller_balance_rub": int(reseller_balance),
        "recent_payments": [
            {"amount": t.amount, "provider": t.provider, "payload": t.payload,
             "date": t.created_at.isoformat() if t.created_at else None}
            for t in recent
        ],
    }


@app.get("/api/admin/audit")
async def admin_audit_log(
    limit: int = 120,
    user: dict = Depends(get_portal_user),
    db: AsyncSession = Depends(get_session),
):
    await _admin_or_403(user, db)
    limit = max(20, min(limit, 300))
    rows = (
        await db.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit))
    ).scalars().all()
    return [
        {
            "id": row.id,
            "actor": row.actor,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "details": row.details or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


class TariffIn(BaseModel):
    name: str
    duration_days: int
    price_rub: int
    device_limit: int = 1
    traffic_limit_gb: int = 0
    is_active: bool = True


@app.get("/api/admin/tariffs")
async def admin_list_tariffs(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    await _admin_or_403(user, db)
    rows = (await db.execute(select(Tariff).order_by(Tariff.price_rub))).scalars().all()
    return [
        {"id": t.id, "name": t.name, "duration_days": t.duration_days, "price_rub": t.price_rub,
         "device_limit": t.device_limit, "traffic_limit_gb": t.traffic_limit_gb, "is_active": t.is_active}
        for t in rows
    ]


@app.post("/api/admin/tariffs")
async def admin_create_tariff(req: TariffIn, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    admin = await _admin_or_403(user, db)
    t = Tariff(name=req.name, duration_days=req.duration_days, price_rub=req.price_rub,
               device_limit=req.device_limit, traffic_limit_gb=req.traffic_limit_gb, is_active=req.is_active)
    db.add(t)
    await db.flush()
    await _audit(db, admin, "admin.tariff.created", "tariff", t.id, {
        "name": t.name,
        "duration_days": t.duration_days,
        "price_rub": t.price_rub,
        "device_limit": t.device_limit,
        "traffic_limit_gb": t.traffic_limit_gb,
        "is_active": t.is_active,
    })
    await db.commit()
    return {"status": "ok", "id": t.id}


@app.patch("/api/admin/tariffs/{tid}")
async def admin_edit_tariff(tid: int, req: TariffIn, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    admin = await _admin_or_403(user, db)
    t = await db.get(Tariff, tid)
    if not t:
        raise HTTPException(404, "Tariff not found")
    before = {
        "name": t.name,
        "duration_days": t.duration_days,
        "price_rub": t.price_rub,
        "device_limit": t.device_limit,
        "traffic_limit_gb": t.traffic_limit_gb,
        "is_active": t.is_active,
    }
    t.name, t.duration_days, t.price_rub = req.name, req.duration_days, req.price_rub
    t.device_limit, t.traffic_limit_gb, t.is_active = req.device_limit, req.traffic_limit_gb, req.is_active
    await _audit(db, admin, "admin.tariff.updated", "tariff", t.id, {
        "before": before,
        "after": {
            "name": t.name,
            "duration_days": t.duration_days,
            "price_rub": t.price_rub,
            "device_limit": t.device_limit,
            "traffic_limit_gb": t.traffic_limit_gb,
            "is_active": t.is_active,
        },
    })
    await db.commit()
    return {"status": "ok"}


@app.delete("/api/admin/tariffs/{tid}")
async def admin_delete_tariff(tid: int, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    admin = await _admin_or_403(user, db)
    t = await db.get(Tariff, tid)
    if t:
        t.is_active = False
        await _audit(db, admin, "admin.tariff.disabled", "tariff", t.id, {"name": t.name})
        await db.commit()
    return {"status": "ok"}


class BlockIn(BaseModel):
    blocked: bool


class LevelIn(BaseModel):
    level: int


class BalanceAdjustIn(BaseModel):
    amount: int
    comment: str = ""


@app.post("/api/admin/resellers/{rid}/block")
async def admin_block_reseller(rid: int, req: BlockIn, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    admin = await _admin_or_403(user, db)
    r = await db.get(Customer, rid)
    if not r:
        raise HTTPException(404, "Reseller not found")
    r.is_blocked = req.blocked
    await _audit(db, admin, "admin.reseller.blocked" if req.blocked else "admin.reseller.unblocked", "customer", r.id, {
        "reseller_name": r.full_name,
        "telegram_id": r.telegram_id,
    })
    await db.commit()
    return {"status": "ok", "is_blocked": r.is_blocked}


@app.post("/api/admin/resellers/{rid}/level")
async def admin_set_level(rid: int, req: LevelIn, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    admin = await _admin_or_403(user, db)
    r = await db.get(Customer, rid)
    if not r:
        raise HTTPException(404, "Reseller not found")
    old_level = r.reseller_level
    r.reseller_level = req.level
    await _audit(db, admin, "admin.reseller.level.updated", "customer", r.id, {
        "old_level": old_level,
        "new_level": r.reseller_level,
    })
    await db.commit()
    return {"status": "ok", "level": r.reseller_level}


@app.post("/api/admin/resellers/{rid}/balance")
async def admin_adjust_balance(rid: int, req: BalanceAdjustIn, admin_user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    admin = await _admin_or_403(admin_user, db)
    r = await db.get(Customer, rid)
    if not r:
        raise HTTPException(404, "Reseller not found")
    r.balance_rub += req.amount
    db.add(BalanceTransaction(
        customer_id=r.id, amount=req.amount, type="adjust",
        description=req.comment or f"Корректировка админом {admin.id}",
    ))
    await _audit(db, admin, "admin.reseller.balance.adjusted", "customer", r.id, {
        "amount": req.amount,
        "new_balance": r.balance_rub,
        "comment": req.comment,
    })
    await db.commit()
    return {"status": "ok", "new_balance": r.balance_rub}


@app.get("/api/admin/keys")
async def admin_all_keys(q: str = "", user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    await _admin_or_403(user, db)
    rows = (await db.execute(
        select(Subscription).options(selectinload(Subscription.customer))
        .order_by(desc(Subscription.created_at)).limit(300)
    )).scalars().all()
    res = []
    for s in rows:
        cust = s.customer
        name = (cust.full_name if cust else "") or ""
        telegram_id = cust.telegram_id if cust else None
        reseller_id = cust.referrer_id if cust else None
        short_code = subscription_short_code(s) if s.access_token else ""
        if (
            q
            and q.lower() not in name.lower()
            and q not in str(telegram_id or "")
            and q not in short_code
        ):
            continue
        res.append({
            "uuid": s.remnawave_uuid,
            "id": cust.id if cust else None,
            "client": name,
            "name": name,
            "telegram_id": telegram_id,
            "status": str(s.status).split(".")[-1],
            "sub_status": str(s.status).split(".")[-1],
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            "sub_url": s.subscription_url,
            "connect_url": subscription_connect_url(get_settings(), s) if s.access_token else None,
            "short_code": short_code or None,
            "remnawave_uuid": s.remnawave_uuid,
            "remnawave_short_uuid": s.remnawave_short_uuid,
            "device_limit": s.device_limit,
            "traffic_limit_gb": s.traffic_limit_gb,
            "plan_code": s.plan_code,
            "reseller_id": reseller_id,
        })
    return res


@app.post("/api/admin/keys/{uuid}/disable")
async def admin_disable_key(uuid: str, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    admin = await _admin_or_403(user, db)
    sub = (await db.execute(select(Subscription).filter_by(remnawave_uuid=uuid))).scalars().first()
    if not sub:
        raise HTTPException(404, "Key not found")
    gw = make_remnawave_gateway(get_settings())
    try:
        await gw.disable_user(uuid)
    except Exception:
        pass
    await deactivate_subscription_slots(db, gw, sub, actor=_actor(admin))
    sub.status = SubscriptionStatus.disabled
    await _audit(db, admin, "admin.key.disabled", "subscription", sub.id, {"remnawave_uuid": uuid})
    await db.commit()
    return {"status": "ok"}


class AdminCreateKeyRequest(BaseModel):
    tariff_id: int | None = None
    days: int | None = None
    devices: int | None = None
    client_name: str = ""


@app.post("/api/admin/keys/create")
async def admin_create_key(req: AdminCreateKeyRequest, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    """Админ создаёт ключ напрямую — без баланса и без лимита."""
    from datetime import datetime, UTC, timedelta
    admin = await _admin_or_403(user, db)

    if req.tariff_id:
        tariff = await db.get(Tariff, req.tariff_id)
        if not tariff:
            raise HTTPException(404, "Tariff not found")
        days, devices = tariff.duration_days, tariff.device_limit
        traffic_gb, name = tariff.traffic_limit_gb, tariff.name
    else:
        days = req.days or 30
        devices = req.devices or 1
        traffic_gb, name = 0, f"Админ · {days} дн."

    expires_at = datetime.now(UTC) + timedelta(days=days)
    settings = get_settings()
    gateway = make_remnawave_gateway(settings)

    fake_tg = int(secrets.token_hex(6), 16)
    client = Customer(
        telegram_id=fake_tg,
        full_name=req.client_name or "Ключ (админ)",
        referrer_id=admin.id,
    )
    db.add(client)
    await db.flush()

    try:
        remote = await gateway.create_user(
            username=f"adm_{client.id}_{secrets.token_hex(2)}",
            telegram_id=fake_tg,
            expires_at=expires_at,
            device_limit=devices,
            traffic_limit_bytes=traffic_gb * 1024**3,
            squads=settings.squad_uuids,
            description="Admin-generated key",
        )
    except Exception as e:
        raise HTTPException(500, f"Remnawave Error: {str(e)}")

    access_token = secrets.token_urlsafe(32)
    sub = Subscription(
        customer_id=client.id,
        plan_code=name,
        status=SubscriptionStatus.active,
        remnawave_uuid=remote.uuid,
        remnawave_short_uuid=remote.short_uuid,
        subscription_url=remote.subscription_url,
        access_token=access_token,
        device_limit=devices,
        traffic_limit_gb=traffic_gb,
        expires_at=expires_at,
    )
    db.add(sub)
    await _audit(db, admin, "admin.key.created", "subscription", sub.id, {
        "client_id": client.id,
        "client_name": client.full_name,
        "plan_code": name,
        "days": days,
        "device_limit": devices,
        "traffic_limit_gb": traffic_gb,
    })
    await db.commit()
    connect_url = subscription_connect_url(settings, sub)
    return {
        "status": "ok",
        "connect_url": connect_url,
        "sub_url": sub.subscription_url,
        "short_code": subscription_short_code(sub),
    }


# ── Публичные правовые документы (URL для бота и платёжных систем) ────────────

def _doc_page(title: str, body: str) -> HTMLResponse:
    html = f"""<!doctype html><html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · HamaliVPN</title>
<style>
:root{{color-scheme:dark}}
body{{margin:0;background:#07080f;color:#e7eaf6;font:16px/1.7 -apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:40px 22px 80px}}
.brand{{display:flex;align-items:center;gap:10px;margin-bottom:24px}}
.mark{{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,#7c5cff,#19d3c5);
display:grid;place-content:center;color:#07080f;font-weight:800}}
h1{{font-size:26px;margin:0 0 6px}}
h3{{font-size:17px;margin:26px 0 6px;color:#fff}}
p{{margin:8px 0;color:#c3c8e0}}
.date{{color:#7a80a6;font-size:13px;margin-bottom:18px}}
.foot{{margin-top:40px;color:#5b6188;font-size:13px;border-top:1px solid rgba(255,255,255,.08);padding-top:16px}}
a{{color:#7c5cff}}
</style></head><body><div class="wrap">
<div class="brand"><div class="mark">H</div><b>HamaliVPN</b></div>
<h1>{title}</h1><div class="date">Редакция от 26.06.2026</div>
{body}
<div class="foot">HamaliVPN · поддержка: <a href="https://t.me/Hamali_Support">@Hamali_Support</a></div>
</div></body></html>"""
    return HTMLResponse(html)


_PRIVACY_BODY = """
<p>Политика регулирует сбор, использование и защиту информации пользователей сервиса.
Собираются идентификаторы аккаунта, техническая информация и история взаимодействий. Данные
используются для работы сервиса, связи с пользователем и анализа. Передача третьим лицам возможна
только в законодательно установленных случаях или с согласия пользователя. Хранение — в течение
необходимого срока, защита — в разумных пределах. Администрация вправе вносить изменения без
уведомления; согласие считается принятым при дальнейшем использовании.</p>
<h3>1. Общие положения</h3>
<p>1.1. Политика регулирует порядок обработки и защиты информации, передаваемой при использовании
сервиса (далее — «Сервис»).<br>1.2. Используя Сервис, Пользователь подтверждает согласие; при
несогласии обязан прекратить использование.</p>
<h3>2. Сбор информации</h3>
<p>2.1. Могут собираться: идентификаторы аккаунта (логин, ID, никнейм); техническая информация
(IP-адрес, браузер, устройство, ОС); история взаимодействий.<br>2.2. Сервис не требует паспортных
данных, документов, фотографий или иной личной информации сверх минимально необходимой.</p>
<h3>3. Использование информации</h3>
<p>3.1. Только для: работы функционала; связи с Пользователем (уведомления и поддержка); анализа и
улучшения Сервиса.</p>
<h3>4. Передача третьим лицам</h3>
<p>4.1. Не передаётся, кроме случаев: требования закона; исполнения обязательств перед Пользователем
(например, платёжные системы); согласия Пользователя.</p>
<h3>5. Хранение и защита</h3>
<p>5.1. Данные хранятся в течение срока, необходимого для целей обработки.<br>5.2. Принимаются
разумные меры защиты; абсолютная безопасность при передаче через интернет не гарантируется.</p>
<h3>6. Отказ от ответственности</h3>
<p>6.1. Передача информации через интернет сопряжена с рисками.<br>6.2. Администрация не отвечает за
утрату, кражу или раскрытие данных по вине третьих лиц или самого Пользователя.</p>
<h3>7. Изменения</h3>
<p>7.1. Администрация вправе изменять Политику без предварительного уведомления.<br>7.2. Продолжение
использования означает согласие с новой редакцией.</p>
"""

_TERMS_BODY = """
<h3>1. Общие положения</h3>
<p>1.1. Настоящее Пользовательское соглашение (далее — «Соглашение») регулирует порядок использования
онлайн-сервиса (далее — «Сервис»), предоставляемого Администрацией.<br>
1.2. Используя Сервис, включая запуск бота, регистрацию, оплату услуг или получение доступа к
материалам, Пользователь подтверждает, что полностью ознакомился с условиями настоящего Соглашения и
принимает их в полном объёме.<br>
1.3. В случае несогласия с условиями Соглашения Пользователь обязан прекратить использование
Сервиса.</p>

<h3>2. Характер услуг и цифровых товаров</h3>
<p>2.1. Сервис предоставляет цифровые товары и услуги нематериального характера, включая, но не
ограничиваясь: информационные материалы, обучающие программы, консультации, цифровые продукты и
сервисные услуги.<br>
2.2. Материалы, предоставляемые через Сервис, могут включать:</p>
<ul><li>информацию из открытых источников;</li>
<li>авторские материалы Администрации и/или третьих лиц;</li>
<li>аналитические обзоры, подборки, рекомендации, структурированные данные.</li></ul>
<p>2.3. Пользователь осознаёт и соглашается, что ценность цифровых товаров и услуг Сервиса заключается
в систематизации, анализе, форме подачи, сопровождении, поддержке и обновлениях, а не в
эксклюзивности отдельных фрагментов информации.<br>
2.4. Сервис не заявляет и не гарантирует уникальность, исключительность или недоступность отдельных
элементов материалов вне Сервиса.</p>

<h3>3. Отказ от гарантий и ответственности</h3>
<p>3.1. Сервис предоставляется на условиях «AS IS» («как есть»).<br>
3.2. Администрация не гарантирует:</p>
<ul><li>соответствие Сервиса ожиданиям Пользователя;</li>
<li>достижение каких-либо финансовых, коммерческих, профессиональных или иных результатов;</li>
<li>бесперебойную и безошибочную работу Сервиса.</li></ul>
<p>3.3. Администрация не несёт ответственности за:</p>
<ul><li>любые прямые или косвенные убытки, включая упущенную выгоду;</li>
<li>последствия применения Пользователем полученных материалов;</li>
<li>действия или бездействие третьих лиц;</li>
<li>временные технические сбои и ограничения доступа.</li></ul>
<p>3.4. Все решения о применении материалов, рекомендаций и услуг принимаются Пользователем
самостоятельно и на его риск.</p>

<h3>4. Законность использования</h3>
<p>4.1. Сервис не предназначен для поощрения, организации или содействия противоправной
деятельности.<br>
4.2. Пользователь обязуется использовать Сервис исключительно в рамках применимого законодательства и
правил третьих сторон.<br>
4.3. Ответственность за законность использования материалов и услуг Сервиса полностью возлагается на
Пользователя.</p>

<h3>5. Интеллектуальная собственность</h3>
<p>5.1. Все материалы, размещённые в Сервисе, охраняются законодательством об интеллектуальной
собственности.<br>
5.2. Пользователю запрещается копировать, распространять, перепродавать, передавать третьим лицам или
иным образом использовать материалы Сервиса без разрешения правообладателя.<br>
5.3. Нарушение прав интеллектуальной собственности может повлечь ограничение доступа к Сервису без
компенсации.</p>

<h3>6. Ограничение доступа</h3>
<p>6.1. Администрация вправе приостановить или ограничить доступ Пользователя к Сервису в случае:</p>
<ul><li>нарушения условий настоящего Соглашения;</li>
<li>выявления злоупотреблений;</li>
<li>требований законодательства или платёжных провайдеров.</li></ul>
<p>6.2. Ограничение доступа не освобождает Пользователя от обязательств, возникших ранее.<br>
6.3. Администрация оставляет за собой право отказывать в обслуживании Пользователям, чьи действия могут
создавать повышенные риски для Сервиса, платёжных провайдеров или третьих лиц.</p>

<h3>7. Платежи и возвраты</h3>
<p>7.1. Оплата услуг и цифровых товаров производится на условиях, указанных в Сервисе до момента
оплаты.<br>
7.2. В связи с нематериальным характером цифровых товаров и услуг, возврат денежных средств после
предоставления доступа не осуществляется, за исключением случаев, указанных ниже.<br>
7.3. Возврат средств возможен только если:</p>
<ul><li>услуга не была оказана по технической вине Сервиса;</li>
<li>доступ к цифровому товару фактически не был предоставлен.</li></ul>
<p>7.4. Для рассмотрения вопроса о возврате Пользователь обязан обратиться в службу поддержки в течение
24 часов с момента оплаты.<br>
7.5. Решение о возврате принимается Администрацией индивидуально.<br>
7.6. Пользователь подтверждает, что обязуется не инициировать возврат платежа (chargeback) через
платёжные системы без предварительного обращения в службу поддержки Сервиса.</p>

<h3>8. Конфиденциальность</h3>
<p>8.1. Администрация может собирать минимально необходимые технические данные для обеспечения работы
Сервиса.<br>
8.2. Администрация принимает разумные меры для защиты данных, однако не гарантирует абсолютную
безопасность передаваемой информации.</p>

<h3>9. Изменение условий</h3>
<p>9.1. Администрация вправе вносить изменения в настоящее Соглашение.<br>
9.2. Актуальная версия Соглашения публикуется в Сервисе.<br>
9.3. Продолжение использования Сервиса означает согласие Пользователя с обновлёнными условиями.</p>

<h3>10. Контактная информация</h3>
<p>10.1. По всем вопросам Пользователь может обратиться в службу поддержки через форму в самом боте.</p>

<p style="margin-top:24px"><i>Используя Сервис (в том числе запуская бота и/или вводя команду /start),
Пользователь подтверждает, что ознакомлен с настоящим Соглашением и принимает его условия в полном
объёме.</i></p>
"""


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return _doc_page("Политика конфиденциальности", _PRIVACY_BODY)


@app.get("/terms", response_class=HTMLResponse)
async def terms_page():
    return _doc_page("Пользовательское соглашение", _TERMS_BODY)


# ── Payments: приём оплаты и автоматическая выдача подписки ──────────────────
FK_PLAN_DAYS = {
    "1_month": 30,
    "2_months": 60,
    "3_months": 90,
    "6_months": 180,
    "12_months": 365,
}
FK_PLAN_DEVICES = {
    "1_month": 1,
    "2_months": 3,
    "3_months": 5,
    "6_months": 5,
    "12_months": 5,
}
FK_PLAN_NAMES = {
    "1_month": "1 месяц",
    "2_months": "2 месяца",
    "3_months": "3 месяца",
    "6_months": "6 месяцев",
    "12_months": "12 месяцев",
}
FK_REFERRAL_RATE = 0.30


def _connect_url(subscription: Subscription) -> str:
    return subscription_connect_url(settings, subscription)


async def _tg_send(telegram_id: int, text: str, reply_markup=None) -> None:
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        logger.warning("Telegram notification skipped: BOT_TOKEN is not configured")
        return
    try:
        from aiogram import Bot
        bot = Bot(token=token)
        try:
            await bot.send_message(
                telegram_id,
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        finally:
            await bot.session.close()
    except Exception:
        logger.exception("Could not send Telegram notification to %s", telegram_id)


def _paid_access_keyboard(subscription: Subscription):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📲 Подключить устройство",
                    url=_connect_url(subscription),
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Моя подписка",
                    callback_data="subscription:show",
                ),
                InlineKeyboardButton(
                    text="💬 Поддержка",
                    url=f"https://t.me/{settings.support_username.lstrip('@')}",
                ),
            ],
        ]
    )


async def _fk_notify(
    telegram_id: int,
    plan_code: str,
    days: int,
    subscription: Subscription | None,
    *,
    provisioned_ok: bool = True,
) -> None:
    if subscription is None or not provisioned_ok:
        await _tg_send(
            telegram_id,
            "✅ <b>Оплата получена.</b>\n\n"
            "Автоматическая выдача сейчас не завершилась. Мы уже видим оплату — "
            "напишите в поддержку, и доступ быстро активируют вручную.",
        )
        return

    await _tg_send(
        telegram_id,
        "✅ <b>Оплата получена — доступ активирован</b>\n\n"
        f"Тариф — <b>{FK_PLAN_NAMES.get(plan_code, plan_code)}</b>\n"
        f"Срок — <b>{days} дн.</b>\n"
        f"Действует до — <b>{as_utc(subscription.expires_at).strftime('%d.%m.%Y')}</b>\n"
        f"Лимит устройств — <b>{subscription.device_limit}</b>\n\n"
        "Нажмите «📲 Подключить устройство» и добавьте подписку в приложение.",
        reply_markup=_paid_access_keyboard(subscription),
    )


async def _fk_fulfill(order_id: str) -> None:
    """Background: extend/create the subscription for a paid transaction."""
    from datetime import datetime, UTC, timedelta
    from .db import SessionFactory
    from .models import Subscription, SubscriptionStatus, as_utc
    from .services import get_latest_subscription, issue_trial
    settings = get_settings()
    gateway = make_remnawave_gateway(settings)
    async with SessionFactory() as db:
        tx = await db.get(PaymentTransaction, order_id)
        if not tx:
            return
        if tx.status != PaymentStatus.paid:
            logger.warning("Skip fulfillment for non-paid transaction %s", order_id)
            return
        customer = await db.get(Customer, tx.customer_id)
        if not customer:
            return
        days = FK_PLAN_DAYS.get(tx.payload or "", 30)
        devices = FK_PLAN_DEVICES.get(tx.payload or "", 1)
        provisioned_ok = False
        provision_error: str | None = None
        ref_customer: Customer | None = None
        ref_tg, ref_bonus = None, 0
        if customer.referrer_id:
            ref = await db.get(Customer, customer.referrer_id)
            if ref:
                ref_customer = ref
                ref_bonus = int(tx.amount * FK_REFERRAL_RATE)
                ref_tg = ref.telegram_id
        sub = await get_latest_subscription(db, customer.telegram_id)
        provisioned = False
        if not sub:
            try:
                r = await issue_trial(
                    db, gateway, settings,
                    telegram_id=customer.telegram_id,
                    telegram_username=customer.telegram_username,
                    full_name=customer.full_name,
                )
                sub = await db.get(Subscription, r.subscription_id)
                provisioned = True
            except Exception as exc:
                logger.exception("Could not provision paid subscription")
                provision_error = f"create_subscription_failed:{type(exc).__name__}"
        if sub:
            now = datetime.now(UTC)
            base = now if provisioned else max(
                as_utc(sub.expires_at) if sub.expires_at else now, now)
            new_exp = base + timedelta(days=days)
            sub.expires_at = new_exp
            sub.status = SubscriptionStatus.active
            sub.device_limit = devices
            await db.commit()
            if sub.remnawave_uuid:
                try:
                    remote = await gateway.update_user_access(
                        user_uuid=sub.remnawave_uuid, expires_at=new_exp,
                        device_limit=sub.device_limit,
                        traffic_limit_bytes=sub.traffic_limit_gb * 1024**3,
                        squads=settings.squad_uuids,
                    )
                    sub.subscription_url = remote.subscription_url
                    sub.remnawave_short_uuid = remote.short_uuid
                    await prune_hwid_devices_to_limit(
                        user_uuid=sub.remnawave_uuid,
                        device_limit=sub.device_limit,
                        list_devices=gateway.list_hwid_devices,
                        delete_device=gateway.delete_hwid_device,
                    )
                    await sync_subscription_device_slots(
                        db,
                        gateway,
                        settings,
                        sub,
                        actor="system:payment:freekassa",
                    )
                    await db.commit()
                    provisioned_ok = True
                except Exception as exc:
                    logger.exception("Could not update paid Remnawave access")
                    provision_error = f"update_remote_failed:{type(exc).__name__}"
            else:
                provision_error = "missing_remnawave_uuid"

        if provision_error:
            db.add(
                AuditLog(
                    actor="system:payment",
                    action="payment.fulfillment.failed",
                    entity_type="payment_transaction",
                    entity_id=tx.id,
                    details={
                        "provider": tx.provider,
                        "payload": tx.payload,
                        "customer_id": customer.id,
                        "telegram_id": customer.telegram_id,
                        "subscription_id": sub.id if sub else None,
                        "reason": provision_error,
                    },
                )
            )
            await db.commit()

        if provisioned_ok and ref_customer and ref_bonus:
            ref_customer.balance_rub += ref_bonus
            db.add(
                BalanceTransaction(
                    customer_id=ref_customer.id,
                    amount=ref_bonus,
                    type="referral_bonus",
                    description=f"Бонус за оплату реферала: {FK_PLAN_NAMES.get(tx.payload or '', tx.payload or 'тариф')}",
                )
            )
            await db.commit()

        await _fk_notify(
            customer.telegram_id,
            tx.payload or "",
            days,
            sub,
            provisioned_ok=provisioned_ok,
        )
        if provisioned_ok and ref_tg and ref_bonus:
            await _tg_send(
                ref_tg,
                f"🎁 Ваш реферал оплатил подписку!\nНачислено <b>{ref_bonus} ₽</b> "
                "на партнёрский баланс.",
            )


@app.get("/api/webhooks/freekassa")
async def freekassa_webhook(request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_session)):
    p = dict(request.query_params)
    merchant_id = os.getenv("FREEKASSA_MERCHANT_ID", "")
    secret2 = os.getenv("FREEKASSA_SECRET2", "")
    if not merchant_id or not secret2:
        raise HTTPException(503, "FreeKassa not configured")
    mid = p.get("MERCHANT_ID", "")
    amount = p.get("AMOUNT", "")
    order_id = p.get("MERCHANT_ORDER_ID", "")
    sign = (p.get("SIGN", "") or "").lower()
    intid = p.get("intid", "")
    expected = hashlib.md5(f"{mid}:{amount}:{secret2}:{order_id}".encode()).hexdigest()
    if mid != merchant_id or not hmac.compare_digest(expected, sign):
        raise HTTPException(400, "bad sign")
    tx = await db.get(PaymentTransaction, order_id)
    if not tx:
        # Valid signature but unknown order (FreeKassa test/verify ping) — ack.
        return PlainTextResponse("YES")
    if tx.status == PaymentStatus.paid:
        return PlainTextResponse("YES")  # already processed -> idempotent
    tx.status = PaymentStatus.paid
    if intid:
        tx.external_id = intid
    await db.commit()
    background.add_task(_fk_fulfill, order_id)
    return PlainTextResponse("YES")


@app.post("/api/webhooks/platega")
async def platega_webhook(
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    x_merchant_id: str | None = Header(default=None, alias="X-MerchantId"),
    x_secret: str | None = Header(default=None, alias="X-Secret"),
):
    merchant_id = settings.platega_merchant_id.strip()
    api_key = settings.platega_api_key.get_secret_value().strip()
    if not merchant_id or not api_key:
        raise HTTPException(503, "Platega not configured")
    if not (
        x_merchant_id
        and x_secret
        and hmac.compare_digest(x_merchant_id, merchant_id)
        and hmac.compare_digest(x_secret, api_key)
    ):
        raise HTTPException(401, "bad platega headers")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "bad json") from exc

    transaction_id = str(payload.get("id") or "")
    local_order_id = str(payload.get("payload") or "")
    if not transaction_id:
        raise HTTPException(400, "missing transaction id")

    conditions = [PaymentTransaction.external_id == transaction_id]
    if local_order_id:
        conditions.append(PaymentTransaction.id == local_order_id)
    tx = (await db.execute(select(PaymentTransaction).where(or_(*conditions)).limit(1))).scalars().first()
    if not tx:
        logger.warning("Platega callback for unknown transaction", extra={"transaction_id": transaction_id})
        return PlainTextResponse("OK")
    if tx.provider != "platega":
        logger.warning(
            "Platega callback matched non-Platega transaction",
            extra={"transaction_id": transaction_id, "provider": tx.provider},
        )
        return PlainTextResponse("OK")

    status = str(payload.get("status") or "").upper()
    if tx.external_id != transaction_id:
        tx.external_id = transaction_id

    if status == "CONFIRMED":
        if tx.status != PaymentStatus.paid:
            payment_details = payload.get("paymentDetails")
            if not isinstance(payment_details, dict):
                payment_details = {}

            raw_amount = payload.get("amount", payment_details.get("amount"))
            currency = str(
                payload.get("currency") or payment_details.get("currency") or ""
            ).upper()

            if currency and currency != tx.currency.upper():
                logger.error(
                    "Platega currency mismatch transaction_id=%s expected=%s actual=%s",
                    transaction_id,
                    tx.currency,
                    currency,
                )
                raise HTTPException(400, "currency mismatch")

            if raw_amount is None:
                logger.warning(
                    "Platega confirmed callback without amount transaction_id=%s order_id=%s",
                    transaction_id,
                    tx.id,
                )
            else:
                try:
                    amount = int(round(float(raw_amount)))
                except (TypeError, ValueError) as exc:
                    raise HTTPException(400, "bad amount") from exc

                if amount < tx.amount:
                    logger.error(
                        "Platega underpaid transaction_id=%s expected=%s actual=%s",
                        transaction_id,
                        tx.amount,
                        raw_amount,
                    )
                    raise HTTPException(400, "amount mismatch")

                if amount != tx.amount:
                    logger.warning(
                        "Platega amount differs but accepted transaction_id=%s expected=%s actual=%s",
                        transaction_id,
                        tx.amount,
                        raw_amount,
                    )

            tx.status = PaymentStatus.paid
            await db.commit()
            background.add_task(_fk_fulfill, tx.id)
        else:
            await db.commit()
        return PlainTextResponse("OK")

    if status in {"CANCELED", "CANCELLED", "CHARGEBACKED"}:
        if tx.status != PaymentStatus.paid:
            tx.status = PaymentStatus.cancelled
        await db.commit()
        return PlainTextResponse("OK")

    logger.warning("Platega callback with unknown status", extra={"transaction_id": transaction_id, "status": status})
    await db.commit()
    return PlainTextResponse("OK")


@app.get("/api/internal/check_sub_limit")
async def check_sub_limit(short_uuid: str, ip: str, db: AsyncSession = Depends(get_session)):
    sub = (
        await db.execute(
            select(Subscription).filter(
                or_(
                    Subscription.remnawave_short_uuid == short_uuid,
                    Subscription.remnawave_uuid == short_uuid,
                    Subscription.access_token == short_uuid,
                )
            )
        )
    ).scalars().first()

    if not sub or sub.status != SubscriptionStatus.active or as_utc(sub.expires_at) < utcnow():
        return {"allowed": False, "reason": "invalid_or_expired"}

    redis_key = f"sub:devices:{sub.id}"
    now_ts = int(time.time())

    unique_ips, _backend = await _rolling_unique_ips(redis_key, ip, now_ts)

    if unique_ips <= sub.device_limit:
        return {"allowed": True, "devices": unique_ips, "limit": sub.device_limit}

    # Prevent the new IP from permanently taking up a slot if it is rejected.
    await _remove_rolling_ip(redis_key, ip)
    return {
        "allowed": False,
        "reason": "limit_reached",
        "devices": unique_ips,
        "limit": sub.device_limit,
    }


@app.get("/api/internal/subscription_meta")
async def subscription_meta(token: str, db: AsyncSession = Depends(get_session)):
    """Small read-only helper for the local subscription injector.

    The injector can use this to make profile decisions without querying
    Remnawave directly. Keep it read-only and local-facing.
    """
    token = (token or "").strip()
    if not token:
        return {"active": False, "device_limit": 0, "reason": "empty_token"}

    slot = await db.scalar(select(SubscriptionDevice).where(SubscriptionDevice.device_token == token))
    if slot:
        sub = await db.get(Subscription, slot.subscription_id)
        active = bool(
            slot.is_active
            and sub
            and sub.status == SubscriptionStatus.active
            and sub.expires_at
            and as_utc(sub.expires_at) > utcnow()
        )
        return {
            "active": active,
            "kind": "device",
            "device_limit": 1,
            "subscription_id": sub.id if sub else None,
            "device_slot_id": slot.id,
            "target_token": slot.remnawave_short_uuid or slot.remnawave_uuid or token,
            "auth_token": slot.device_token,
            "remnawave_uuid": slot.remnawave_uuid,
            "expires_at": sub.expires_at.isoformat() if sub and sub.expires_at else None,
            "reason": None if active else "inactive_device_slot",
        }

    sub = (
        await db.execute(
            select(Subscription).filter(
                or_(
                    Subscription.remnawave_short_uuid == token,
                    Subscription.remnawave_uuid == token,
                    Subscription.access_token == token,
                )
            )
        )
    ).scalars().first()

    if not sub:
        return {"active": False, "device_limit": 0, "reason": "not_found"}

    active = bool(
        sub.status == SubscriptionStatus.active
        and sub.expires_at
        and as_utc(sub.expires_at) > utcnow()
    )
    return {
        "active": active,
        "kind": "subscription",
        "device_limit": int(sub.device_limit or 1),
        "subscription_id": sub.id,
        "target_token": sub.remnawave_short_uuid or sub.remnawave_uuid or token,
        "auth_token": token,
        "remnawave_uuid": sub.remnawave_uuid,
        "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
    }


PACKAGE_DIR = os.path.dirname(__file__)
CONNECT_TEMPLATES = Jinja2Templates(directory=os.path.join(PACKAGE_DIR, "templates"))
STATIC_DIR = os.path.join(PACKAGE_DIR, "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

PORTAL_DIST_DIR = os.getenv("PORTAL_DIST_DIR", "/opt/hamalivpn/portal-webapp/dist")
if not os.path.isdir(PORTAL_DIST_DIR):
    PORTAL_DIST_DIR = os.path.join(PACKAGE_DIR, "portal_web")
PORTAL_ASSETS_DIR = os.path.join(PORTAL_DIST_DIR, "assets")
if not os.path.isdir(PORTAL_ASSETS_DIR):
    # The production portal is a tiny no-build SPA in src/hamalivpn/portal_web
    # where app.js/styles.css/favicon.svg live next to index.html. Some deploys
    # do not have a dist/assets directory; without this fallback browsers receive
    # the SPA HTML for JS/CSS requests and show a white page with only the raw "H".
    PORTAL_ASSETS_DIR = PORTAL_DIST_DIR
if os.path.isdir(PORTAL_ASSETS_DIR):
    app.mount("/portal/assets", StaticFiles(directory=PORTAL_ASSETS_DIR), name="portal_assets")


def _connect_import_path(access_token: str, client: str) -> str:
    return f"/{access_token}/import/{client}"


def _connect_links(access_token: str, subscription_url: str) -> dict[str, str]:
    if not subscription_url:
        return {
            "hiddify_link": _connect_import_path(access_token, "hiddify"),
            "v2raytun_link": _connect_import_path(access_token, "v2raytun"),
            "happ_link": _connect_import_path(access_token, "happ"),
            "incy_link": _connect_import_path(access_token, "incy"),
            "streisand_link": _connect_import_path(access_token, "streisand"),
            "manual_link": _connect_import_path(access_token, "manual"),
        }
    return {
        "hiddify_link": hiddify_deeplink(subscription_url, settings.subscription_name),
        "v2raytun_link": v2raytun_deeplink(subscription_url),
        "happ_link": happ_deeplink(subscription_url),
        "incy_link": incy_deeplink(subscription_url, settings.subscription_name),
        "streisand_link": streisand_deeplink(subscription_url),
        "manual_link": "",
    }


def _connect_template_response(
    request: Request,
    access_token: str,
    subscription: Subscription,
    *,
    subscription_url: str = "",
    slot: SubscriptionDevice | None = None,
    expired: bool = False,
    limit_reached: bool = False,
    device_slots_used: int = 0,
) -> HTMLResponse:
    public_code = subscription_short_code(subscription)
    links = _connect_links(public_code, subscription_url)
    return CONNECT_TEMPLATES.TemplateResponse(
        request,
        "connect.html",
        {
            "subscription": subscription,
            "subscription_url": subscription_url,
            "qr": qr_data_uri(subscription_url) if subscription_url else None,
            **links,
            "expired": expired,
            "limit_reached": limit_reached,
            "activation_pending": bool(not expired and not limit_reached and not subscription_url),
            "device_slot": slot,
            "device_slots_used": device_slots_used,
            "support_username": settings.support_username,
            "health_status": subscription.health_status,
            "health_message": subscription.health_message,
            "endpoint_count": subscription.health_endpoint_count,
        },
    )


def _set_slot_cookie(response: HTMLResponse | RedirectResponse, subscription: Subscription, slot: SubscriptionDevice) -> None:
    max_age = max(60, int((as_utc(subscription.expires_at) - utcnow()).total_seconds()))
    response.set_cookie(
        f"hamali_slot_{subscription.id}",
        slot.device_token,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
    )


@app.get("/connect/{access_token}/import/{client_name}", response_class=HTMLResponse)
async def public_connect_import(
    request: Request,
    access_token: str,
    client_name: str,
    db: AsyncSession = Depends(get_session),
):
    subscription = await get_subscription_by_token(db, access_token)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    expired = as_utc(subscription.expires_at) <= utcnow()
    if expired:
        return _connect_template_response(request, access_token, subscription, expired=True)

    gateway = make_remnawave_gateway(settings)
    try:
        slot = await ensure_device_slot(
            db,
            gateway,
            settings,
            subscription,
            existing_token=request.cookies.get(f"hamali_slot_{subscription.id}"),
            client_ip=_client_ip(request),
            user_agent=request.headers.get("User-Agent", ""),
        )
        subscription_url = device_subscription_url(settings, subscription, slot)
        await db.commit()
    except DeviceLimitReached:
        device_slots_used = await active_device_slot_count(db, subscription.id)
        return _connect_template_response(
            request,
            access_token,
            subscription,
            expired=False,
            limit_reached=True,
            device_slots_used=device_slots_used,
        )

    links = _connect_links(access_token, subscription_url)
    client = client_name.lower().strip()
    deeplink = {
        "hiddify": links["hiddify_link"],
        "v2raytun": links["v2raytun_link"],
        "happ": links["happ_link"],
        "incy": links["incy_link"],
        "streisand": links["streisand_link"],
    }.get(client, "")

    if client == "manual" or not deeplink:
        response = _connect_template_response(
            request,
            access_token,
            subscription,
            subscription_url=subscription_url,
            slot=slot,
        )
    else:
        response = RedirectResponse(url=deeplink, status_code=302)
    _set_slot_cookie(response, subscription, slot)
    return response


@app.get("/connect/{access_token}", response_class=HTMLResponse)
async def public_connect_page(
    request: Request,
    access_token: str,
    db: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Public device activation page used by the bot and reseller portal.

    The partner portal API is what app.hamali.ru/portal.hamali.ru actually
    serves in production. Keeping the route here ensures every generated
    connect link passes through device slots instead of falling into the SPA
    catch-all and bypassing the new per-device subscription URLs.
    """

    subscription = await get_subscription_by_token(db, access_token)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    expires_at = as_utc(subscription.expires_at)
    expired = expires_at <= utcnow()
    subscription_url = ""
    slot = None
    limit_reached = False
    device_slots_used = 0

    if not expired:
        cookie_name = f"hamali_slot_{subscription.id}"
        existing_token = request.cookies.get(cookie_name)
        if existing_token:
            existing_slot = await db.scalar(
                select(SubscriptionDevice).where(
                    SubscriptionDevice.device_token == existing_token,
                    SubscriptionDevice.subscription_id == subscription.id,
                    SubscriptionDevice.is_active.is_(True),
                )
            )
            if existing_slot:
                gateway = make_remnawave_gateway(settings)
                try:
                    slot = await ensure_device_slot(
                        db,
                        gateway,
                        settings,
                        subscription,
                        existing_token=existing_token,
                        client_ip=_client_ip(request),
                        user_agent=request.headers.get("User-Agent", ""),
                    )
                    subscription_url = device_subscription_url(settings, subscription, slot)
                    await db.commit()
                except DeviceLimitReached:
                    limit_reached = True
                    device_slots_used = await active_device_slot_count(db, subscription.id)

    response = _connect_template_response(
        request,
        access_token,
        subscription,
        subscription_url=subscription_url,
        slot=slot,
        expired=expired,
        limit_reached=limit_reached,
        device_slots_used=device_slots_used,
    )
    if slot:
        _set_slot_cookie(response, subscription, slot)
    return response

@app.get("/portal")
@app.get("/portal/{full_path:path}")
async def serve_portal(request: Request):
    index_path = os.path.join(PORTAL_DIST_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"error": "Portal not built"}

# ── Device control (list / disconnect) — reseller + admin ────────────────────
# NOTE: must be registered BEFORE the SPA catch-all below, or the GET route
# gets shadowed by "/{full_path:path}" and returns index.html instead of JSON.
class DeleteDeviceReq(BaseModel):
    hwid: str


async def _client_sub_for_agent(uuid: str, user: dict, db: AsyncSession):
    """Resolve the subscription and enforce that the caller may manage it."""
    agent = await _reseller_or_admin(user, db)
    sub = (await db.execute(select(Subscription).filter_by(remnawave_uuid=uuid))).scalars().first()
    if not sub:
        raise HTTPException(404, "Subscription not found")
    client = await db.get(Customer, sub.customer_id)
    if agent.role != "super_admin" and (not client or client.referrer_id != agent.id):
        raise HTTPException(403, "Not your client")
    return sub, client, agent


@app.get("/api/reseller/clients/{uuid}/devices")
async def get_reseller_client_devices(
    uuid: str,
    user: dict = Depends(get_portal_user),
    db: AsyncSession = Depends(get_session),
):
    sub, _, _ = await _client_sub_for_agent(uuid, user, db)
    gw = make_remnawave_gateway(get_settings())
    slots = await active_device_slots(db, sub.id)
    slot_devices = [
        {
            "id": f"slot:{slot.id}",
            "hwid": f"slot:{slot.id}",
            "deviceModel": slot.label or "Устройство",
            "platform": slot.platform or "Device slot",
            "userAgent": slot.user_agent or "",
            "updatedAt": (slot.last_seen_at or slot.created_at).isoformat() if (slot.last_seen_at or slot.created_at) else None,
            "slot": True,
            "lastIp": slot.last_ip,
        }
        for slot in slots
    ]
    try:
        await prune_hwid_devices_to_limit(
            user_uuid=uuid,
            device_limit=sub.device_limit,
            list_devices=gw.list_hwid_devices,
            delete_device=gw.delete_hwid_device,
            keep="oldest",
        )
        devices = await gw.list_hwid_devices(uuid)
    except Exception:
        logger.exception("Could not sync Remnawave HWID devices", extra={"subscription_id": sub.id})
        devices = []
    display_devices = slot_devices if slot_devices else devices
    return {
        "device_limit": sub.device_limit,
        "count": len(display_devices),
        "devices": display_devices,
        "slots": slot_devices,
    }


@app.post("/api/reseller/clients/{uuid}/devices/delete")
async def delete_reseller_client_device(
    uuid: str,
    req: DeleteDeviceReq,
    user: dict = Depends(get_portal_user),
    db: AsyncSession = Depends(get_session),
):
    sub, _, actor = await _client_sub_for_agent(uuid, user, db)
    gw = make_remnawave_gateway(get_settings())
    if req.hwid.startswith("slot:"):
        try:
            slot_id = int(req.hwid.split(":", 1)[1])
        except ValueError:
            raise HTTPException(400, "Invalid device slot") from None
        slot = await db.get(SubscriptionDevice, slot_id)
        if not slot or slot.subscription_id != sub.id:
            raise HTTPException(404, "Device slot not found")
        await deactivate_device_slot(db, gw, slot, actor=_actor(actor))
        await db.commit()
        return {"status": "ok"}

    await gw.delete_hwid_device(uuid, req.hwid)
    await _audit(
        db,
        actor,
        "subscription.device.deleted",
        "subscription",
        sub.id,
        {"remnawave_uuid": uuid, "hwid": req.hwid},
    )
    await db.commit()
    return {"status": "ok"}


class UpdateDeviceLimitReq(BaseModel):
    devices_limit: int = Field(ge=1, le=10)


class RenewClientReq(BaseModel):
    tariff_id: int

@app.get("/api/admin/resellers/{reseller_id}/clients")
async def get_admin_reseller_clients(reseller_id: int, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    admin = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not admin or admin.role != "super_admin":
        raise HTTPException(403)
        
    clients = (await db.execute(select(Customer).options(selectinload(Customer.subscriptions)).filter_by(referrer_id=reseller_id).order_by(desc(Customer.created_at)))).scalars().all()
    res = []
    for c in clients:
        sub = next((s for s in c.subscriptions if str(s.status).split('.')[-1] == "active"), None)
        if not sub and c.subscriptions: sub = c.subscriptions[0]
        res.append({
            "id": c.id,
            "name": c.full_name,
            "telegram_id": c.telegram_id,
            "sub_status": str(sub.status).split('.')[-1] if sub else "none",
            "expires_at": sub.expires_at.isoformat() if sub and sub.expires_at else None,
            "sub_url": sub.subscription_url if sub else None,
            "connect_url": subscription_connect_url(get_settings(), sub) if sub and sub.access_token else None,
            "short_code": subscription_short_code(sub) if sub and sub.access_token else None,
            "remnawave_uuid": sub.remnawave_uuid if sub else None,
            "device_limit": sub.device_limit if sub else 0
        })
    return res

@app.put("/api/reseller/clients/{uuid}")
async def update_reseller_client(uuid: str, req: UpdateDeviceLimitReq, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    sub, _, actor = await _client_sub_for_agent(uuid, user, db)

    settings = get_settings()
    gw = make_remnawave_gateway(settings)
    await gw.set_device_limit(uuid, req.devices_limit)
    prune_result = await prune_hwid_devices_to_limit(
        user_uuid=uuid,
        device_limit=req.devices_limit,
        list_devices=gw.list_hwid_devices,
        delete_device=gw.delete_hwid_device,
    )
    old_limit = sub.device_limit
    sub.device_limit = req.devices_limit
    slot_sync = await sync_subscription_device_slots(
        db,
        gw,
        settings,
        sub,
        actor=_actor(actor),
    )
    await _audit(
        db,
        actor,
        "subscription.device_limit.updated",
        "subscription",
        sub.id,
        {
            "remnawave_uuid": uuid,
            "old_limit": old_limit,
            "new_limit": req.devices_limit,
            "pruned_devices": prune_result,
            "slot_sync": slot_sync,
        },
    )
    await db.commit()
    return {
        "status": "ok",
        "device_limit": sub.device_limit,
        "pruned_devices": prune_result,
        "slot_sync": slot_sync,
    }


@app.post("/api/reseller/clients/{uuid}/renew")
async def renew_reseller_client(
    uuid: str,
    req: RenewClientReq,
    user: dict = Depends(get_portal_user),
    db: AsyncSession = Depends(get_session),
):
    actor = await _reseller_or_admin(user, db, lock=True)

    sub = (await db.execute(select(Subscription).filter_by(remnawave_uuid=uuid))).scalars().first()
    if not sub:
        raise HTTPException(404, "Subscription not found")

    client = await db.get(Customer, sub.customer_id)
    if actor.role != "super_admin" and (not client or client.referrer_id != actor.id):
        raise HTTPException(403, "Not your client")

    tariff = await db.get(Tariff, req.tariff_id)
    if not tariff or not tariff.is_active:
        raise HTTPException(404, "Tariff not found")

    if actor.role != "super_admin" and actor.balance_rub < tariff.price_rub:
        raise HTTPException(400, "Insufficient funds")

    from datetime import UTC, datetime

    settings = get_settings()
    now = datetime.now(UTC)
    base = max(as_utc(sub.expires_at) if sub.expires_at else now, now)
    new_exp = base + timedelta(days=tariff.duration_days)

    gw = make_remnawave_gateway(settings)
    remote = await gw.update_user_access(
        user_uuid=uuid,
        expires_at=new_exp,
        device_limit=tariff.device_limit,
        traffic_limit_bytes=tariff.traffic_limit_gb * 1024**3,
        squads=settings.squad_uuids,
    )
    prune_result = await prune_hwid_devices_to_limit(
        user_uuid=uuid,
        device_limit=tariff.device_limit,
        list_devices=gw.list_hwid_devices,
        delete_device=gw.delete_hwid_device,
    )

    old_exp = sub.expires_at.isoformat() if sub.expires_at else None
    old_limit = sub.device_limit
    sub.status = SubscriptionStatus.active
    sub.plan_code = tariff.name
    sub.expires_at = new_exp
    sub.device_limit = tariff.device_limit
    sub.traffic_limit_gb = tariff.traffic_limit_gb
    sub.subscription_url = remote.subscription_url
    sub.remnawave_short_uuid = remote.short_uuid
    slot_sync = await sync_subscription_device_slots(
        db,
        gw,
        settings,
        sub,
        actor=_actor(actor),
    )

    if actor.role != "super_admin":
        actor.balance_rub -= tariff.price_rub
        db.add(
            BalanceTransaction(
                customer_id=actor.id,
                amount=-tariff.price_rub,
                type="renewal",
                description=f"Renewed {client.full_name if client else uuid} · {tariff.name}",
            )
        )

    await _audit(
        db,
        actor,
        "reseller.key.renewed",
        "subscription",
        sub.id,
        {
            "client_id": client.id if client else None,
            "client_name": client.full_name if client else "",
            "tariff_id": tariff.id,
            "tariff_name": tariff.name,
            "amount": 0 if actor.role == "super_admin" else -tariff.price_rub,
            "old_expires_at": old_exp,
            "new_expires_at": new_exp.isoformat(),
            "old_device_limit": old_limit,
            "new_device_limit": tariff.device_limit,
            "pruned_devices": prune_result,
            "slot_sync": slot_sync,
        },
    )
    await db.commit()
    return {
        "status": "ok",
        "expires_at": sub.expires_at.isoformat(),
        "device_limit": sub.device_limit,
        "balance": float(actor.balance_rub),
        "connect_url": subscription_connect_url(settings, sub),
        "short_code": subscription_short_code(sub),
    }

@app.delete("/api/reseller/clients/{uuid}")
async def delete_reseller_client(uuid: str, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    sub, client, agent = await _client_sub_for_agent(uuid, user, db)
    gw = make_remnawave_gateway(get_settings())
    
    # Check if method exists, revoke_subscription or disable_user
    try:
        await gw.revoke_subscription(user_uuid=uuid)
    except Exception:
        await gw.disable_user(user_uuid=uuid)
        
    await deactivate_subscription_slots(db, gw, sub, actor=_actor(agent))
    sub.status = SubscriptionStatus.revoked
    await _audit(
        db,
        agent,
        "reseller.key.revoked",
        "subscription",
        sub.id,
        {
            "client_id": client.id if client else None,
            "client_name": client.full_name if client else "",
            "remnawave_uuid": uuid,
        },
    )
    await db.commit()
    return {"status": "ok"}


# ── Hysteria auth / device guard ─────────────────────────────────────────────
# Standalone Hysteria2 nodes call this endpoint with a subscription secret.
# We keep a short rolling IP window in Redis; this is not perfect HWID control,
# but it prevents obvious multi-household sharing without touching Remnawave VLESS.
import redis.asyncio as redis_async

redis_client = redis_async.from_url(get_settings().redis_url, decode_responses=True)
redis_docker_client = redis_async.Redis(host="172.19.0.3", port=6379, db=0, decode_responses=True)
_DEVICE_WINDOW_CACHE: dict[str, dict[str, int]] = {}
_REDIS_DEVICE_WARNING_EMITTED = False


async def _rolling_unique_ips(redis_key: str, ip: str, now_ts: int, window_seconds: int = 600) -> tuple[int, str]:
    """Track unique client IPs with Redis first and a safe in-process fallback.

    Production currently has two runtimes: Docker services can resolve
    redis://redis:6379, while the live portal API runs as a host systemd
    service. If Redis DNS is unavailable from that host process, we still
    enforce the short rolling window inside the process instead of returning
    500 and breaking subscriptions.
    """
    global _REDIS_DEVICE_WARNING_EMITTED
    window_start = now_ts - window_seconds

    last_exc: Exception | None = None
    for client, backend in ((redis_client, "redis-url"), (redis_docker_client, "redis-docker-ip")):
        try:
            await client.zremrangebyscore(redis_key, 0, window_start)
            await client.zadd(redis_key, {ip: now_ts})
            unique_ips = int(await client.zcard(redis_key))
            await client.expire(redis_key, window_seconds)
            return unique_ips, backend
        except Exception as exc:
            last_exc = exc

    if not _REDIS_DEVICE_WARNING_EMITTED:
        logger.warning("Redis device window unavailable; using memory fallback: %s", last_exc)
        _REDIS_DEVICE_WARNING_EMITTED = True

    bucket = _DEVICE_WINDOW_CACHE.setdefault(redis_key, {})
    for cached_ip, seen_at in list(bucket.items()):
        if seen_at <= window_start:
            bucket.pop(cached_ip, None)
    bucket[ip] = now_ts
    return len(bucket), "memory"


async def _remove_rolling_ip(redis_key: str, ip: str) -> None:
    removed = False
    for client in (redis_client, redis_docker_client):
        try:
            await client.zrem(redis_key, ip)
            removed = True
        except Exception:
            pass
    if not removed:
        _DEVICE_WINDOW_CACHE.get(redis_key, {}).pop(ip, None)


class HysteriaAuthRequest(BaseModel):
    addr: str
    auth: str
    tx: int = 0


def _hysteria_client_ip(req: HysteriaAuthRequest, request: Request) -> str:
    raw = (req.addr or "").strip()
    if raw.startswith("[") and "]" in raw:
        return raw[1 : raw.index("]")]
    if raw.count(":") == 1 and "." in raw:
        return raw.rsplit(":", 1)[0]
    if raw:
        return raw
    return _client_ip(request)


@app.post("/hysteria/auth")
async def hysteria_auth(req: HysteriaAuthRequest, request: Request, db: AsyncSession = Depends(get_session)):
    # Standalone Hysteria2 has no stable per-device HWID like Remnawave/VLESS.
    # Keep it available for every active subscription, while applying a
    # best-effort rolling-IP guard below. True per-device Hysteria limiting
    # requires per-device auth tokens/device slots, not removing the protocol.
    #
    # Legacy UK/London nodes still use the shared Hysteria password injected only
    # into active subscription documents. Accept it only from our known node IPs
    # to avoid opening a public password oracle.
    legacy_password = settings.hysteria_legacy_password.get_secret_value()
    legacy_nodes = settings.hysteria_legacy_node_set
    if legacy_password and req.auth == legacy_password and request.client and request.client.host in legacy_nodes:
        return {"ok": True, "id": "legacy_hysteria"}

    client_ip = _hysteria_client_ip(req, request)

    slot = await db.scalar(select(SubscriptionDevice).where(SubscriptionDevice.device_token == req.auth))
    if slot:
        sub = await db.get(Subscription, slot.subscription_id)
        if not (
            slot.is_active
            and sub
            and sub.status == SubscriptionStatus.active
            and as_utc(sub.expires_at) > utcnow()
        ):
            return {"ok": False, "id": ""}

        redis_key = f"hysteria:slot:{slot.id}"
        unique_ips, backend = await _rolling_unique_ips(
            redis_key,
            client_ip,
            int(time.time()),
            window_seconds=3600,
        )
        if unique_ips > 1:
            await _remove_rolling_ip(redis_key, client_ip)
            logger.info(
                "Hysteria device slot reused from another IP",
                extra={
                    "subscription_id": sub.id,
                    "slot_id": slot.id,
                    "unique_ips": unique_ips,
                    "backend": backend,
                },
            )
            return {"ok": False, "id": ""}

        if not slot.first_ip:
            slot.first_ip = client_ip
        slot.last_ip = client_ip
        slot.last_seen_at = utcnow()
        await db.commit()
        return {"ok": True, "id": f"slot_{slot.id}"}

    sub = (
        await db.execute(
            select(Subscription).filter(
                or_(
                    Subscription.remnawave_uuid == req.auth,
                    Subscription.access_token == req.auth,
                    Subscription.remnawave_short_uuid == req.auth,
                )
            )
        )
    ).scalars().first()

    if not sub or sub.status != SubscriptionStatus.active or as_utc(sub.expires_at) <= utcnow():
        return {"ok": False, "id": ""}

    redis_key = f"hysteria:devices:{sub.id}"
    unique_ips, backend = await _rolling_unique_ips(
        redis_key,
        client_ip,
        int(time.time()),
        window_seconds=3600,
    )
    if unique_ips > sub.device_limit:
        await _remove_rolling_ip(redis_key, client_ip)
        logger.info(
            "Hysteria device limit reached",
            extra={
                "subscription_id": sub.id,
                "device_limit": sub.device_limit,
                "unique_ips": unique_ips,
                "backend": backend,
            },
        )
        return {"ok": False, "id": ""}

    return {"ok": True, "id": f"sub_{sub.id}"}


@app.get("/{access_token}/import/{client_name}", response_class=HTMLResponse)
async def public_short_connect_import(
    request: Request,
    access_token: str,
    client_name: str,
    db: AsyncSession = Depends(get_session),
):
    return await public_connect_import(request, access_token, client_name, db)


@app.get("/{access_token}", response_class=HTMLResponse)
async def public_short_connect_page(
    request: Request,
    access_token: str,
    db: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    return await public_connect_page(request, access_token, db)


# Redirect every non-API frontend route to the active partner portal.
#
# The old Telegram mini-app used to be served from /opt/hamalivpn/webapp/dist
# through this catch-all route. That made portal.hamali.ru and app.hamali.ru
# randomly open an obsolete interface. Keep the reseller portal as the only
# public frontend entry point.
@app.get("/")
async def serve_portal_root():
    return RedirectResponse(url="/portal", status_code=307)


# Keep this route LAST: FastAPI resolves routes in declaration order, and a
# catch-all GET above API routes silently returns HTML where JSON is expected.
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    return RedirectResponse(url="/portal", status_code=307)
