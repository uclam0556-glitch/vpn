import base64
import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from hamalivpn.api import app, get_session
from hamalivpn.integration import (
    _compact_node_name,
    _ensure_public_subscription_url,
    clean_node_display_name,
    parse_node_address,
    parse_subscription_content,
    renamed_node_display_name,
    synchronize_integration_nodes,
)
from hamalivpn.models import IntegrationLink, IntegrationNode


def test_parse_plain_and_base64_subscription() -> None:
    raw = "vless://uuid@example.com:443?security=reality#France%20Reserve"

    assert parse_subscription_content(raw) == [{"raw_link": raw, "original_name": "France Reserve"}]

    encoded = base64.b64encode(raw.encode()).decode().rstrip("=")
    assert parse_subscription_content(encoded) == [
        {"raw_link": raw, "original_name": "France Reserve"}
    ]

    urlsafe_raw = "vless://uuid@example.com:443?security=reality#𐀾"
    urlsafe = base64.urlsafe_b64encode(urlsafe_raw.encode()).decode().rstrip("=")
    assert "-" in urlsafe or "_" in urlsafe
    assert parse_subscription_content(urlsafe) == [{"raw_link": urlsafe_raw, "original_name": "𐀾"}]


def test_compact_node_name_keeps_outbound_tag_visible() -> None:
    result = _compact_node_name("[Резерв] Ультра (Белые списки LTE) Россия · youtube-4", max_len=38)

    assert len(result) <= 38
    assert result.endswith("· youtube-4")


def test_rename_removes_reserve_prefix_and_preserves_existing_country_flag() -> None:
    assert clean_node_display_name("  [Резерв]   Австрия 🇦🇹 ") == "Австрия 🇦🇹"
    assert (
        renamed_node_display_name(
            "Моё имя",
            original_name="🇫🇲 Анти Заглушка - 2",
            current_name="анти заглушка Ham",
        )
        == "🇫🇲 Моё имя"
    )
    assert (
        renamed_node_display_name(
            "Быстрый сервер 🇺🇸",
            original_name="Австрия 🇦🇹",
            current_name="[Резерв] Австрия 🇦🇹",
        )
        == "Быстрый сервер 🇦🇹"
    )


def test_parse_xray_json_preserves_complete_profile() -> None:
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
    assert json.loads(nodes[0]["raw_link"]) == document


def test_parse_xray_json_list_keeps_every_outbound_as_lossless_profile() -> None:
    documents = [
        {
            "remarks": name,
            "routing": {"rules": [{"outboundTag": "youtube"}]},
            "outbounds": [
                {"protocol": "vless", "tag": "proxy"},
                {"protocol": "vless", "tag": "youtube"},
                {"protocol": "freedom", "tag": "direct"},
            ],
        }
        for name in ("Spain", "Austria")
    ]

    nodes = parse_subscription_content(json.dumps(documents))

    assert [node["original_name"] for node in nodes] == [
        "Spain",
        "Spain · youtube",
        "Austria",
        "Austria · youtube",
    ]
    assert len(nodes) == 4
    for node in nodes:
        profile = json.loads(node["raw_link"])
        proxy_outbounds = [
            outbound for outbound in profile["outbounds"] if outbound.get("protocol") == "vless"
        ]
        assert len(proxy_outbounds) == 1
        assert profile["remarks"] == node["original_name"]
        assert any(outbound.get("protocol") == "freedom" for outbound in profile["outbounds"])

    youtube = json.loads(nodes[1]["raw_link"])
    assert youtube["outbounds"][0]["tag"] == "youtube"
    assert youtube["routing"]["rules"] == [{"outboundTag": "youtube"}]


def test_parse_node_address() -> None:
    assert parse_node_address("vless://uuid@vpn.example.com:8443#France") == (
        "vpn.example.com",
        8443,
    )
    full_profile = {
        "outbounds": [
            {
                "protocol": "vless",
                "tag": "proxy",
                "settings": {
                    "vnext": [
                        {
                            "address": "primary.example.com",
                            "port": 443,
                            "users": [{"id": "uuid"}],
                        }
                    ]
                },
            },
            {
                "protocol": "vless",
                "tag": "youtube",
                "settings": {
                    "vnext": [
                        {
                            "address": "auxiliary.example.com",
                            "port": 8443,
                            "users": [{"id": "uuid-2"}],
                        }
                    ]
                },
            },
        ]
    }
    assert parse_node_address(json.dumps(full_profile)) == ("primary.example.com", 443)
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
    assert response.json() == {
        "nodes": ["vless://active@example.com:443#Active"],
        "items": [
            {
                "raw_link": "vless://active@example.com:443#Active",
                "display_name": "Active",
                "original_name": "Active",
            }
        ],
    }


@pytest.mark.asyncio
async def test_snapshot_sync_migrates_flattened_profiles_and_preserves_active_state(
    session_factory,
) -> None:
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
                    raw_link=f"vless://uuid-{index}@example.com:443?type=xhttp#Austria",
                    original_name="Austria",
                    display_name="Австрия — резерв" if index == 2 else "[Резерв] Austria",
                    is_active=index == 2,
                )
                for index in range(3)
            ]
            + [
                IntegrationNode(
                    link_id=link.id,
                    raw_link="vless://spain@example.com:443?type=xhttp#Spain",
                    original_name="Spain",
                    display_name="Испания",
                    is_active=True,
                )
            ]
        )
        await session.commit()

        incoming = [
            {
                "raw_link": json.dumps(
                    {"remarks": name, "outbounds": [{"protocol": "vless", "tag": "proxy"}]}
                ),
                "original_name": name,
            }
            for name in ("Austria", "Spain")
        ]
        changes = await synchronize_integration_nodes(session, link.id, incoming)
        await session.commit()

        result = await session.execute(
            IntegrationNode.__table__.select()
            .where(IntegrationNode.link_id == link.id)
            .order_by(IntegrationNode.original_name)
        )
        rows = result.mappings().all()

    assert changes == {"added": 0, "updated": 2, "removed": 2}
    assert len(rows) == 2
    assert all(row["is_active"] for row in rows)
    assert rows[0]["display_name"] == "Австрия — резерв"
    assert rows[1]["display_name"] == "Испания"
    assert all(row["raw_link"].startswith("{") for row in rows)


@pytest.mark.asyncio
async def test_snapshot_sync_creates_new_nodes_without_legacy_reserve_prefix(
    session_factory,
) -> None:
    async with session_factory() as session:
        link = IntegrationLink(
            url="https://provider.example/new",
            hwid="0123456789abcdef",
            user_agent="Happ/test",
        )
        session.add(link)
        await session.flush()

        changes = await synchronize_integration_nodes(
            session,
            link.id,
            [
                {
                    "raw_link": "vless://austria@example.com:443#Austria",
                    "original_name": "Австрия 🇦🇹",
                }
            ],
        )
        await session.commit()
        node = (
            (
                await session.execute(
                    IntegrationNode.__table__.select().where(IntegrationNode.link_id == link.id)
                )
            )
            .mappings()
            .one()
        )

    assert changes == {"added": 1, "updated": 0, "removed": 0}
    assert node["display_name"] == "Австрия 🇦🇹"
