import json
import logging
import os
import shutil
import subprocess
from urllib.parse import quote

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

    script = """
const { encryptLink } = require('@incy/link-encoder');
const payload = JSON.parse(process.argv[1]);
process.stdout.write(encryptLink(payload.url, { name: payload.name || 'HamaliVPN' }));
""".strip()

    try:
        result = subprocess.run(
            [node_bin, "-e", script, json.dumps({"url": subscription_url, "name": name})],
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
