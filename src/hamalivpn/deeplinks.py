from urllib.parse import quote


def hiddify_deeplink(subscription_url: str, name: str = "HamaliVpn") -> str:
    encoded_url = quote(subscription_url, safe="")
    encoded_name = quote(name, safe="")
    return f"hiddify://import/{encoded_url}#{encoded_name}"


def v2raytun_deeplink(subscription_url: str) -> str:
    return f"v2raytun://import/{subscription_url}"


def happ_deeplink(subscription_url: str) -> str:
    """Happ Proxy Utility — iOS, Android, Windows, macOS, Linux."""
    encoded_url = quote(subscription_url, safe="")
    return f"happ://sub/{encoded_url}"


def streisand_deeplink(subscription_url: str) -> str:
    """Streisand — iOS и macOS."""
    encoded_url = quote(subscription_url, safe="")
    return f"streisand://import/{encoded_url}"
