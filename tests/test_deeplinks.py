import json

from hamalivpn.deeplinks import (
    happ_deeplink,
    hiddify_deeplink,
    incy_deeplink,
    incy_integrated_deeplink,
    incy_integrated_subscription_url,
    incy_subscription_url,
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


def test_incy_deeplink_uses_official_crypt_scheme(monkeypatch) -> None:
    class Result:
        stdout = "incy://crypt1/encrypted-payload"

    calls = {}

    def fake_which(name: str) -> str:
        calls["node_name"] = name
        return "/usr/bin/node"

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr("hamalivpn.deeplinks.shutil.which", fake_which)
    monkeypatch.setattr("hamalivpn.deeplinks.subprocess.run", fake_run)

    link = incy_deeplink("https://sub.example.com/a", "HamaliVPN")

    assert link == "incy://crypt1/encrypted-payload"
    assert calls["node_name"] == "node"
    assert calls["cmd"][0] == "/usr/bin/node"
    payload = json.loads(calls["cmd"][-1])
    assert payload["url"] == "https://sub.example.com/a?client=incy"
    assert calls["kwargs"]["timeout"] == 2


def test_incy_deeplink_hides_button_when_encoder_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("hamalivpn.deeplinks.shutil.which", lambda _name: None)
    assert incy_deeplink("https://sub.example.com/a") == ""


def test_incy_subscription_url_preserves_and_replaces_query() -> None:
    assert incy_subscription_url("https://sub.example.com/a?slot=1") == (
        "https://sub.example.com/a?slot=1&client=incy"
    )
    assert incy_subscription_url("https://sub.example.com/a?client=old&slot=1") == (
        "https://sub.example.com/a?slot=1&client=incy"
    )


def test_incy_integrated_subscription_uses_separate_full_config_variant(monkeypatch) -> None:
    class Result:
        stdout = "incy://crypt1/full-config-profile"

    calls = {}
    monkeypatch.setattr("hamalivpn.deeplinks.shutil.which", lambda _name: "/usr/bin/node")

    def fake_run(cmd, **kwargs):
        calls["payload"] = json.loads(cmd[-1])
        return Result()

    monkeypatch.setattr("hamalivpn.deeplinks.subprocess.run", fake_run)
    source = "https://sub.example.com/a?slot=1&client=old"

    assert incy_integrated_subscription_url(source) == (
        "https://sub.example.com/a?slot=1&client=incy-integrated"
    )
    assert incy_integrated_deeplink(source) == "incy://crypt1/full-config-profile"
    assert calls["payload"]["url"].endswith("slot=1&client=incy-integrated")
