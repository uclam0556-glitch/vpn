import base64
import json

from hamalivpn.sub_injector import (
    CLUSTER_REMARKS,
    STANDALONE_CLUSTER_TAGS,
    extract_subscription_token,
    incy_compatible_link,
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
