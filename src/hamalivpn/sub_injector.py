import base64
import datetime
import json
import os
import re
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOT_URL = "https://t.me/HamaliVpn_bot"
PROFILE_TITLE = "HamaliVpn"
PROFILE_TOTAL_BYTES = 10 * 1024 * 1024 * 1024 * 1024
PROFILE_NOTE = (
    "Нажмите ⓘ для перехода в бот: продление, поддержка и управление подпиской. "
    "Спасибо, что выбрали HamaliVPN. Если возникнут проблемы — напишите нам."
)
STANDALONE_CLUSTER_TAGS = ("nl", "fr", "fr-new", "uk", "fi", "de")
CLUSTER_REMARKS = {
    "nl": "🇳🇱 Нидерланды",
    "fr": "🇫🇷 Франция",
    "fr-new": "🇫🇷 Франция (Новая)",
    "de": "🇩🇪 Германия",
    "uk": "🇬🇧 Юнайтед Кингдом",
    "fi": "🇫🇮 Финляндия",
}
INCY_PROFILE_HEADERS = {"sort-order": "ping"}


def b64_text(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def parse_expires_at(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.datetime.fromtimestamp(value, datetime.UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


def profile_days_left(meta):
    expires_at = parse_expires_at((meta or {}).get("expires_at"))
    if not expires_at:
        return None, None
    seconds_left = (expires_at - datetime.datetime.now(datetime.UTC)).total_seconds()
    days_left = max(0, int((seconds_left + 86399) // 86400))
    return days_left, expires_at


def profile_description(meta=None):
    days_left, expires_at = profile_days_left(meta)
    if expires_at:
        expire_text = expires_at.strftime("%d.%m.%Y")
        return f"Осталось {days_left} дн. · действует до {expire_text}. {PROFILE_NOTE}"
    return PROFILE_NOTE


def profile_title(meta=None):
    days_left, _ = profile_days_left(meta)
    if days_left is not None:
        return f"{PROFILE_TITLE} · {days_left} дн."
    return PROFILE_TITLE


def is_incy_request(handler):
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    requested_client = query.get("client", [""])[-1].strip().lower()
    user_agent = handler.headers.get("User-Agent", "").strip().lower()
    client_header = handler.headers.get("x-client", "").strip().lower()
    return requested_client == "incy" or client_header == "incy" or user_agent.startswith("incy/")


def incy_compatible_link(link):
    """Normalize Hysteria2 links to INCY's canonical, fully specified form."""

    parsed = urllib.parse.urlsplit(link)
    if parsed.scheme.lower() not in {"hy2", "hysteria2"}:
        return link

    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    existing = {key.lower() for key, _ in params}
    if "insecure" not in existing:
        # The LTE nodes use self-signed certificates. INCY still validates the
        # pinned leaf certificate; this only disables the public-CA lookup.
        params.append(("insecure", "1"))
    if "up" not in existing:
        params.append(("up", "60"))
    if "down" not in existing:
        params.append(("down", "200"))

    return urllib.parse.urlunsplit(
        (
            "hysteria2",
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(params),
            parsed.fragment,
        )
    )


def incy_compatible_links(links):
    return [incy_compatible_link(link) for link in links]


def _first_config_value(value, default=""):
    if isinstance(value, list):
        return value[0] if value else default
    return value if value not in (None, "") else default


def _xray_vless_share_links(config, profile_name="Integrated"):
    """Flatten VLESS outbounds from an Xray JSON config into share links."""

    if not isinstance(config, dict):
        return []

    configured_outbounds = config.get("outbounds")
    outbounds = configured_outbounds if isinstance(configured_outbounds, list) else [config]
    vless_outbounds = [
        outbound
        for outbound in outbounds
        if isinstance(outbound, dict) and outbound.get("protocol") == "vless"
    ]
    if vless_outbounds:
        # A stored IntegrationNode is one full Happ profile. Additional VLESS
        # outbounds such as youtube/obhod/proxy-2 are routing legs inside that
        # profile, not independent servers. A share-link cannot carry those
        # routing rules, so expose the primary outbound only.
        primary = next(
            (
                outbound
                for outbound in vless_outbounds
                if str(outbound.get("tag") or "").strip().lower()
                in {"proxy", "primary", "main", "default"}
            ),
            vless_outbounds[0],
        )
        vless_outbounds = [primary]
    links = []

    for outbound in vless_outbounds:
        settings = outbound.get("settings") or {}
        servers = settings.get("vnext") or []
        stream = outbound.get("streamSettings") or {}
        network = str(stream.get("network") or "tcp")
        security = str(stream.get("security") or "none")

        for server in servers:
            if not isinstance(server, dict):
                continue
            address = str(server.get("address") or "").strip()
            port = server.get("port")
            users = server.get("users") or []
            if not address or not port:
                continue

            for user in users:
                if not isinstance(user, dict):
                    continue
                uuid_value = str(user.get("id") or "").strip()
                if not uuid_value:
                    continue

                params = {
                    "type": network,
                    "security": security,
                    "encryption": str(user.get("encryption") or "none"),
                }
                if flow := user.get("flow"):
                    params["flow"] = str(flow)

                if security == "reality":
                    reality = stream.get("realitySettings") or {}
                    params.update(
                        {
                            "pbk": str(reality.get("publicKey") or ""),
                            "sni": str(_first_config_value(reality.get("serverName"))),
                            "fp": str(reality.get("fingerprint") or "chrome"),
                            "sid": str(_first_config_value(reality.get("shortId"))),
                            "spx": str(reality.get("spiderX") or ""),
                        }
                    )
                elif security == "tls":
                    tls = stream.get("tlsSettings") or {}
                    alpn = tls.get("alpn")
                    params.update(
                        {
                            "sni": str(_first_config_value(tls.get("serverName"))),
                            "fp": str(tls.get("fingerprint") or "chrome"),
                            "alpn": ",".join(str(item) for item in alpn)
                            if isinstance(alpn, list)
                            else str(alpn or ""),
                        }
                    )
                    tls_share_fields = {
                        "pcs": tls.get("pinnedPeerCertSha256"),
                        "vcn": tls.get("verifyPeerCertByName"),
                        "ech": tls.get("echConfigList"),
                    }
                    params.update(
                        {
                            key: ",".join(str(item) for item in value)
                            if isinstance(value, list)
                            else str(value or "")
                            for key, value in tls_share_fields.items()
                        }
                    )

                if network == "ws":
                    transport = stream.get("wsSettings") or {}
                    params["path"] = str(transport.get("path") or "/")
                    headers = transport.get("headers") or {}
                    if host := headers.get("Host") or headers.get("host"):
                        params["host"] = str(host)
                elif network == "grpc":
                    transport = stream.get("grpcSettings") or {}
                    params["serviceName"] = str(transport.get("serviceName") or "")
                    if transport.get("multiMode") is True:
                        params["mode"] = "multi"
                elif network in {"xhttp", "splithttp"}:
                    transport = stream.get("xhttpSettings") or stream.get("splithttpSettings") or {}
                    # INCY normalizes legacy SplitHTTP to XHTTP. Preserve the
                    # complete `extra` payload: it can define XMUX, padding and
                    # split-download transports required by the provider.
                    params["type"] = "xhttp"
                    params["path"] = str(transport.get("path") or "/")
                    params["host"] = str(transport.get("host") or "")
                    params["mode"] = str(transport.get("mode") or "")
                    if transport.get("extra") is not None:
                        params["extra"] = json.dumps(
                            transport["extra"], ensure_ascii=False, separators=(",", ":")
                        )
                    if transport.get("xhttpSessionIDTable") is not None:
                        params["sit"] = str(transport["xhttpSessionIDTable"])
                    if transport.get("xhttpSessionIDLength") is not None:
                        params["sil"] = str(transport["xhttpSessionIDLength"])
                elif network in {"http", "h2"}:
                    transport = stream.get("httpSettings") or {}
                    params["path"] = str(transport.get("path") or "/")
                    params["host"] = str(_first_config_value(transport.get("host")))
                elif network == "tcp":
                    tcp = stream.get("tcpSettings") or {}
                    header = tcp.get("header") or {}
                    if header_type := header.get("type"):
                        params["headerType"] = str(header_type)

                if stream.get("finalmask") is not None:
                    finalmask = stream["finalmask"]
                    params["fm"] = (
                        json.dumps(finalmask, ensure_ascii=False, separators=(",", ":"))
                        if isinstance(finalmask, (dict, list))
                        else str(finalmask)
                    )

                config_name = str(config.get("remarks") or "").strip()
                title_parts = [str(profile_name or config_name or "Integrated").strip()]
                if config_name and config_name not in title_parts:
                    title_parts.append(config_name)
                title = " · ".join(part for part in title_parts if part)[:200]
                label = happ_label(title, "Integrated | VLESS | JSON")
                query = urllib.parse.urlencode(
                    {key: value for key, value in params.items() if value not in (None, "")}
                )
                host = f"[{address}]" if ":" in address and not address.startswith("[") else address
                links.append(f"vless://{uuid_value}@{host}:{port}?{query}#{label}")

    return links


def _share_link_connection_key(link):
    try:
        parsed = urllib.parse.urlsplit(link)
        if not parsed.scheme or not parsed.netloc:
            return link.split("#", 1)[0]
        query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query)))
        return urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, parsed.path, query, "")
        )
    except ValueError:
        return link.split("#", 1)[0]


