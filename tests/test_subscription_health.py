import base64

import httpx
import pytest

import hamalivpn.subscription_health as subscription_health
from hamalivpn.subscription_health import (
    decode_subscription_body,
    parse_subscription_endpoints,
    probe_subscription_url,
)


def encoded_subscription(*lines: str) -> str:
    return base64.b64encode("\n".join(lines).encode()).decode()


def test_decodes_and_parses_remnawave_base64_subscription() -> None:
    body = encoded_subscription(
        "vless://uuid@node.example:443?type=xhttp#DE%20XHTTP",
        "vless://uuid@node.example:8443?type=tcp#DE%20Mobile%20RAW",
    )

    decoded = decode_subscription_body(body)
    endpoints = parse_subscription_endpoints(body)

    assert "vless://" in decoded
    assert [endpoint.name for endpoint in endpoints] == ["DE XHTTP", "DE Mobile RAW"]
    assert [endpoint.port for endpoint in endpoints] == [443, 8443]


@pytest.mark.asyncio
async def test_probe_detects_empty_hosts_response() -> None:
    body = encoded_subscription(
        "→ Remnawave",
        "→ No hosts found",
        "→ Check Hosts tab",
        "→ Check Internal Squads tab",
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    result = await probe_subscription_url(
        "https://panel.example/api/sub/test",
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "empty"
    assert result.endpoint_count == 0
    assert "Host" in result.message


@pytest.mark.asyncio
async def test_probe_reports_subscription_endpoints() -> None:
    body = encoded_subscription(
        "hysteria2://secret@node.example:443#DE%20Hysteria2",
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    result = await probe_subscription_url(
        "https://panel.example/api/sub/test",
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "healthy"
    assert result.endpoint_count == 1
    assert result.endpoints[0].scheme == "hysteria2"


@pytest.mark.asyncio
async def test_probe_sends_extra_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    body = encoded_subscription("vless://uuid@node.example:443?type=tcp#Node")
    seen_header = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_header
        seen_header = request.headers.get("X-Proxy-Bypass")
        return httpx.Response(200, text=body)

    async def fake_tcp_reachable(host: str, port: int, timeout_seconds: float) -> bool:
        return True

    monkeypatch.setattr(subscription_health, "_tcp_reachable", fake_tcp_reachable)

    result = await probe_subscription_url(
        "https://panel.example/api/sub/test",
        extra_headers={"X-Proxy-Bypass": "true"},
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "healthy"
    assert seen_header == "true"


@pytest.mark.asyncio
async def test_probe_marks_partial_tcp_reachability_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = encoded_subscription(
        "vless://uuid@fast.example:443?type=tcp#Fast",
        "vless://uuid@dead.example:2053?type=tcp#Dead",
        "hy2://secret@lte.example:8443#LTE",
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async def fake_tcp_reachable(host: str, port: int, timeout_seconds: float) -> bool:
        return host == "fast.example" and port == 443

    monkeypatch.setattr(subscription_health, "_tcp_reachable", fake_tcp_reachable)

    result = await probe_subscription_url(
        "https://panel.example/api/sub/test",
        transport=httpx.MockTransport(handler),
    )

    assert result.status == "degraded"
    assert result.endpoint_count == 3
    assert result.reachable_count == 1
    assert "TCP доступно: 1/2" in result.message
