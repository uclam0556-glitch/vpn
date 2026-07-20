import secrets
import uuid
from datetime import UTC, datetime
from typing import Protocol

import httpx

from .config import Settings
from .schemas import RemoteUser


class RemnawaveError(RuntimeError):
    pass


class RemnawaveNotFoundError(RemnawaveError):
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

    async def update_user_access(
        self,
        *,
        user_uuid: str,
        expires_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
        squads: list[str],
    ) -> RemoteUser: ...

    async def revoke_subscription(self, user_uuid: str) -> RemoteUser | None: ...

    async def set_device_limit(self, user_uuid: str, device_limit: int) -> None: ...

    async def list_hwid_devices(self, user_uuid: str) -> list[dict]: ...

    async def delete_hwid_device(self, user_uuid: str, hwid: str) -> None: ...

    async def list_nodes_summary(self) -> list[dict]: ...


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
            if response.status_code == 404:
                raise RemnawaveNotFoundError(f"Remnawave {response.status_code}: {message}")
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

    async def update_user_access(
        self,
        *,
        user_uuid: str,
        expires_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
        squads: list[str],
    ) -> RemoteUser:
        payload = {
            "uuid": user_uuid,
            "status": "ACTIVE",
            "expireAt": expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "hwidDeviceLimit": device_limit,
            "trafficLimitBytes": traffic_limit_bytes,
            "trafficLimitStrategy": "NO_RESET",
            "activeInternalSquads": squads,
        }
        response = await self._request("PATCH", "/api/users", json=payload)
        return self._parse_user(response)

    async def revoke_subscription(self, user_uuid: str) -> RemoteUser | None:
        response = await self._request(
            "POST",
            f"/api/users/{user_uuid}/actions/revoke",
            json={},
        )
        return self._parse_user(response)

    async def set_device_limit(self, user_uuid: str, device_limit: int) -> None:
        # Partial update: keep expiry/squads/traffic untouched and only change HWID limit.
        await self._request(
            "PATCH",
            "/api/users",
            json={"uuid": user_uuid, "hwidDeviceLimit": device_limit},
        )

    async def list_hwid_devices(self, user_uuid: str) -> list[dict]:
        response = await self._request("GET", f"/api/hwid/devices/{user_uuid}")
        return response.get("response", {}).get("devices", [])

    async def delete_hwid_device(self, user_uuid: str, hwid: str) -> None:
        try:
            await self._request(
                "POST",
                "/api/hwid/devices/delete",
                json={"userUuid": user_uuid, "hwid": hwid},
            )
        except RemnawaveNotFoundError:
            pass  # already removed — idempotent success

    async def list_nodes_summary(self) -> list[dict]:
        response = await self._request("GET", "/api/nodes")
        rows = response.get("response", response)
        summaries: list[dict] = []
        for node in rows if isinstance(rows, list) else []:
            system = node.get("system") or {}
            info = system.get("info") or {}
            stats = system.get("stats") or {}
            interface = stats.get("interface") or {}
            memory_total = int(info.get("memoryTotal") or 0)
            memory_used = int(stats.get("memoryUsed") or 0)
            cpus = max(1, int(info.get("cpus") or 1))
            load = stats.get("loadAvg") or []
            load_one = float(load[0]) if load else 0.0
            summaries.append(
                {
                    "name": str(node.get("name") or "Node")[:100],
                    "country_code": str(node.get("countryCode") or "").upper()[:3],
                    "connected": bool(node.get("isConnected")),
                    "disabled": bool(node.get("isDisabled")),
                    "users_online": int(node.get("usersOnline") or 0),
                    "traffic_used_bytes": int(node.get("trafficUsedBytes") or 0),
                    "rx_mbps": round(float(interface.get("rxBytesPerSec") or 0) * 8 / 1_000_000, 2),
                    "tx_mbps": round(float(interface.get("txBytesPerSec") or 0) * 8 / 1_000_000, 2),
                    "cpu_percent": round(min(999.0, load_one / cpus * 100), 1),
                    "memory_percent": round(memory_used / memory_total * 100, 1)
                    if memory_total
                    else 0.0,
                    "updated_at": node.get("updatedAt"),
                }
            )
        return summaries


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
        subscription_url = f"{self.settings.public_base_url.rstrip('/')}/demo/sub/{short_uuid}"
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

    async def update_user_access(
        self,
        *,
        user_uuid: str,
        expires_at: datetime,
        device_limit: int,
        traffic_limit_bytes: int,
        squads: list[str],
    ) -> RemoteUser:
        return RemoteUser(
            uuid=user_uuid,
            short_uuid=secrets.token_urlsafe(10),
            username=f"mock_{user_uuid[:8]}",
            subscription_url=(f"{self.settings.public_base_url.rstrip('/')}/demo/sub/{user_uuid}"),
            expire_at=expires_at,
            device_limit=device_limit,
        )

    async def revoke_subscription(self, user_uuid: str) -> RemoteUser | None:
        return None

    async def set_device_limit(self, user_uuid: str, device_limit: int) -> None:
        return None

    async def list_hwid_devices(self, user_uuid: str) -> list[dict]:
        return []

    async def delete_hwid_device(self, user_uuid: str, hwid: str) -> None:
        return None

    async def list_nodes_summary(self) -> list[dict]:
        return []


def make_remnawave_gateway(settings: Settings) -> RemnawaveGateway:
    if settings.remnawave_mock:
        return MockRemnawaveClient(settings)
    return RemnawaveClient(settings)
