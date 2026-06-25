from pydantic import BaseModel, Field

from .models import Client, LedgerEntry, Reseller, Tariff, VpnKey
from .services import kopecks_to_rubles

# ── request bodies ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    key: str = Field(min_length=4, max_length=128)


class BuyKeyRequest(BaseModel):
    tariff_code: str
    client_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=80)


class ExtendKeyRequest(BaseModel):
    tariff_code: str
    idempotency_key: str | None = Field(default=None, max_length=80)


class ClientRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    telegram: str | None = Field(default=None, max_length=64)
    note: str | None = None


class CreateResellerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    level: str = "start"
    telegram_id: int | None = None
    allow_negative: bool = False


class TopupRequest(BaseModel):
    amount_rub: float = Field(gt=0)
    comment: str = ""
    idempotency_key: str | None = Field(default=None, max_length=80)


class AdjustRequest(BaseModel):
    amount_rub: float = Field(description="Signed: positive credit, negative debit")
    kind: str = "adjust"
    comment: str = ""
    idempotency_key: str | None = Field(default=None, max_length=80)


class IssueSecretKeyRequest(BaseModel):
    label: str = Field(default="", max_length=120)


class CreateTariffRequest(BaseModel):
    code: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=120)
    duration_days: int = Field(gt=0)
    price_rub: float = Field(ge=0)
    device_limit: int = 1
    traffic_limit_gb: int = 0
    squad_uuids: str = ""
    sort_order: int = 0


# ── serializers ──────────────────────────────────────────────────────────────

def serialize_tariff(tariff: Tariff, price_kopecks: int | None = None) -> dict:
    return {
        "code": tariff.code,
        "name": tariff.name,
        "duration_days": tariff.duration_days,
        "price_rub": kopecks_to_rubles(
            tariff.price_kopecks if price_kopecks is None else price_kopecks
        ),
        "device_limit": tariff.device_limit,
        "traffic_limit_gb": tariff.traffic_limit_gb,
        "is_active": tariff.is_active,
    }


def serialize_key(key: VpnKey) -> dict:
    return {
        "id": key.id,
        "tariff_code": key.tariff_code,
        "status": str(key.status),
        "subscription_url": key.subscription_url,
        "device_limit": key.device_limit,
        "traffic_limit_gb": key.traffic_limit_gb,
        "client_id": key.client_id,
        "note": key.note,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "price_paid_rub": kopecks_to_rubles(key.price_paid_kopecks),
    }


def serialize_client(client: Client) -> dict:
    return {
        "id": client.id,
        "name": client.name,
        "phone": client.phone,
        "telegram": client.telegram,
        "note": client.note,
        "created_at": client.created_at.isoformat() if client.created_at else None,
    }


def serialize_ledger(entry: LedgerEntry) -> dict:
    return {
        "id": entry.id,
        "kind": str(entry.kind),
        "amount_rub": kopecks_to_rubles(entry.amount_kopecks),
        "balance_after_rub": kopecks_to_rubles(entry.balance_after_kopecks),
        "comment": entry.comment,
        "actor": entry.actor,
        "vpn_key_id": entry.vpn_key_id,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def serialize_reseller(reseller: Reseller) -> dict:
    return {
        "id": reseller.id,
        "name": reseller.name,
        "level": str(reseller.level),
        "telegram_id": reseller.telegram_id,
        "telegram_username": reseller.telegram_username,
        "balance_rub": kopecks_to_rubles(reseller.balance_kopecks),
        "allow_negative": reseller.allow_negative,
        "is_blocked": reseller.is_blocked,
        "created_at": reseller.created_at.isoformat() if reseller.created_at else None,
    }
