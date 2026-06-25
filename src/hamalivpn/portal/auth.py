import hashlib
import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from .models import Reseller, SecretKey, SecretKeyRole, generate_secret_key, utcnow

SESSION_KEY = "portal_identity"


def hash_secret_key(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def key_prefix(token: str) -> str:
    return token.strip()[:12]


@dataclass(frozen=True, slots=True)
class Identity:
    secret_key_id: int
    role: SecretKeyRole
    reseller_id: int | None

    @property
    def is_admin(self) -> bool:
        return self.role == SecretKeyRole.admin


async def authenticate(session: AsyncSession, token: str) -> SecretKey | None:
    """Resolve a plaintext token to an active SecretKey, or None."""
    token = (token or "").strip()
    if not token:
        return None
    digest = hash_secret_key(token)
    candidate = await session.scalar(select(SecretKey).where(SecretKey.key_hash == digest))
    # Constant-time compare guards against timing oracles even though the lookup
    # is by hash; a missing row still does a dummy compare.
    expected = candidate.key_hash if candidate else "0" * 64
    if not hmac.compare_digest(expected, digest) or candidate is None:
        return None
    if not candidate.is_active:
        return None
    if candidate.reseller_id is not None:
        reseller = await session.get(Reseller, candidate.reseller_id)
        if reseller is None or reseller.is_blocked:
            return None
    candidate.last_used_at = utcnow()
    return candidate


def login_session(request: Request, key: SecretKey) -> None:
    request.session[SESSION_KEY] = {
        "secret_key_id": key.id,
        "role": str(key.role),
        "reseller_id": key.reseller_id,
    }


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def current_identity(request: Request) -> Identity | None:
    raw = request.session.get(SESSION_KEY)
    if not raw:
        return None
    try:
        return Identity(
            secret_key_id=int(raw["secret_key_id"]),
            role=SecretKeyRole(raw["role"]),
            reseller_id=raw["reseller_id"],
        )
    except (KeyError, ValueError):
        return None


SessionDep = Depends(get_session)


def require_identity(request: Request) -> Identity:
    identity = current_identity(request)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return identity


def require_reseller(request: Request) -> Identity:
    identity = require_identity(request)
    if identity.role != SecretKeyRole.reseller or identity.reseller_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reseller access only")
    return identity


def require_admin(request: Request) -> Identity:
    identity = require_identity(request)
    if not identity.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access only")
    return identity


async def load_reseller(session: AsyncSession, identity: Identity) -> Reseller:
    if identity.reseller_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No reseller bound")
    reseller = await session.get(Reseller, identity.reseller_id)
    if reseller is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reseller not found")
    if reseller.is_blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reseller blocked")
    return reseller


__all__ = [
    "Identity",
    "authenticate",
    "current_identity",
    "generate_secret_key",
    "hash_secret_key",
    "key_prefix",
    "load_reseller",
    "login_session",
    "logout_session",
    "require_admin",
    "require_identity",
    "require_reseller",
]