def incy_integrated_links(items):
    """Return native share links for integrated nodes without mutating their source data."""

    converted = []
    for item in items or []:
        if isinstance(item, dict):
            raw_link = str(item.get("raw_link") or "").strip()
            profile_name = str(
                item.get("display_name") or item.get("original_name") or "Integrated"
            ).strip()
        else:
            raw_link = str(item or "").strip()
            profile_name = "Integrated"
        if not raw_link:
            continue

        if raw_link.startswith(("{", "[")):
            try:
                document = json.loads(raw_link)
            except json.JSONDecodeError:
                continue
            configs = document if isinstance(document, list) else [document]
            for config in configs:
                converted.extend(_xray_vless_share_links(config, profile_name))
        elif "://" in raw_link:
            converted.append(incy_compatible_link(raw_link))

    result = []
    seen = set()
    for link in converted:
        key = _share_link_connection_key(link)
        if key in seen:
            continue
        seen.add(key)
        result.append(link)
    return result


def incy_whitelist_routing_link():
    """INCY routing equivalent of Happ's selectable «Белые списки» config."""

    profile = {
        "Name": "HamaliVPN — Белые списки",
        "GlobalProxy": "true",
        "LastUpdated": "1784419200",
        "RemoteDNSType": "DoH",
        "RemoteDNSDomain": "https://cloudflare-dns.com/dns-query",
        "RemoteDNSIP": "1.1.1.1",
        "DomesticDNSType": "DoH",
        "DomesticDNSDomain": "https://dns.google/dns-query",
        "DomesticDNSIP": "8.8.8.8",
        "DnsHosts": {
            "cloudflare-dns.com": "1.1.1.1",
            "dns.google": "8.8.8.8",
        },
        "DirectSites": [
            "geosite:private",
            "geosite:category-ru",
            "domain:ru",
            "domain:su",
            "domain:xn--p1ai",
            "domain:ya.ru",
            "domain:yandex.ru",
            "domain:vk.com",
            "domain:vk.ru",
            "domain:ok.ru",
            "domain:mail.ru",
            "domain:avito.ru",
            "domain:ozon.ru",
            "domain:wildberries.ru",
            "domain:gosuslugi.ru",
            "domain:nalog.gov.ru",
            "domain:hamali.ru",
            "domain:app.hamali.ru",
            "domain:portal.hamali.ru",
        ],
        "DirectIp": [
            "geoip:private",
            "geoip:ru",
            "geoip:by",
            "geoip:kz",
        ],
        "ProxySites": [],
        "ProxyIp": [],
        "BlockSites": ["geosite:category-ads-all"],
        "BlockIp": [],
        "DomainStrategy": "IPIfNonMatch",
        "FakeDNS": "false",
        "useChunkFiles": True,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return "incy://routing/onadd/" + encoded.rstrip("=")


def reality_share_link(uuid, address, port, sni, public_key, short_id, label):
    query = urllib.parse.urlencode(
        {
            "type": "tcp",
            "security": "reality",
            "pbk": public_key,
            "fp": "firefox",
            "sni": sni,
            "sid": short_id,
            "spx": "/",
            "flow": "xtls-rprx-vision",
            "encryption": "none",
        }
    )
    return f"vless://{uuid}@{address}:{port}?{query}#{label}"


def send_profile_headers(handler, meta=None):
    _, expires_at = profile_days_left(meta)
    description = profile_description(meta)
    handler.send_header("profile-title", "base64:" + b64_text(profile_title(meta)))
    handler.send_header("profile-update-interval", "1")
    handler.send_header("support-url", BOT_URL)

    handler.send_header("profile-description", "base64:" + b64_text(description))
    handler.send_header("announce", "base64:" + b64_text(description))
    if is_incy_request(handler):
        for name, value in INCY_PROFILE_HEADERS.items():
            handler.send_header(name, value)
    if expires_at:
        handler.send_header(
            "subscription-userinfo",
            f"upload=0; download=0; total={PROFILE_TOTAL_BYTES}; expire={int(expires_at.timestamp())}",
        )


def get_subscription_meta(token, user_agent=None, hwid=None):
    token = (token or "").strip()
    if not token:
        return None
    url = "http://127.0.0.1:8001/api/internal/subscription_meta?token=" + urllib.parse.quote(token)
    if user_agent:
        url += "&ua=" + base64.b64encode(user_agent.encode("utf-8")).decode("ascii")
    if hwid:
        url += "&hwid=" + urllib.parse.quote(hwid)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        import sys

        print("Error fetching subscription meta:", e, file=sys.stderr)
        return None


def send_disabled_subscription(handler, meta=None):
    content = base64.b64encode(b"# HamaliVPN\n# Subscription inactive or device limit reached\n")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    send_profile_headers(handler, meta)
    handler.send_header("Content-Length", str(len(content)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(content)


def extract_uuid(links):
    for link in links:
        if link.startswith("vless://"):
            return link.split("//")[1].split("@")[0]
    return "de1e3979-4e6e-48a3-816d-273c0a55f56f"


def happ_label(title, description):
    desc = base64.b64encode(description.encode("utf-8")).decode("ascii")
    return urllib.parse.quote(title) + "?serverDescription=" + desc


def extract_vless_params(links):
    for link in links:
        if link.startswith("vless://") and "security=reality" in link:
            parts = link.split("?", 1)
            if len(parts) > 1:
                query = parts[1].split("#")[0]
                params = dict(x.split("=") for x in query.split("&") if "=" in x)
                if "pbk" in params and "sni" in params:
                    return params.get("sni", "dzen.ru"), params.get("pbk"), params.get("sid")
    return "dzen.ru", "cTkGXYP9rpc38LCDAODT6YeTzWVexdsGvj06nS8BODo", "75199e27729a2355"


def vless_to_outbound(vless_url):
    try:
        parsed = urllib.parse.urlparse(vless_url)
        if parsed.scheme != "vless":
            return None

        user_info = parsed.netloc.split("@")
        if len(user_info) != 2:
            return None

        uuid_val = user_info[0]
        host_port = user_info[1].split(":")
        address = host_port[0]
        port = int(host_port[1])

        query = urllib.parse.parse_qs(parsed.query)
        tag = urllib.parse.unquote(parsed.fragment) if parsed.fragment else "Integrated Node"

        network = query.get("type", ["tcp"])[0]
        security = query.get("security", ["none"])[0]
        flow = query.get("flow", [""])[0]

        outbound = {
            "tag": tag,
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": address,
                        "port": port,
                        "users": [{"id": uuid_val, "encryption": "none", "flow": flow}],
                    }
                ]
            },
            "streamSettings": {"network": network, "security": security},
        }

        if security == "reality":
            outbound["streamSettings"]["realitySettings"] = {
                "publicKey": query.get("pbk", [""])[0],
                "serverName": query.get("sni", [""])[0],
                "fingerprint": query.get("fp", ["chrome"])[0],
                "shortId": query.get("sid", [""])[0],
                "spiderX": query.get("spx", [""])[0],
            }
        elif security == "tls":
            outbound["streamSettings"]["tlsSettings"] = {
                "serverName": query.get("sni", [""])[0],
                "fingerprint": query.get("fp", ["chrome"])[0],
            }

        if network == "grpc":
            outbound["streamSettings"]["grpcSettings"] = {
                "serviceName": query.get("serviceName", [""])[0],
                "multiMode": False,
            }
        elif network == "ws":
            outbound["streamSettings"]["wsSettings"] = {
                "path": query.get("path", ["/"])[0],
                "headers": {"Host": query.get("host", [query.get("sni", [""])[0]])[0]},
            }

        return {
            "remarks": tag,
            "outbounds": [
                outbound,
                {"protocol": "freedom", "tag": "direct"},
                {"protocol": "blackhole", "tag": "block"},
            ],
        }
    except Exception as e:
        print("Error parsing vless URI:", e)
        return None


