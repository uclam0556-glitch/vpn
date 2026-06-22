import asyncio
import base64
import binascii
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpx

SUPPORTED_SCHEMES = {
    "vless",
    "vmess",
    "trojan",
    "ss",
    "hysteria2",
    "hy2",
    "tuic",
}
EMPTY_HOST_MARKERS = (
    "no hosts found",
    "check hosts tab",
    "check internal squads tab",
)


@dataclass(frozen=True, slots=True)
class SubscriptionEndpoint:
    scheme: str
    host: str | None
    port: int | None
    name: str


@dataclass(frozen=True, slots=True)
class SubscriptionProbeResult:
    status: str
    message: str
    endpoint_count: int
    reachable_count: int
    response_ms: int | None
    endpoints: tuple[SubscriptionEndpoint, ...] = ()

    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"


def _add_base64_padding(value: str) -> str:
    return value + ("=" * (-len(value) % 4))


def _decode_base64(value: str) -> str | None:
    compact = "".join(value.split())
    if not compact:
        return None
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            decoded = decoder(_add_base64_padding(compact)).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            continue
        if "://" in decoded or any(marker in decoded.lower() for marker in EMPTY_HOST_MARKERS):
            return decoded
    return None


def decode_subscription_body(body: str) -> str:
    stripped = body.strip()
    decoded = _decode_base64(stripped)
    return decoded if decoded is not None else stripped


def parse_subscription_endpoints(body: str) -> tuple[SubscriptionEndpoint, ...]:
    decoded = decode_subscription_body(body)
    endpoints: list[SubscriptionEndpoint] = []
    for raw_line in decoded.replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line or "://" not in line:
            continue
        parsed = urlsplit(line)
        scheme = parsed.scheme.lower()
        if scheme not in SUPPORTED_SCHEMES:
            continue
        try:
            port = parsed.port
        except ValueError:
            port = None
        name = unquote(parsed.fragment).strip() or f"{scheme.upper()} endpoint"
        endpoints.append(
            SubscriptionEndpoint(
                scheme=scheme,
                host=parsed.hostname,
                port=port,
                name=name,
            )
        )
    return tuple(endpoints)


async def _tcp_reachable(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        async with asyncio.timeout(timeout_seconds):
            _, writer = await asyncio.open_connection(host, port)
    except (TimeoutError, OSError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


async def probe_subscription_url(
    subscription_url: str,
    *,
    timeout_seconds: float = 8,
    user_agent: str = "Happ/4.11.0/ios/2606031844510",
    transport: httpx.AsyncBaseTransport | None = None,
) -> SubscriptionProbeResult:
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": user_agent,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            transport=transport,
        ) as client:
            response = await client.get(subscription_url)
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as error:
        return SubscriptionProbeResult(
            status="unreachable",
            message=f"Подписка недоступна: {type(error).__name__}",
            endpoint_count=0,
            reachable_count=0,
            response_ms=None,
        )

    response_ms = round((time.monotonic() - started) * 1000)
    decoded = decode_subscription_body(response.text)
    endpoints = parse_subscription_endpoints(response.text)
    if not endpoints:
        message = (
            "Remnawave не вернул доступные Host"
            if any(marker in decoded.lower() for marker in EMPTY_HOST_MARKERS)
            else "В подписке нет поддерживаемых серверов"
        )
        return SubscriptionProbeResult(
            status="empty",
            message=message,
            endpoint_count=0,
            reachable_count=0,
            response_ms=response_ms,
        )

    tcp_endpoints = {
        (endpoint.host, endpoint.port)
        for endpoint in endpoints
        if endpoint.host and endpoint.port and endpoint.scheme not in {"hysteria2", "hy2", "tuic"}
    }
    checks = [_tcp_reachable(host, port, min(timeout_seconds, 4)) for host, port in tcp_endpoints]
    check_results = await asyncio.gather(*checks) if checks else []
    reachable_count = sum(check_results)
    if tcp_endpoints and reachable_count == 0:
        return SubscriptionProbeResult(
            status="degraded",
            message="Серверы есть, но TCP-порты недоступны с control-сервера",
            endpoint_count=len(endpoints),
            reachable_count=0,
            response_ms=response_ms,
            endpoints=endpoints,
        )

    return SubscriptionProbeResult(
        status="healthy",
        message=f"Готово серверов: {len(endpoints)}",
        endpoint_count=len(endpoints),
        reachable_count=reachable_count,
        response_ms=response_ms,
        endpoints=endpoints,
    )
