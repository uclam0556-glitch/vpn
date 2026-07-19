import base64
import json
import urllib.parse

from hamalivpn.sub_injector import (
    CLUSTER_REMARKS,
    STANDALONE_CLUSTER_TAGS,
    extract_subscription_token,
    incy_compatible_link,
    incy_integrated_links,
    incy_whitelist_routing_link,
    is_incy_request,
    reality_share_link,
    remnawave_subscription_path,
)


def test_happ_standalone_clusters_include_new_france_once() -> None:
    assert STANDALONE_CLUSTER_TAGS.count("fr-new") == 1
    assert CLUSTER_REMARKS["fr-new"] == "🇫🇷 Франция (Новая)"


def test_subscription_path_accepts_supported_public_tokens() -> None:
    token = "Abc_1234-valid-token"

    assert extract_subscription_token(f"/api/sub/{token}?cluster=all") == token
    assert extract_subscription_token(f"/{token}") == token
    assert extract_subscription_token("/health") == ""
    assert extract_subscription_token("/api/internal/nodes") == ""


def test_remnawave_subscription_path_preserves_query_string() -> None:
    assert remnawave_subscription_path("target-token", "/source?cluster=all") == (
        "/api/sub/target-token?cluster=all"
    )


def test_incy_hysteria_link_uses_canonical_scheme_and_bandwidth() -> None:
    original = "hy2://token@example.com:8443?sni=example.org&obfs=salamander#France"
    result = incy_compatible_link(original)

    assert result.startswith("hysteria2://token@example.com:8443?")
    assert "sni=example.org" in result
    assert "obfs=salamander" in result
    assert "insecure=1" in result
    assert "up=60" in result
    assert "down=200" in result
    assert result.endswith("#France")


def test_incy_link_normalization_leaves_other_protocols_unchanged() -> None:
    link = "vless://uuid@example.com:443?security=reality#Node"
    assert incy_compatible_link(link) == link


def test_incy_flattens_integrated_xray_json_without_mutating_source() -> None:
    config = {
        "remarks": "Provider profile",
        "outbounds": [
            {
                "tag": "France",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "vpn.example.com",
                            "port": 443,
                            "users": [
                                {
                                    "id": "00000000-0000-0000-0000-000000000001",
                                    "encryption": "none",
                                    "flow": "xtls-rprx-vision",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "tls",
                    "tlsSettings": {
                        "serverName": ["www.example.com"],
                        "fingerprint": "firefox",
                        "alpn": ["h2", "http/1.1"],
                        "pinnedPeerCertSha256": ["certificate-pin"],
                    },
                    "xhttpSettings": {
                        "path": "/api/tunnel",
                        "host": "www.example.com",
                        "mode": "auto",
                        "extra": {
                            "xmux": {"maxConcurrency": "8-16"},
                            "xPaddingBytes": "100-1000",
                        },
                    },
                },
            },
            {"tag": "direct", "protocol": "freedom"},
        ],
    }
    original = json.loads(json.dumps(config))

    links = incy_integrated_links(
        [{"raw_link": json.dumps(config), "display_name": "[Резерв] Франция"}]
    )

    assert len(links) == 1
    assert links[0].startswith("vless://00000000-0000-0000-0000-000000000001@vpn.example.com:443?")
    parsed = urllib.parse.urlsplit(links[0])
    params = urllib.parse.parse_qs(parsed.query)
    assert params["type"] == ["xhttp"]
    assert params["security"] == ["tls"]
    assert params["sni"] == ["www.example.com"]
    assert params["alpn"] == ["h2,http/1.1"]
    assert params["pcs"] == ["certificate-pin"]
    assert params["path"] == ["/api/tunnel"]
    assert params["host"] == ["www.example.com"]
    assert params["mode"] == ["auto"]
    assert (
        json.loads(params["extra"][0])
        == config["outbounds"][0]["streamSettings"]["xhttpSettings"]["extra"]
    )
    assert (
        "%5B%D0%A0%D0%B5%D0%B7%D0%B5%D1%80%D0%B2%5D%20%D0%A4%D1%80%D0%B0%D0%BD%D1%86%D0%B8%D1%8F"
        in links[0]
    )
    assert config == original


def test_incy_integrated_links_deduplicate_connections_and_normalize_hysteria() -> None:
    first = "vless://uuid@example.com:443?security=reality&type=tcp#First"
    duplicate = "vless://uuid@example.com:443?type=tcp&security=reality#Second"
    hysteria = "hy2://secret@lte.example.com:443?sni=lte.example.com#LTE"

    links = incy_integrated_links([first, duplicate, hysteria])

    assert len(links) == 2
    assert links[0] == first
    assert links[1].startswith("hysteria2://")
    assert "insecure=1" in links[1]


def test_incy_request_is_detected_by_query_and_official_headers() -> None:
    class Handler:
        path = "/token?client=incy"
        headers = {}

    assert is_incy_request(Handler())

    Handler.path = "/token"
    Handler.headers = {"User-Agent": "INCY/1.2/iOS", "x-client": "INCY"}
    assert is_incy_request(Handler())


def test_generated_germany_link_is_valid_reality_vless() -> None:
    link = reality_share_link(
        "00000000-0000-0000-0000-000000000001",
        "192.0.2.10",
        443,
        "example.org",
        "public-key",
        "short-id",
        "Germany",
    )

    assert link.startswith("vless://00000000-0000-0000-0000-000000000001@192.0.2.10:443?")
    assert "security=reality" in link
    assert "flow=xtls-rprx-vision" in link
    assert link.endswith("#Germany")


def test_incy_whitelist_uses_native_routing_profile() -> None:
    link = incy_whitelist_routing_link()
    payload = link.removeprefix("incy://routing/onadd/")
    payload += "=" * (-len(payload) % 4)
    profile = json.loads(base64.urlsafe_b64decode(payload))

    assert profile["Name"] == "HamaliVPN — Белые списки"
    assert "geosite:category-ru" in profile["DirectSites"]
    assert "geoip:ru" in profile["DirectIp"]
    assert profile["GlobalProxy"] == "true"