_SUBSCRIPTION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
_RESERVED_PATHS = {"", "health", "healthz", "favicon.ico", "robots.txt"}


def extract_subscription_token(path):
    parsed = urllib.parse.urlsplit(path)
    clean = parsed.path.strip("/")
    if clean.startswith("api/sub/") or clean.startswith("sub/"):
        token = clean.rsplit("/", 1)[-1]
    elif "/" not in clean and clean not in _RESERVED_PATHS:
        token = clean
    else:
        return ""
    token = urllib.parse.unquote(token)
    return token if _SUBSCRIPTION_TOKEN_RE.fullmatch(token) else ""


def remnawave_subscription_path(token, original_path):
    query = urllib.parse.urlsplit(original_path).query
    result = "/api/sub/" + urllib.parse.quote(token, safe="")
    return result + (f"?{query}" if query else "")


class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        import sys

        print("INCOMING REQUEST HEADERS:", self.headers, file=sys.stderr)
        if urllib.parse.urlsplit(self.path).path in {"/health", "/healthz"}:
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        target_path = self.path
        requested_token = extract_subscription_token(self.path)
        auth_token = ""
        meta = None

        if requested_token:
            auth_token = requested_token
            meta = get_subscription_meta(
                requested_token, self.headers.get("User-Agent"), self.headers.get("X-Hwid")
            )

            if meta is None:
                self.send_response(502)
                self.end_headers()
                self.wfile.write(b"Bad Gateway: Unable to fetch subscription meta")
                return

            if meta.get("active") is False:
                send_disabled_subscription(self, meta)
                return
            target_token = requested_token
            if meta:
                target_token = meta.get("target_token") or requested_token
                auth_token = meta.get("auth_token") or requested_token
            target_path = remnawave_subscription_path(target_token, self.path)

            # Device limits belong to the actual subscription/VPN user
            # (Remnawave HWID limit + tariff device_limit), not to profile
            # download IPs. Mobile networks/NAT/VPN tests can change the IP
            # during import and must not receive a fake "limit exceeded" node.
            #
            # Hysteria2 is additionally checked by /hysteria/auth for active
            # subscription status. We intentionally do not block subscription
            # rendering here.

        url = "https://panel.104.171.137.220.sslip.io" + target_path
        req = urllib.request.Request(url)
        for key, value in self.headers.items():
            if key.lower() not in [
                "host",
                "accept-encoding",
                "x-forwarded-for",
                "x-forwarded-proto",
                "x-proxy-bypass",
                "if-none-match",
                "if-modified-since",
            ]:
                req.add_header(key, value)
        req.add_header("X-Proxy-Bypass", "true")

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read()
                headers = response.info()
                status = response.status

                if status == 200 and "/api/sub" in target_path:
                    try:
                        decoded = base64.b64decode(content).decode("utf-8")
                        # Keep Remnawave/native Reality fingerprint for Russia stability.
                        # decoded = decoded.replace('fp=firefox', 'fp=chrome')
                        decoded_check = urllib.parse.unquote(decoded).lower()
                        disabled = any(
                            m in decoded_check
                            for m in (
                                "subscription disabled",
                                "contact support",
                                "account disabled",
                                "expired",
                                "limit of devices reached",
                                "app not supported",
                            )
                        )
                        # Отключённая/истёкшая подписка — НЕ добавляем Hysteria,
                        # отдаём оригинал (иначе общий пароль Hysteria оставлял бы доступ).
                        if "://" in decoded and not disabled:
                            existing = [
                                line for line in decoded.strip().split("\n") if line.strip()
                            ]
                            uuid = extract_uuid(existing)
                            if uuid == "00000000-0000-0000-0000-000000000000":
                                disabled = True
                            sni, pbk, sid = extract_vless_params(existing)

                            os.environ["HYSTERIA_LEGACY_PASSWORD"]
                            obfs_pass = os.environ["HYSTERIA_OBFS_PASSWORD"]
                            pin_fr = (
                                "83beff34d998f9f5734f9ea7b8534b90a4d93e3621cbbc48a4f4d8d8424a1dc0"
                            )
                            pin_nl = (
                                "1384cdd849ec74e8fe0cb4c5793fdff9758b412046fc208f686f165a64ab216b"
                            )

                            # Use UUID for migrated nodes, hpass for old nodes
                            user_uuid = (
                                auth_token or urllib.parse.urlparse(self.path).path.split("/")[-1]
                            )
                            auth_nl = user_uuid
                            auth_fr = user_uuid

                            # LTE: only the proven Hysteria directions.
                            # DE/London/UK stay on VLESS to avoid n/a spikes in clients.
                            lte_links = [
                                f"hy2://{auth_nl}@103.112.69.188:8443?sni={sni}&obfs=salamander&obfs-password={obfs_pass}&pinSHA256={pin_nl}#"
                                + happ_label(
                                    "🇳🇱 Нидерланды LTE", "Hysteria | hysteria | TLS | JSON"
                                ),
                                f"hy2://{auth_fr}@45.92.218.178:8443?sni={sni}&obfs=salamander&obfs-password={obfs_pass}&pinSHA256={pin_fr}#"
                                + happ_label("🇫🇷 Франция LTE", "Hysteria | hysteria | TLS | JSON"),
                            ]

                            premium_links = []
                            premium_nl = []
                            premium_fr = []
                            premium_uk = []
                            premium_fi = []
                            reserve_links = []
                            other_links = []

                            for link in existing:
                                dec_link = urllib.parse.unquote(link)
                                if "LTE" in dec_link or "LTE" in link:
                                    continue

                                clean_url = link.split("#")[0] if "#" in link else link

                                if "103.112.69.188" in link:
                                    premium_nl.append(
                                        clean_url
                                        + "#"
                                        + happ_label(
                                            "🇳🇱 Нидерланды", "VLESS | TCP | Reality | JSON"
                                        )
                                    )
                                elif "45.92.218.178" in link:
                                    premium_fr.append(
                                        clean_url
                                        + "#"
                                        + happ_label("🇫🇷 Франция", "VLESS | TCP | Reality | JSON")
                                    )
                                elif "107.161.160.220" in link:
                                    premium_fr.append(
                                        clean_url
                                        + "#"
                                        + happ_label(
                                            "🇫🇷 Франция (Новая)", "VLESS | TCP | Reality | JSON"
                                        )
                                    )
                                elif "92.119.166.192" in link and "type=tcp" in link:
                                    reserve_links.append(
                                        clean_url
                                        + "#"
                                        + happ_label("🇩🇪 Германия", "VLESS | TCP | Reality | JSON")
                                    )
                                elif "67.159.56.63" in link:
                                    # This route is currently unreachable from affected Russian networks.
                                    # Keep it out of every public profile until provider routing is fixed.
                                    continue
                                elif "85.137.249.225" in link:
                                    premium_uk.append(
                                        clean_url
                                        + "#"
                                        + happ_label(
                                            "🇬🇧 Юнайтед Кингдом", "VLESS | TCP | Reality | JSON"
                                        )
                                    )
                                elif "62.60.249.228" in link:
                                    premium_fi.append(
                                        clean_url
                                        + "#"
                                        + happ_label("🇫🇮 Финляндия", "VLESS | TCP | Reality | JSON")
                                    )
                                else:
                                    if "Автовыбор" not in dec_link:
                                        other_links.append(link)

                            cluster_json = """{
  "remarks": "🇪🇺 Автовыбор",
  "burstObservatory": {
    "pingConfig": {
      "connectivity": "http://connectivitycheck.gstatic.com/generate_204",
      "destination": "http://connectivitycheck.gstatic.com/generate_204",
      "interval": "1m",
      "sampling": 2,
      "timeout": "5s"
    },
    "subjectSelector": ["proxy-nl", "proxy-fr", "proxy-fr-new", "proxy-uk", "proxy-fi", "proxy-de"]
  },
  "dns": {
    "queryStrategy": "UseIPv4",
    "servers": ["1.1.1.1", "8.8.8.8", "8.8.4.4", { "address": "77.88.8.8", "domains": ["geosite:category-ru", "domain:ru", "domain:su", "domain:xn--p1ai"] }]
  },
  "inbounds": [
    {
      "listen": "127.0.0.1",
      "port": 10808,
      "protocol": "socks",
      "settings": { "auth": "noauth", "udp": true },
      "sniffing": { "destOverride": ["http", "tls", "quic"], "enabled": true, "routeOnly": true },
      "tag": "socks"
    },
    {
      "listen": "127.0.0.1",
      "port": 10809,
      "protocol": "http",
      "settings": { "allowTransparent": false },
      "sniffing": { "destOverride": ["http", "tls", "quic"], "enabled": true, "routeOnly": true },
      "tag": "http"
    }
  ],
  "outbounds": [
    {
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "103.112.69.188", "port": 443, "users": [{ "encryption": "none", "flow": "xtls-rprx-vision", "id": "{uuid}" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "fingerprint": "firefox", "publicKey": "{pbk}", "serverName": "{sni}", "shortId": "{sid}", "spiderX": "/" }, "tcpSettings": {} },
      "tag": "proxy-nl"
    },
    {
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "45.92.218.178", "port": 443, "users": [{ "encryption": "none", "flow": "xtls-rprx-vision", "id": "{uuid}" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "fingerprint": "firefox", "publicKey": "{pbk}", "serverName": "{sni}", "shortId": "{sid}", "spiderX": "/" }, "tcpSettings": {} },
      "tag": "proxy-fr"
    },
    {
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "107.161.160.220", "port": 443, "users": [{ "encryption": "none", "flow": "xtls-rprx-vision", "id": "{uuid}" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "fingerprint": "firefox", "publicKey": "{pbk}", "serverName": "{sni}", "shortId": "{sid}", "spiderX": "/" }, "tcpSettings": {} },
      "tag": "proxy-fr-new"
    },
    {
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "85.137.249.225", "port": 2053, "users": [{ "encryption": "none", "flow": "xtls-rprx-vision", "id": "{uuid}" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "fingerprint": "firefox", "publicKey": "{pbk}", "serverName": "{sni}", "shortId": "{sid}", "spiderX": "/" }, "tcpSettings": {} },
      "tag": "proxy-uk"
    },
    {
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "92.119.166.192", "port": 443, "users": [{ "encryption": "none", "flow": "xtls-rprx-vision", "id": "{uuid}" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "fingerprint": "firefox", "publicKey": "{pbk}", "serverName": "{sni}", "shortId": "{sid}", "spiderX": "/" }, "tcpSettings": {} },
      "tag": "proxy-de"
    },
    {
      "protocol": "vless",
      "settings": { "vnext": [{ "address": "62.60.249.228", "port": 443, "users": [{ "encryption": "none", "flow": "xtls-rprx-vision", "id": "{uuid}" }] }] },
      "streamSettings": { "network": "tcp", "security": "reality", "realitySettings": { "fingerprint": "firefox", "publicKey": "{pbk}", "serverName": "{sni}", "shortId": "{sid}", "spiderX": "/" }, "tcpSettings": {} },
      "tag": "proxy-fi"
    },
    { "protocol": "freedom", "tag": "direct" },
    { "protocol": "blackhole", "tag": "block" }
  ],
  "routing": {
    "balancers": [
      {
        "fallbackTag": "proxy-nl",
        "selector": ["proxy-nl", "proxy-fr", "proxy-fr-new", "proxy-uk", "proxy-fi", "proxy-de"],
        "strategy": {
          "type": "leastLoad",
          "settings": {
            "expected": 1,
            "maxRTT": "4s",
            "baselines": ["3s"],
            "tolerance": 0.12,
            "costs": [
              { "match": "^proxy-nl$", "regexp": true, "value": 0 },
              { "match": "^proxy-fr$", "regexp": true, "value": 5 },
              { "match": "^proxy-fr-new$", "regexp": true, "value": 5 },
              { "match": "^proxy-uk$", "regexp": true, "value": 25 },
              { "match": "^proxy-fi$", "regexp": true, "value": 40 },
              { "match": "^proxy-de$", "regexp": true, "value": 80 }
            ]
          }
        },
        "tag": "LB"
      }
    ],
    "domainMatcher": "hybrid",
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      { "outboundTag": "direct", "protocol": ["bittorrent"], "type": "field" },
      {
        "domain": [
          "domain:app.hamali.ru",
          "domain:portal.hamali.ru",
          "domain:hamali.ru",
          "domain:www.hamali.ru"
        ],
        "balancerTag": "LB",
        "type": "field"
      },
      {
        "domain": [
          "geosite:category-ru",
          "geosite:private",
          "domain:ru",
          "domain:su",
          "domain:xn--p1ai",
          "domain:yandex",
          "domain:ya.ru",
          "domain:vk.com",
          "domain:vk.ru",
          "domain:ok.ru",
          "domain:mail.ru",
          "domain:avito.ru",
          "domain:ozon.ru",
          "domain:wildberries.ru",
          "domain:wb.ru",
          "domain:sberbank.ru",
          "domain:sber.ru",
          "domain:tbank.ru",
          "domain:tinkoff.ru",
          "domain:alfabank.ru",
          "domain:vtb.ru",
          "domain:gosuslugi.ru",
          "domain:nalog.gov.ru",
          "domain:mos.ru",
          "domain:beeline.ru",
          "domain:megafon.ru",
          "domain:mts.ru",
          "domain:tele2.ru",
          "domain:alfabank.com", "domain:vtb.com", "domain:tbank-online.com", "domain:tochka.com",
          "domain:gazprombank.tech", "domain:moex.com", "domain:2gis.com", "domain:2gis.ru",
          "domain:ozonusercontent.com", "domain:megamarket.tech", "domain:okko.tv", "domain:youla.io",
          "domain:userapi.com", "domain:vk-portal.net", "domain:yandexcloud.net", "domain:gismeteo.com",
          "domain:wbstatic.net", "domain:lenta.com", "domain:dns-shop.ru", "domain:citilink.ru"
        ],
        "outboundTag": "direct",
        "type": "field"
      },
      { "ip": ["geoip:private", "geoip:ru", "geoip:by", "geoip:kz"], "outboundTag": "direct", "type": "field" },
      { "balancerTag": "LB", "network": "tcp,udp", "type": "field" }
    ]
  },
  "meta": { "serverDescription": "Автовыбор HamaliVPN: оптимальный сервер, продление и поддержка — через ⓘ" }
}"""
                            cluster_json = (
                                cluster_json.replace("{uuid}", uuid)
                                .replace("{pbk}", pbk)
                                .replace("{sni}", sni)
                                .replace("{sid}", sid)
                            )

                            def _build_whitelist_config():
                                """Emergency Happ profile: direct RU allowlist + simple VLESS failover."""
                                cfg = json.loads(cluster_json)
                                keep_tags = {
                                    "proxy-nl",
                                    "proxy-fr",
                                    "proxy-fr-new",
                                    "direct",
                                    "block",
                                }
                                cfg["remarks"] = "🇪🇺 Белые списки"
                                cfg["outbounds"] = [
                                    outbound
                                    for outbound in cfg.get("outbounds", [])
                                    if outbound.get("tag") in keep_tags
                                ]
                                cfg.pop("burstObservatory", None)
                                cfg["observatory"] = {
                                    "enableConcurrency": True,
                                    "probeInterval": "1m",
                                    "probeUrl": "http://connectivitycheck.gstatic.com/generate_204",
                                    "subjectSelector": ["proxy-nl", "proxy-fr", "proxy-fr-new"],
                                }
                                cfg["dns"] = {
                                    "queryStrategy": "UseIPv4",
                                    "servers": ["1.1.1.1", "8.8.8.8", "8.8.4.4"],
                                }
                                cfg["meta"] = {
                                    "routingTags": ["auto-route", "universal"],
                                    "serverDescription": "Белые списки: RU-сервисы напрямую, остальное через AntiBlock",
                                }
                                cfg["routing"] = {
                                    "balancers": [
                                        {
                                            "tag": "white-list-lb",
                                            "selector": ["proxy-nl", "proxy-fr", "proxy-fr-new"],
                                            "fallbackTag": "proxy-nl",
                                            "strategy": {
                                                "type": "leastLoad",
                                                "settings": {
                                                    "expected": 1,
                                                    "maxRTT": "2s",
                                                    "baselines": ["3s"],
                                                    "tolerance": 0.15,
                                                    "costs": [
                                                        {
                                                            "match": "proxy-nl",
                                                            "regexp": False,
                                                            "value": 0,
                                                        },
                                                        {
                                                            "match": "proxy-fr",
                                                            "regexp": False,
                                                            "value": 10,
                                                        },
                                                        {
                                                            "match": "proxy-fr-new",
                                                            "regexp": False,
                                                            "value": 10,
                                                        },
                                                    ],
                                                },
                                            },
                                        }
                                    ],
                                    "domainMatcher": "hybrid",
                                    "domainStrategy": "AsIs",
                                    "rules": [
                                        {
                                            "domain": [
                                                "geosite:private",
                                                "geosite:category-ru",
                                                "domain:ru",
                                                "domain:su",
                                                "domain:xn--p1ai",
                                                "domain:moscow",
                                                "domain:ya.ru",
                                                "domain:yandex.ru",
                                                "domain:yandex.com",
                                                "domain:yandex.net",
                                                "domain:vk.com",
                                                "domain:vk.ru",
                                                "domain:vk.cc",
                                                "domain:mvk.com",
                                                "domain:userapi.com",
                                                "domain:ok.ru",
                                                "domain:ok.me",
                                                "domain:mail.ru",
                                                "domain:mycdn.me",
                                                "domain:my.com",
                                                "domain:my.games",
                                                "domain:avito.ru",
                                                "domain:ozon.ru",
                                                "domain:ozon.com",
                                                "domain:ozonusercontent.com",
                                                "domain:wildberries.ru",
                                                "domain:wb.ru",
                                                "domain:wbstatic.net",
                                                "domain:sber.ru",
                                                "domain:sberbank.ru",
                                                "domain:tbank.ru",
                                                "domain:tinkoff.ru",
                                                "domain:alfabank.ru",
                                                "domain:vtb.ru",
                                                "domain:gosuslugi.ru",
                                                "domain:nalog.gov.ru",
                                                "domain:mos.ru",
                                                "domain:2gis.ru",
                                                "domain:2gis.com",
                                                "domain:mts.ru",
                                                "domain:megafon.ru",
                                                "domain:beeline.ru",
                                                "domain:tele2.ru",
                                                "domain:okko.tv",
                                                "domain:premier.one",
                                                "domain:boosty.to",
                                                "domain:hh.ru",
                                                "domain:lenta.com",
                                                "domain:dns-shop.ru",
                                                "domain:citilink.ru",
                                                "domain:hamali.ru",
                                                "domain:app.hamali.ru",
                                                "domain:portal.hamali.ru",
                                            ],
                                            "outboundTag": "direct",
                                            "type": "field",
                                        },
                                        {
                                            "ip": [
                                                "geoip:private",
                                                "geoip:ru",
                                                "geoip:by",
                                                "geoip:kz",
                                            ],
                                            "outboundTag": "direct",
                                            "type": "field",
                                        },
                                        {
                                            "outboundTag": "block",
                                            "protocol": ["bittorrent"],
                                            "type": "field",
                                        },
                                        {
                                            "balancerTag": "white-list-lb",
                                            "inboundTag": ["socks", "http"],
                                            "network": "tcp,udp",
                                            "type": "field",
                                        },
                                    ],
                                }
                                return cfg

                            def _build_adblock_config():
                                """Optional NL/FR profile with DNS and routing-level ad blocking."""
                                cfg = json.loads(cluster_json)
                                keep_tags = {"proxy-nl", "proxy-fr", "direct", "block"}
                                cfg["remarks"] = "🇪🇺 YouTube без рекламы"
                                cfg["outbounds"] = [
                                    outbound
                                    for outbound in cfg.get("outbounds", [])
                                    if outbound.get("tag") in keep_tags
                                ]
                                cfg["burstObservatory"] = {
                                    "pingConfig": {
                                        "connectivity": "http://connectivitycheck.gstatic.com/generate_204",
                                        "destination": "http://connectivitycheck.gstatic.com/generate_204",
                                        "interval": "1m",
                                        "sampling": 2,
                                        "timeout": "5s",
                                    },
                                    "subjectSelector": ["proxy-nl", "proxy-fr"],
                                }
                                cfg["dns"] = {
                                    "queryStrategy": "UseIPv4",
                                    "servers": [
                                        "94.140.14.14",
                                        "94.140.15.15",
                                        "1.1.1.1",
                                        {
                                            "address": "77.88.8.8",
                                            "domains": [
                                                "geosite:category-ru",
                                                "domain:ru",
                                                "domain:su",
                                                "domain:xn--p1ai",
                                            ],
                                        },
                                    ],
                                }
                                cfg["meta"] = {
                                    "routingTags": ["adblock", "auto-route"],
                                    "serverDescription": "Фильтрация рекламы и трекеров через AdGuard DNS; Нидерланды и Франция с автовыбором.",
                                }
                                cfg["routing"] = {
                                    "balancers": [
                                        {
                                            "tag": "adblock-lb",
                                            "selector": ["proxy-nl", "proxy-fr"],
                                            "fallbackTag": "proxy-nl",
                                            "strategy": {
                                                "type": "leastLoad",
                                                "settings": {
                                                    "expected": 1,
                                                    "maxRTT": "3s",
                                                    "baselines": ["3s"],
                                                    "tolerance": 0.15,
                                                    "costs": [
                                                        {
                                                            "match": "proxy-nl",
                                                            "regexp": False,
                                                            "value": 0,
                                                        },
                                                        {
                                                            "match": "proxy-fr",
                                                            "regexp": False,
                                                            "value": 10,
                                                        },
                                                    ],
                                                },
                                            },
                                        }
                                    ],
                                    "domainMatcher": "hybrid",
                                    "domainStrategy": "IPIfNonMatch",
                                    "rules": [
                                        {
                                            "domain": [
                                                "geosite:category-ads-all",
                                                "domain:doubleclick.net",
                                                "domain:googleadservices.com",
                                                "domain:googlesyndication.com",
                                                "domain:adservice.google.com",
                                                "domain:pagead2.googlesyndication.com",
                                                "domain:google-analytics.com",
                                                "domain:googletagmanager.com",
                                                "domain:adnxs.com",
                                                "domain:scorecardresearch.com",
                                            ],
                                            "outboundTag": "block",
                                            "type": "field",
                                        },
                                        {
                                            "domain": [
                                                "geosite:private",
                                                "geosite:category-ru",
                                                "domain:ru",
                                                "domain:su",
                                                "domain:xn--p1ai",
                                                "domain:hamali.ru",
                                                "domain:app.hamali.ru",
                                                "domain:portal.hamali.ru",
                                            ],
                                            "outboundTag": "direct",
                                            "type": "field",
                                        },
                                        {
                                            "ip": [
                                                "geoip:private",
                                                "geoip:ru",
                                                "geoip:by",
                                                "geoip:kz",
                                            ],
                                            "outboundTag": "direct",
                                            "type": "field",
                                        },
                                        {
                                            "outboundTag": "block",
                                            "protocol": ["bittorrent"],
                                            "type": "field",
                                        },
                                        {
                                            "balancerTag": "adblock-lb",
                                            "inboundTag": ["socks", "http"],
                                            "network": "tcp,udp",
                                            "type": "field",
                                        },
                                    ],
                                }
                                return cfg

                            _ctag = (
                                self.path.split("cluster=", 1)[1].split("&")[0].split("#")[0]
                                if "cluster=" in self.path
                                else None
                            )
                            if (
                                _ctag is None
                                and "happ" in self.headers.get("User-Agent", "").lower()
                            ):
                                _ctag = "all"
                            if _ctag in (
                                "1",
                                "true",
                                "all",
                                "nl",
                                "fr",
                                "fr-new",
                                "de",
                                "uk",
                                "fi",
                                "white",
                                "whitelist",
                                "antiblock",
                                "bs",
                                "adblock",
                                "ads",
                                "clean",
                                "youtube",
                                "yt",
                            ):
                                _ct = "application/json; charset=utf-8"
                                if _ctag == "all":
                                    _all = [json.loads(cluster_json)]
                                    for _t in STANDALONE_CLUSTER_TAGS:
                                        _k = "proxy-" + _t
                                        _c = json.loads(cluster_json)
                                        _c["outbounds"] = [
                                            o
                                            for o in _c["outbounds"]
                                            if o.get("tag") in (_k, "direct", "block")
                                        ]
                                        _c.pop("burstObservatory", None)
                                        _c.get("routing", {}).pop("balancers", None)
                                        for _rr in _c.get("routing", {}).get("rules", []):
                                            if _rr.get("balancerTag"):
                                                _rr.pop("balancerTag")
                                                _rr["outboundTag"] = _k
                                        _c["remarks"] = CLUSTER_REMARKS[_t]
                                        _all.append(_c)
                                    _hymap = {
                                        "103.112.69.188": "proxy-nl",
                                        "45.92.218.178": "proxy-fr",
                                        "62.60.249.228": "proxy-fi",
                                    }
                                    _pinmap = {
                                        "103.112.69.188": "1384cdd849ec74e8fe0cb4c5793fdff9758b412046fc208f686f165a64ab216b",
                                        "45.92.218.178": "83beff34d998f9f5734f9ea7b8534b90a4d93e3621cbbc48a4f4d8d8424a1dc0",
                                        "62.60.249.228": "6b22e49604fc2c1964ef3fabc7b54449b68348ee2f070c6ce76e49bca69e623e",
                                    }
                                    for _hip, _hn in (
                                        ("103.112.69.188", "🇳🇱 Нидерланды"),
                                        ("45.92.218.178", "🇫🇷 Франция"),
                                    ):
                                        _hc = json.loads(cluster_json)
                                        _fb = json.loads(
                                            json.dumps(
                                                next(
                                                    o
                                                    for o in _hc["outbounds"]
                                                    if o.get("tag") == _hymap[_hip]
                                                )
                                            )
                                        )
                                        _fb["tag"] = "fallback-vless"
                                        _hyout = {
                                            "tag": "hy2",
                                            "protocol": "hysteria",
                                            "settings": {
                                                "version": 2,
                                                "address": _hip,
                                                "port": 8443,
                                            },
                                            "streamSettings": {
                                                "network": "hysteria",
                                                "security": "tls",
                                                "tlsSettings": {
                                                    "serverName": sni,
                                                    "alpn": ["h3"],
                                                    "pinnedPeerCertSha256": _pinmap[_hip],
                                                },
                                                "finalmask": {
                                                    "quicParams": {
                                                        "brutalDown": "200 mbps",
                                                        "brutalUp": "60 mbps",
                                                        "congestion": "brutal",
                                                    },
                                                    "udp": [
                                                        {
                                                            "settings": {
                                                                "packetSize": "1200-1400",
                                                                "password": obfs_pass,
                                                            },
                                                            "type": "salamander",
                                                        }
                                                    ],
                                                },
                                                "hysteriaSettings": {
                                                    "version": 2,
                                                    "auth": auth_token or user_uuid,
                                                    "udpIdleTimeout": 60,
                                                },
                                            },
                                        }
                                        _hc["outbounds"] = [
                                            _hyout,
                                            _fb,
                                            {"protocol": "freedom", "tag": "direct"},
                                            {"protocol": "blackhole", "tag": "block"},
                                        ]
                                        _hc["burstObservatory"] = {
                                            "subjectSelector": ["hy2", "fallback-vless"],
                                            "pingConfig": {
                                                "destination": "http://connectivitycheck.gstatic.com/generate_204",
                                                "connectivity": "http://connectivitycheck.gstatic.com/generate_204",
                                                "interval": "1m",
                                                "sampling": 2,
                                                "timeout": "5s",
                                            },
                                        }
                                        _hc["routing"]["balancers"] = [
                                            {
                                                "tag": "LB",
                                                "selector": ["hy2", "fallback-vless"],
                                                "fallbackTag": "fallback-vless",
                                                "strategy": {
                                                    "type": "leastLoad",
                                                    "settings": {
                                                        "expected": 1,
                                                        "maxRTT": "4s",
                                                        "baselines": ["3s"],
                                                        "tolerance": 0.2,
                                                    },
                                                },
                                            }
                                        ]
                                        for _rr in _hc["routing"]["rules"]:
                                            if _rr.get("balancerTag"):
                                                _rr["balancerTag"] = "LB"
                                        _hc["remarks"] = _hn + " LTE"
                                        _all.append(_hc)
                                    _all.append(_build_whitelist_config())

                                    try:
                                        req_int = urllib.request.Request(
                                            "http://127.0.0.1:8001/api/internal/integrated_nodes",
                                            headers={"User-Agent": "HamaliVPN-Injector"},
                                        )
                                        with urllib.request.urlopen(req_int, timeout=5) as int_res:
                                            int_data = json.loads(int_res.read().decode("utf-8"))
                                            for node_str in int_data.get("nodes", []):
                                                if node_str.startswith("vless://"):
                                                    outbound_cfg = vless_to_outbound(node_str)
                                                    if outbound_cfg:
                                                        _all.append(outbound_cfg)
                                                else:
                                                    try:
                                                        _all.append(json.loads(node_str))
                                                    except (
                                                        TypeError,
                                                        ValueError,
                                                        json.JSONDecodeError,
                                                    ):
                                                        pass
                                    except Exception as e:
                                        print("Error fetching integrated nodes for JSON:", e)

                                    raw = json.dumps(_all, ensure_ascii=False).encode("utf-8")
                                elif _ctag in ("white", "whitelist", "antiblock", "bs"):
                                    raw = json.dumps(
                                        _build_whitelist_config(), ensure_ascii=False
                                    ).encode("utf-8")
                                elif _ctag in ("adblock", "ads", "clean", "youtube", "yt"):
                                    raw = json.dumps(
                                        _build_adblock_config(), ensure_ascii=False
                                    ).encode("utf-8")
                                elif _ctag in ("nl", "fr", "fr-new", "de", "uk", "fi"):
                                    _keep = "proxy-" + _ctag
                                    _cfg = json.loads(cluster_json)
                                    _cfg["outbounds"] = [
                                        o
                                        for o in _cfg["outbounds"]
                                        if o.get("tag") in (_keep, "direct", "block")
                                    ]
                                    _cfg.pop("burstObservatory", None)
                                    _cfg.get("routing", {}).pop("balancers", None)
                                    for _r in _cfg.get("routing", {}).get("rules", []):
                                        if _r.get("balancerTag"):
                                            _r.pop("balancerTag")
                                            _r["outboundTag"] = _keep
                                    _cfg["remarks"] = CLUSTER_REMARKS.get(_ctag, _ctag)
                                    raw = json.dumps(_cfg, ensure_ascii=False).encode("utf-8")
                                else:
                                    raw = cluster_json.encode("utf-8")
                                self.send_response(200)
                                self.send_header("Content-Type", _ct)
                                send_profile_headers(self, meta)
                                self.send_header("Content-Length", str(len(raw)))
                                self.send_header("Connection", "close")
                                self.end_headers()
                                self.wfile.write(raw)
                                return
                            else:
                                # Final order: fastest/brand-first VLESS, then proven LTE, then other.
                                premium_links = premium_nl + premium_fr + premium_uk + premium_fi
                                smart_links = []
                                smart_source = premium_nl or premium_fr or premium_uk or premium_fi
                                if smart_source:
                                    smart_clean_url = (
                                        smart_source[0].split("#")[0]
                                        if "#" in smart_source[0]
                                        else smart_source[0]
                                    )
                                    # Happ can hide exact duplicate nodes. Use DNS aliases that resolve to the same IP
                                    # so the visible Smart entry stays separate without changing the actual exit server.
                                    smart_clean_url = smart_clean_url.replace(
                                        "@103.112.69.188:", "@103.112.69.188.sslip.io:"
                                    )
                                    smart_clean_url = smart_clean_url.replace(
                                        "@45.92.218.178:", "@45.92.218.178.sslip.io:"
                                    )
                                    smart_clean_url = smart_clean_url.replace(
                                        "@85.137.249.225:", "@85.137.249.225.sslip.io:"
                                    )
                                    smart_links.append(
                                        smart_clean_url
                                        + "#"
                                        + happ_label("🇪🇺 Автовыбор", "VLESS | TCP | Reality | JSON")
                                    )

                                integrated_links = []
                                integrated_items = []
                                try:
                                    req_int = urllib.request.Request(
                                        "http://127.0.0.1:8001/api/internal/integrated_nodes",
                                        headers={"User-Agent": "HamaliVPN-Injector"},
                                    )
                                    with urllib.request.urlopen(req_int, timeout=5) as int_res:
                                        int_data = json.loads(int_res.read().decode("utf-8"))
                                        integrated_links = int_data.get("nodes", [])
                                        integrated_items = int_data.get("items") or [
                                            {"raw_link": link} for link in integrated_links
                                        ]
                                except Exception as e:
                                    print("Error fetching integrated nodes:", e)

                                if is_incy_request(self):
                                    integrated_links = incy_integrated_links(integrated_items)

                                all_links = (
                                    smart_links
                                    + premium_links
                                    + lte_links
                                    + reserve_links
                                    + other_links
                                    + integrated_links
                                )
                                if is_incy_request(self):
                                    # Happ's Germany entry is a generated full config and may not
                                    # exist in Remnawave's share-link response. INCY needs a normal
                                    # VLESS share link, so generate the equivalent endpoint only
                                    # when the upstream list does not already contain it.
                                    if not any("@92.119.166.192:" in link for link in all_links):
                                        all_links.append(
                                            reality_share_link(
                                                uuid,
                                                "92.119.166.192",
                                                443,
                                                sni,
                                                pbk,
                                                sid,
                                                happ_label(
                                                    "🇩🇪 Германия",
                                                    "VLESS | TCP | Reality | JSON",
                                                ),
                                            )
                                        )
                                    all_links = incy_compatible_links(all_links)
                                    all_links.append(incy_whitelist_routing_link())
                                content = base64.b64encode("\n".join(all_links).encode("utf-8"))

                            if "Content-Length" in headers:
                                del headers["Content-Length"]
                    except Exception as ex:
                        print("Error processing sub:", ex)

                self.send_response(status)
                for key, value in headers.items():
                    if key.lower() not in [
                        "content-length",
                        "transfer-encoding",
                        "content-encoding",
                        "connection",
                        "etag",
                        "cache-control",
                        "profile-title",
                        "profile-update-interval",
                        "support-url",
                        "profile-web-page-url",
                        "profile-description",
                        "subscription-userinfo",
                        "announce",
                    ]:
                        self.send_header(key, value)
            if requested_token:
                send_profile_headers(self, meta)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(content)

        except urllib.error.HTTPError as e:
            content = e.read()
            self.send_response(e.code)
            for key, value in e.headers.items():
                if key.lower() not in [
                    "content-length",
                    "transfer-encoding",
                    "content-encoding",
                    "connection",
                ]:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())


if __name__ == "__main__":
    server_address = (
        os.getenv("SUB_INJECTOR_HOST", "0.0.0.0"),
        int(os.getenv("SUB_INJECTOR_PORT", "8000")),
    )
    httpd = ThreadingHTTPServer(server_address, ProxyHTTPRequestHandler)
    httpd.serve_forever()
