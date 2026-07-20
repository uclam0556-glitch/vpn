import json
from datetime import UTC, datetime

import httpx
import pytest

from hamalivpn.config import Settings
from hamalivpn.remnawave import RemnawaveClient, RemnawaveNotFoundError


@pytest.mark.asyncio
async def test_create_user_matches_official_remnawave_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/users"
        assert request.headers["Authorization"] == "Bearer api-token"
        payload = json.loads(request.content)
        assert payload["telegramId"] == 123456
        assert payload["hwidDeviceLimit"] == 1
        assert payload["trafficLimitBytes"] == 30 * 1024**3
        assert payload["activeInternalSquads"] == ["87d08c48-13ad-4f60-bf35-a1e639d82af0"]
        return httpx.Response(
            201,
            json={
                "response": {
                    "uuid": "1e74ddcf-80c0-45ce-96ee-0338cab97b75",
                    "shortUuid": "short-token",
                    "username": payload["username"],
                    "subscriptionUrl": "https://panel.example/api/sub/short-token",
                    "expireAt": payload["expireAt"],
                    "hwidDeviceLimit": 1,
                }
            },
        )

    settings = Settings(
        panel_base_url="https://panel.example",
        remnawave_api_token="api-token",
        remnawave_mock=False,
    )
    client = RemnawaveClient(settings, transport=httpx.MockTransport(handler))
    result = await client.create_user(
        username="tg_123456_test",
        telegram_id=123456,
        expires_at=datetime(2026, 6, 22, 20, 0, tzinfo=UTC),
        device_limit=1,
        traffic_limit_bytes=30 * 1024**3,
        squads=["87d08c48-13ad-4f60-bf35-a1e639d82af0"],
        description="HamaliVpn test",
    )

    assert result.short_uuid == "short-token"
    assert result.subscription_url.endswith("/short-token")


@pytest.mark.asyncio
async def test_update_user_access_reactivates_existing_user() -> None:
    user_uuid = "1e74ddcf-80c0-45ce-96ee-0338cab97b75"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/api/users"
        payload = json.loads(request.content)
        assert payload["uuid"] == user_uuid
        assert payload["status"] == "ACTIVE"
        assert payload["trafficLimitBytes"] == 0
        assert payload["hwidDeviceLimit"] == 1
        return httpx.Response(
            200,
            json={
                "response": {
                    "uuid": user_uuid,
                    "shortUuid": "renewed-short-token",
                    "username": "tg_123456_test",
                    "subscriptionUrl": "https://panel.example/api/sub/renewed-short-token",
                    "expireAt": payload["expireAt"],
                    "hwidDeviceLimit": 1,
                }
            },
        )

    settings = Settings(
        panel_base_url="https://panel.example",
        remnawave_api_token="api-token",
        remnawave_mock=False,
    )
    client = RemnawaveClient(settings, transport=httpx.MockTransport(handler))
    result = await client.update_user_access(
        user_uuid=user_uuid,
        expires_at=datetime(2036, 6, 22, 20, 0, tzinfo=UTC),
        device_limit=1,
        traffic_limit_bytes=0,
        squads=["87d08c48-13ad-4f60-bf35-a1e639d82af0"],
    )

    assert result.short_uuid == "renewed-short-token"


@pytest.mark.asyncio
async def test_not_found_has_a_specific_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errorCode": "A025", "message": "User not found"})

    settings = Settings(
        panel_base_url="https://panel.example",
        remnawave_api_token="api-token",
        remnawave_mock=False,
    )
    client = RemnawaveClient(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(RemnawaveNotFoundError):
        await client.disable_user("1e74ddcf-80c0-45ce-96ee-0338cab97b75")


@pytest.mark.asyncio
async def test_node_summary_exposes_metrics_without_configuration_secrets() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/nodes"
        return httpx.Response(
            200,
            json={
                "response": [
                    {
                        "name": "France",
                        "countryCode": "FR",
                        "isConnected": True,
                        "isDisabled": False,
                        "usersOnline": 4,
                        "trafficUsedBytes": 2048,
                        "configProfile": {"privateKey": "must-not-leak"},
                        "system": {
                            "info": {"cpus": 4, "memoryTotal": 1000},
                            "stats": {
                                "memoryUsed": 250,
                                "loadAvg": [0.4, 0.2, 0.1],
                                "interface": {"rxBytesPerSec": 125000, "txBytesPerSec": 62500},
                            },
                        },
                    }
                ]
            },
        )

    settings = Settings(
        panel_base_url="https://panel.example",
        remnawave_api_token="api-token",
        remnawave_mock=False,
    )
    client = RemnawaveClient(settings, transport=httpx.MockTransport(handler))
    rows = await client.list_nodes_summary()

    assert rows == [
        {
            "name": "France",
            "country_code": "FR",
            "connected": True,
            "disabled": False,
            "users_online": 4,
            "traffic_used_bytes": 2048,
            "rx_mbps": 1.0,
            "tx_mbps": 0.5,
            "cpu_percent": 10.0,
            "memory_percent": 25.0,
            "updated_at": None,
        }
    ]
    assert "private" not in json.dumps(rows).lower()
