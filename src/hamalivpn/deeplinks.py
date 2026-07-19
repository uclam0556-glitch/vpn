import json
import logging
import os
import shutil
import subprocess
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)


def hiddify_deeplink(subscription_url: str, name: str = "HamaliVpn") -> str:
    encoded_url = quote(subscription_url, safe="")
    encoded_name = quote(name, safe="")
    return f"hiddify://import/{encoded_url}#{encoded_name}"


def v2raytun_deeplink(subscription_url: str) -> str:
    return f"v2raytun://import/{subscription_url}"


def happ_deeplink(subscription_url: str) -> str:
    """Happ Proxy Utility — канонический формат Remnawave: happ://add/<сырой url>.

    Happ ждёт ссылку подписки без кодирования; base64/percent ломают разбор
    и дают «неизвестное действие».
    """
    return f"happ://add/{subscription_url}"


def streisand_deeplink(subscription_url: str) -> str:
    """Streisand — iOS и macOS."""
    encoded_url = quote(subscription_url, safe="")
    return f"streisand://import/{encoded_url}"


def with_query_parameter(url: str, name: str, value: str) -> str:
    """Return *url* with one idempotently replaced query parameter."""

    parsed = urlsplit(url)
    query = [
        (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True) if key != name
    ]
    query.append((name, value))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def incy_subscription_url(subscription_url: str) -> str:
    """Dedicated subscription URL that asks the injector for INCY-safe links."""

    return with_query_parameter(subscription_url, "client", "incy") if subscription_url else ""


def incy_deeplink(subscription_url: str, name: str = "HamaliVPN") -> str:
    """INCY encrypted import link.

    INCY does not use a plain `incy://add/<url>` scheme. Its public encoder
    produces `incy://crypt1/<payload>` links via `@incy/link-encoder`, so the
    raw subscription URL is not exposed in chat/browser history.

    If Node or the encoder package is unavailable, return an empty string: the
    template will hide the INCY button instead of showing a broken link.
    """

    if not subscription_url:
        return ""

    node_bin = shutil.which(os.getenv("INCY_NODE_BIN", "node"))
    if not node_bin:
        return ""

    dedicated_url = incy_subscription_url(subscription_url)
    script = """
const { encryptLink } = require('@incy/link-encoder');
const payload = JSON.parse(process.argv[1]);
process.stdout.write(encryptLink(payload.url, { name: payload.name || 'HamaliVPN' }));
""".strip()

    try:
        result = subprocess.run(
            [node_bin, "-e", script, json.dumps({"url": dedicated_url, "name": name})],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception as exc:
        logger.warning("Could not generate INCY deeplink: %s", exc)
        return ""

    link = result.stdout.strip()
    return link if link.startswith("incy://crypt1/") else ""
