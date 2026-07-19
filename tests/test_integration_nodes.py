import base64
import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from hamalivpn.api import app, get_session
from hamalivpn.integration import (
    _ensure_public_subscription_url,
    parse_node_address,
    parse_subscription_content,
)
from hamalivpn.models import IntegrationLink, IntegrationNode


def test_parse_plain_and_base64_subscription() -> None:
    raw = "vless://uuid@example.com:443?security=reality#France%20Reserve"

    assert parse_subscription_content(raw) == [{"raw_link": raw, "original_name": "France Reserve"}]

    encoded = base64.b64encode(raw.encode()).decode().rstrip("=")
    assert parse_subscription_content(encoded) == [
        {"raw_link": raw, "original_name": "France Reserve"}
    ]


def test_parse_xray_json_into_vless_uri() -> None:
    document = {
        "remarks": "Imported France",
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "vpn.example.com",
                            "port": 443,
                            "users": [{"id": "uuid-value", "flow": "xtls-rprx-vision"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "publicKey": "public-key",
                        "serverName": "www.example.com",
                        "fingerprint": "chrome",
                        "shortId": "abcd",
                    },
                },
            }
        ],
    }

    nodes = parse_subscription_content(json.dumps(document))

    assert len(nodes) == 1
    assert nodes[0]["original_name"] == "Imported France"
    assert nodes[0]["raw_link"].startswith("vless://uuid-value@vpn.example.com:443?")
    assert "security=reality" in nodes[0]["raw_link"]


def test_parse_node_address() -> None:
    assert parse_node_address("vless://uuid@vpn.example.com:8443#France") == (
        "vpn.example.com",
        8443,
    )
    assert parse_node_address("not-a-node") == (None, None)


@pytest.mark.asyncio
async def test_subscription_fetch_rejects_private_targets() -> None:
    with pytest.raises(ValueError, match="Локальные"):
        await _ensure_public_subscription_url("http://127.0.0.1/private")


@pytest.mark.asyncio
async def test_internal_nodes_endpoint_returns_only_active_nodes(session_factory) -> None:
    async with session_factory() as session:
        link = IntegrationLink(
            url="https://provider.example/subscription",
            hwid="0123456789abcdef",
            user_agent="Happ/test",
        )
        session.add(link)
        await session.flush()
        session.add_all(
            [
                IntegrationNode(
                    link_id=link.id,
                    raw_link="vless://active@example.com:443#Active",
                    original_name="Active",
                    display_name="Active",
                    is_active=True,
                ),
                IntegrationNode(
                    link_id=link.id,
                    raw_link="vless://disabled@example.com:443#Disabled",
                    original_name="Disabled",
                    display_name="Disabled",
                    is_active=False,
                ),
            ]
        )
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).get("/api/internal/integrated_nodes")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"nodes": ["vless://active@example.com:443#Active"]}
