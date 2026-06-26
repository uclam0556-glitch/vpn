import os
import hmac
import hashlib
import time
from urllib.parse import parse_qsl
from fastapi import BackgroundTasks, FastAPI, Depends, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from .db import get_session
from .models import Customer, PaymentStatus, PaymentTransaction
import json
import secrets

app = FastAPI(title="HamaliVPN TWA API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.hamali.ru"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    return {"id": customer.telegram_id}

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
from .config import get_settings

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
async def get_user_servers(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_session)):
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
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"http://127.0.0.1:8000/api/sub/{active_sub.remnawave_uuid}", headers={"User-Agent": "HamaliVPN-WebApp"})
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

from pydantic import BaseModel
from sqlalchemy import desc
from datetime import timedelta
from .models import BalanceTransaction, Tariff, Subscription, SubscriptionStatus
from .remnawave import make_remnawave_gateway
import secrets

class BuyKeyRequest(BaseModel):
    tariff_id: int
    client_name: str = ""
    client_phone: str = ""
    client_telegram: str = ""
    note: str = ""


@app.get("/api/portal/me")
async def get_portal_me(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    customer = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not customer:
        raise HTTPException(404)
    return {"role": customer.role, "name": customer.full_name, "level": customer.reseller_level}

@app.get("/api/reseller/dashboard")
async def get_reseller_dashboard(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    customer = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not customer or customer.role not in ["reseller", "super_admin"]:
        raise HTTPException(403, "Not a reseller")
    
    clients_count = (await db.execute(select(Customer).filter_by(referrer_id=customer.id))).scalars().all()
    txs = (await db.execute(select(BalanceTransaction).filter_by(customer_id=customer.id).order_by(desc(BalanceTransaction.created_at)).limit(10))).scalars().all()
    
    return {
        "balance": float(customer.balance_rub),
        "clients_count": len(clients_count),
        "transactions": [{"id": t.id, "amount": t.amount, "type": t.type, "desc": t.description, "date": t.created_at.isoformat()} for t in txs]
    }

@app.get("/api/reseller/clients")
async def get_reseller_clients(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    customer = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not customer or customer.role not in ["reseller", "super_admin"]:
        raise HTTPException(403, "Not a reseller")
        
    clients = (await db.execute(select(Customer).options(selectinload(Customer.subscriptions)).filter_by(referrer_id=customer.id).order_by(desc(Customer.created_at)))).scalars().all()

    base = get_settings().public_base_url.rstrip("/")
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
            "connect_url": f"{base}/connect/{sub.access_token}" if sub and sub.access_token else None,
            "remnawave_uuid": sub.remnawave_uuid if sub else None,
            "device_limit": sub.device_limit if sub else 0
        })
    return res

@app.get("/api/reseller/tariffs")
async def get_tariffs(user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    customer = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not customer or customer.role not in ["reseller", "super_admin"]:
        raise HTTPException(403, "Not a reseller")
    
    tariffs = (await db.execute(select(Tariff).filter_by(is_active=True))).scalars().all()
    return [{"id": t.id, "name": t.name, "duration_days": t.duration_days, "price_rub": t.price_rub, "device_limit": t.device_limit, "traffic_limit_gb": t.traffic_limit_gb} for t in tariffs]

@app.post("/api/reseller/keys/buy")
async def buy_key(req: BuyKeyRequest, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    customer = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not customer or customer.role not in ["reseller", "super_admin"]:
        raise HTTPException(403, "Not a reseller")
        
    tariff = await db.get(Tariff, req.tariff_id)
    if not tariff or not tariff.is_active:
        raise HTTPException(404, "Tariff not found")
        
    if customer.balance_rub < tariff.price_rub:
        raise HTTPException(400, "Insufficient funds")
        
    from datetime import datetime, UTC
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=tariff.duration_days)
    
    # Create client Customer
    fake_tg_id = int(secrets.token_hex(4), 16) # random id since they don't auth via tg yet
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

    await db.commit()
    connect_url = f"{settings.public_base_url.rstrip('/')}/connect/{access_token}"
    return {
        "status": "ok",
        "client_id": new_client.id,
        "sub_url": sub.subscription_url,
        "connect_url": connect_url,
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
    await db.commit()
    return {"status": "ok", "id": customer.id, "name": customer.full_name, "portal_access_key": new_key}


# ── Admin: полное управление из панели ───────────────────────────────────────

async def _admin_or_403(user: dict, db: AsyncSession) -> Customer:
    c = (await db.execute(select(Customer).filter_by(telegram_id=user["id"]))).scalars().first()
    if not c or c.role != "super_admin":
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
    await _admin_or_403(user, db)
    t = Tariff(name=req.name, duration_days=req.duration_days, price_rub=req.price_rub,
               device_limit=req.device_limit, traffic_limit_gb=req.traffic_limit_gb, is_active=req.is_active)
    db.add(t)
    await db.commit()
    return {"status": "ok", "id": t.id}


@app.patch("/api/admin/tariffs/{tid}")
async def admin_edit_tariff(tid: int, req: TariffIn, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    await _admin_or_403(user, db)
    t = await db.get(Tariff, tid)
    if not t:
        raise HTTPException(404, "Tariff not found")
    t.name, t.duration_days, t.price_rub = req.name, req.duration_days, req.price_rub
    t.device_limit, t.traffic_limit_gb, t.is_active = req.device_limit, req.traffic_limit_gb, req.is_active
    await db.commit()
    return {"status": "ok"}


@app.delete("/api/admin/tariffs/{tid}")
async def admin_delete_tariff(tid: int, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    await _admin_or_403(user, db)
    t = await db.get(Tariff, tid)
    if t:
        await db.delete(t)
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
    await _admin_or_403(user, db)
    r = await db.get(Customer, rid)
    if not r:
        raise HTTPException(404, "Reseller not found")
    r.is_blocked = req.blocked
    await db.commit()
    return {"status": "ok", "is_blocked": r.is_blocked}


@app.post("/api/admin/resellers/{rid}/level")
async def admin_set_level(rid: int, req: LevelIn, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    await _admin_or_403(user, db)
    r = await db.get(Customer, rid)
    if not r:
        raise HTTPException(404, "Reseller not found")
    r.reseller_level = req.level
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
        if q and q.lower() not in name.lower() and q not in str(cust.telegram_id if cust else ""):
            continue
        res.append({
            "uuid": s.remnawave_uuid,
            "client": name,
            "telegram_id": cust.telegram_id if cust else None,
            "status": str(s.status).split(".")[-1],
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            "reseller_id": cust.referrer_id if cust else None,
        })
    return res


@app.post("/api/admin/keys/{uuid}/disable")
async def admin_disable_key(uuid: str, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    await _admin_or_403(user, db)
    sub = (await db.execute(select(Subscription).filter_by(remnawave_uuid=uuid))).scalars().first()
    if not sub:
        raise HTTPException(404, "Key not found")
    gw = make_remnawave_gateway(get_settings())
    try:
        await gw.disable_user(uuid)
    except Exception:
        pass
    sub.status = SubscriptionStatus.disabled
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
    await db.commit()
    connect_url = f"{settings.public_base_url.rstrip('/')}/connect/{access_token}"
    return {"status": "ok", "connect_url": connect_url, "sub_url": sub.subscription_url}


# ── FreeKassa: приём оплаты и автоматическая выдача подписки ──────────────────
FK_PLAN_DAYS = {"1_month": 30, "2_months": 60, "3_months": 90, "6_months": 180}
FK_PLAN_DEVICES = {"1_month": 1, "2_months": 3, "3_months": 5, "6_months": 5}
FK_PLAN_NAMES = {"1_month": "1 месяц", "2_months": "2 месяца", "3_months": "3 месяца", "6_months": "6 месяцев"}
FK_REFERRAL_RATE = 0.30


async def _tg_send(telegram_id: int, text: str) -> None:
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        return
    try:
        from aiogram import Bot
        bot = Bot(token=token)
        try:
            await bot.send_message(telegram_id, text, parse_mode="HTML")
        finally:
            await bot.session.close()
    except Exception:
        pass


async def _fk_notify(telegram_id: int, plan_code: str, days: int) -> None:
    await _tg_send(
        telegram_id,
        f"✅ <b>Оплата получена!</b>\nТариф «{FK_PLAN_NAMES.get(plan_code, plan_code)}» "
        f"активирован на {days} дн. Откройте «Моя подписка» в боте.",
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
        customer = await db.get(Customer, tx.customer_id)
        if not customer:
            return
        days = FK_PLAN_DAYS.get(tx.payload or "", 30)
        devices = FK_PLAN_DEVICES.get(tx.payload or "", 1)
        ref_tg, ref_bonus = None, 0
        if customer.referrer_id:
            ref = await db.get(Customer, customer.referrer_id)
            if ref:
                ref_bonus = int(tx.amount * FK_REFERRAL_RATE)
                ref.balance_rub += ref_bonus
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
            except Exception:
                pass
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
                    await db.commit()
                except Exception:
                    pass
        await _fk_notify(customer.telegram_id, tx.payload or "", days)
        if ref_tg and ref_bonus:
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


app.mount("/portal/assets", StaticFiles(directory="/opt/hamalivpn/portal-webapp/dist/assets"), name="portal_assets")

@app.get("/portal")
@app.get("/portal/{full_path:path}")
async def serve_portal(request: Request):
    import os
    index_path = "/opt/hamalivpn/portal-webapp/dist/index.html"
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"error": "Portal not built"}

app.mount("/assets", StaticFiles(directory="/opt/hamalivpn/webapp/dist/assets"), name="assets")

# Fallback to index.html for SPA routing
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = f"/opt/hamalivpn/webapp/dist/{full_path}"
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse("/opt/hamalivpn/webapp/dist/index.html")





from pydantic import BaseModel
class UpdateDeviceLimitReq(BaseModel):
    devices_limit: int

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
            "remnawave_uuid": sub.remnawave_uuid if sub else None,
            "device_limit": sub.devices_limit if sub else 0,
            "device_limit": sub.devices_limit if sub else 0
        })
    return res

@app.put("/api/reseller/clients/{uuid}")
async def update_reseller_client(uuid: str, req: UpdateDeviceLimitReq, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    agent = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not agent or agent.role not in ["reseller", "super_admin"]:
        raise HTTPException(403)
        
    sub = (await db.execute(select(Subscription).filter_by(remnawave_uuid=uuid))).scalars().first()
    if not sub:
        raise HTTPException(404, "Subscription not found")
        
    client = await db.get(Customer, sub.customer_id)
    if agent.role != "super_admin" and client.referrer_id != agent.id:
        raise HTTPException(403, "Not your client")
        
    from .app import settings
    gw = make_remnawave_gateway(settings)
    
    success = await gw.update_user_access(user_uuid=uuid, devices_limit=req.devices_limit)
    if success:
        sub.devices_limit = req.devices_limit
        await db.commit()
        return {"status": "ok"}
    raise HTTPException(500, "Failed to update in Remnawave")

@app.delete("/api/reseller/clients/{uuid}")
async def delete_reseller_client(uuid: str, user: dict = Depends(get_portal_user), db: AsyncSession = Depends(get_session)):
    tg_id = user['id']
    agent = (await db.execute(select(Customer).filter_by(telegram_id=tg_id))).scalars().first()
    if not agent or agent.role not in ["reseller", "super_admin"]:
        raise HTTPException(403)
        
    sub = (await db.execute(select(Subscription).filter_by(remnawave_uuid=uuid))).scalars().first()
    if not sub:
        raise HTTPException(404, "Subscription not found")
        
    client = await db.get(Customer, sub.customer_id)
    if agent.role != "super_admin" and client.referrer_id != agent.id:
        raise HTTPException(403, "Not your client")
        
    from .app import settings
    gw = make_remnawave_gateway(settings)
    
    # Check if method exists, revoke_subscription or disable_user
    try:
        await gw.revoke_subscription(user_uuid=uuid)
    except:
        await gw.disable_user(user_uuid=uuid)
        
    sub.status = SubscriptionStatus.revoked
    await db.commit()
    return {"status": "ok"}
