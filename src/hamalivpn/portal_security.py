import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis_async

from .config import Settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "hamali_portal_session"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class PortalSecurityStore:
    """Shared Redis security state with a fail-safe single-process fallback.

    Authentication must remain available during a short Redis incident, but the
    fallback still enforces rate limits and short session expiry in the API
    process. Redis is the normal production backend.
    """

    def __init__(self, settings: Settings):
        urls = [settings.redis_url]
        if settings.redis_fallback_url:
            urls.append(settings.redis_fallback_url)
        self.clients = [redis_async.from_url(url, decode_responses=True) for url in urls]
        self.session_ttl = max(300, min(settings.portal_session_ttl_seconds, 24 * 60 * 60))
        self._memory: dict[str, tuple[str, float]] = {}
        self._redis_warning_emitted = False

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    async def _redis_call(self, method: str, *args, **kwargs):
        last_error: Exception | None = None
        for client in self.clients:
            try:
                return await getattr(client, method)(*args, **kwargs)
            except Exception as exc:  # Redis availability must not break portal login.
                last_error = exc
        if not self._redis_warning_emitted:
            logger.warning("Portal Redis unavailable; using bounded memory fallback: %s", last_error)
            self._redis_warning_emitted = True
        raise RuntimeError("redis unavailable") from last_error

    def _prune_memory(self) -> None:
        now = time.monotonic()
        for key, (_, expires_at) in list(self._memory.items()):
            if expires_at <= now:
                self._memory.pop(key, None)

    async def rate_limit(self, scope: str, identity: str, *, limit: int, window: int) -> RateLimitResult:
        key = f"portal:limit:{scope}:{self._digest(identity)[:24]}"
        try:
            count = int(await self._redis_call("incr", key))
            if count == 1:
                await self._redis_call("expire", key, window)
            ttl = max(0, int(await self._redis_call("ttl", key)))
            return RateLimitResult(count <= limit, ttl if count > limit else 0)
        except RuntimeError:
            self._prune_memory()
            raw, expires_at = self._memory.get(key, ("0", time.monotonic() + window))
            count = int(raw) + 1
            self._memory[key] = (str(count), expires_at)
            retry_after = max(0, int(expires_at - time.monotonic()))
            return RateLimitResult(count <= limit, retry_after if count > limit else 0)

    async def clear_rate_limit(self, scope: str, identity: str) -> None:
        key = f"portal:limit:{scope}:{self._digest(identity)[:24]}"
        try:
            await self._redis_call("delete", key)
        except RuntimeError:
            self._memory.pop(key, None)

    async def create_session(self, customer_id: int, role: str) -> tuple[str, int]:
        token = secrets.token_urlsafe(48)
        key = f"portal:session:{self._digest(token)}"
        customer_key = f"portal:customer-sessions:{customer_id}"
        payload = json.dumps(
            {"customer_id": customer_id, "role": role, "issued_at": int(time.time())},
            separators=(",", ":"),
        )
        try:
            await self._redis_call("set", key, payload, ex=self.session_ttl)
            await self._redis_call("sadd", customer_key, key)
            await self._redis_call("expire", customer_key, self.session_ttl)
        except RuntimeError:
            self._prune_memory()
            self._memory[key] = (payload, time.monotonic() + self.session_ttl)
        return token, self.session_ttl

    async def revoke_customer_sessions(self, customer_id: int) -> None:
        customer_key = f"portal:customer-sessions:{customer_id}"
        try:
            session_keys = list(await self._redis_call("smembers", customer_key))
            if session_keys:
                await self._redis_call("delete", *session_keys)
            await self._redis_call("delete", customer_key)
            return
        except RuntimeError:
            self._prune_memory()
        for key, (raw, _expires_at) in list(self._memory.items()):
            if not key.startswith("portal:session:"):
                continue
            try:
                if int(json.loads(raw).get("customer_id", 0)) == customer_id:
                    self._memory.pop(key, None)
            except (TypeError, ValueError):
                self._memory.pop(key, None)

    async def get_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        key = f"portal:session:{self._digest(token)}"
        try:
            raw = await self._redis_call("get", key)
        except RuntimeError:
            self._prune_memory()
            item = self._memory.get(key)
            raw = item[0] if item else None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if not isinstance(data.get("customer_id"), int):
                return None
            return data
        except (TypeError, ValueError):
            return None

    async def revoke_session(self, token: str) -> None:
        if not token:
            return
        key = f"portal:session:{self._digest(token)}"
        try:
            await self._redis_call("delete", key)
        except RuntimeError:
            self._memory.pop(key, None)
