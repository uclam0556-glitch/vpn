from hamalivpn.deeplinks import hiddify_deeplink, v2raytun_deeplink


def test_hiddify_deeplink_encodes_subscription_url() -> None:
    link = hiddify_deeplink("https://sub.example.com/a?x=1", "HamaliVpn")
    assert link == "hiddify://import/https%3A%2F%2Fsub.example.com%2Fa%3Fx%3D1#HamaliVpn"


def test_v2raytun_deeplink_uses_supported_import_scheme() -> None:
    assert (
        v2raytun_deeplink("https://sub.example.com/abc")
        == "v2raytun://import/https://sub.example.com/abc"
    )
