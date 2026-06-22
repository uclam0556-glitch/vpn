import base64

import httpx
import pytest

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
