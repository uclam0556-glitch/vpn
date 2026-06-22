from hamalivpn.deeplinks import (
    happ_deeplink,
    hiddify_deeplink,
    streisand_deeplink,
    v2raytun_deeplink,
)


def test_hiddify_deeplink_encodes_subscription_url() -> None:
    link = hiddify_deeplink("https://sub.example.com/a?x=1", "HamaliVpn")
    assert link == "hiddify://import/https%3A%2F%2Fsub.example.com%2Fa%3Fx%3D1#HamaliVpn"


def test_v2raytun_deeplink_uses_supported_import_scheme() -> None:
    assert (
        v2raytun_deeplink("https://sub.example.com/abc")
        == "v2raytun://import/https://sub.example.com/abc"
    )


def test_happ_deeplink_uses_happ_add_scheme() -> None:
    link = happ_deeplink("https://sub.example.com/a")
    assert link == "happ://add/https://sub.example.com/a"


def test_happ_deeplink_keeps_url_raw() -> None:
    url = "https://panel.1.2.3.4.sslip.io/api/sub/abc123"
    assert happ_deeplink(url) == f"happ://add/{url}"


def test_streisand_deeplink_uses_import_scheme() -> None:
    link = streisand_deeplink("https://sub.example.com/abc")
    assert link.startswith("streisand://import/")
    assert "https%3A%2F%2F" in link


def test_streisand_deeplink_encodes_url_correctly() -> None:
    url = "https://panel.1.2.3.4.sslip.io/api/sub/abc123"
    link = streisand_deeplink(url)
    assert link == "streisand://import/https%3A%2F%2Fpanel.1.2.3.4.sslip.io%2Fapi%2Fsub%2Fabc123"
