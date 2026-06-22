from urllib.parse import quote


def hiddify_deeplink(subscription_url: str, name: str = "HamaliVpn") -> str:
    encoded_url = quote(subscription_url, safe="")
    encoded_name = quote(name, safe="")
    return f"hiddify://import/{encoded_url}#{encoded_name}"


def v2raytun_deeplink(subscription_url: str) -> str:
    return f"v2raytun://import/{subscription_url}"
