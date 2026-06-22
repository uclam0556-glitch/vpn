import secrets
import uuid
from datetime import UTC, datetime
from typing import Protocol

import httpx

from .config import Settings
from .schemas import RemoteUser


class RemnawaveError(RuntimeError):
    pass


class RemnawaveGateway(Protocol):
    async def create_user(
        self,
        *,
        username: str,
        telegram_id: int,
        expires_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
        squads: list[str],
        description: str,
    ) -> RemoteUser: ...

    async def disable_user(self, user_uuid: str) -> None: ...

    async def revoke_subscription(self, user_uuid: str) -> RemoteUser | None: ...


class RemnawaveClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.base_url = settings.panel_base_url.rstrip("/")
        self.transport = transport
        token = settings.remnawave_api_token.get_secret_value()
        self.headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=20,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, **kwargs)
        if response.is_error:
            message = response.text[:500]
            raise RemnawaveError(f"Remnawave {response.status_code}: {message}")
        return response.json()

    @staticmethod
    def _parse_user(payload: dict) -> RemoteUser:
        data = payload.get("response", payload)
        return RemoteUser(
            uuid=data["uuid"],
            short_uuid=data["shortUuid"],
            username=data["username"],
            subscription_url=data["subscriptionUrl"],
            expire_at=data["expireAt"],
            device_limit=data.get("hwidDeviceLimit"),
        )

    async def create_user(
        self,
        *,
        username: str,
        telegram_id: int,
        expires_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
        squads: list[str],
        description: str,
    ) -> RemoteUser:
        payload = {
            "username": username,
            "status": "ACTIVE",
            "expireAt": expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "telegramId": telegram_id,
            "description": description,
            "tag": "HAMALIVPN",
            "hwidDeviceLimit": device_limit,
            "trafficLimitBytes": traffic_limit_bytes,
            "trafficLimitStrategy": "NO_RESET",
            "activeInternalSquads": squads,
        }
        response = await self._request("POST", "/api/users", json=payload)
        return self._parse_user(response)

    async def disable_user(self, user_uuid: str) -> None:
        await self._request("POST", f"/api/users/{user_uuid}/actions/disable")

    async def revoke_subscription(self, user_uuid: str) -> RemoteUser | None:
        response = await self._request(
            "POST",
            f"/api/users/{user_uuid}/actions/revoke",
            json={},
        )
        return self._parse_user(response)


class MockRemnawaveClient:
    """Deterministic local gateway used before the real panel is connected."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def create_user(
        self,
        *,
        username: str,
        telegram_id: int,
        expires_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
        squads: list[str],
        description: str,
    ) -> RemoteUser:
        user_uuid = str(uuid.uuid4())
        short_uuid = secrets.token_urlsafe(10)
        subscription_url = (
            f"{self.settings.public_base_url.rstrip('/')}/demo/sub/{short_uuid}"
        )
        return RemoteUser(
            uuid=user_uuid,
            short_uuid=short_uuid,
            username=username,
            subscription_url=subscription_url,
            expire_at=expires_at,
            device_limit=device_limit,
        )

    async def disable_user(self, user_uuid: str) -> None:
        return None

    async def revoke_subscription(self, user_uuid: str) -> RemoteUser | None:
        return None


def make_remnawave_gateway(settings: Settings) -> RemnawaveGateway:
    if settings.remnawave_mock:
        return MockRemnawaveClient(settings)
    return RemnawaveClient(settings)
