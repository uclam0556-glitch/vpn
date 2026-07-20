import asyncio
import base64
import html
import ipaddress
import json
import random
import re
import socket
import string
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import UTC, datetime

import httpx
from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import case, select
from sqlalchemy import func as sa_func
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import SessionFactory
from .models import Customer, IntegrationLink, IntegrationNode, Subscription


class IntegrationState(StatesGroup):
    waiting_for_name = State()


class NodesState(StatesGroup):
    waiting_for_rename = State()


integration_router = Router()
MAX_SUBSCRIPTION_BYTES = 8 * 1024 * 1024
MAX_SUBSCRIPTION_REDIRECTS = 5
_RESERVE_PREFIX_RE = re.compile(r"^\s*\[\s*резерв\s*\]\s*", re.IGNORECASE)
_COUNTRY_FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")


def _profile_name(config: dict, fallback: str) -> str:
    for key in ("remarks", "name", "ps", "tag"):
        value = str(config.get(key) or "").strip()
        if value:
            return value[:200]
    return fallback[:200]


def _json_subscription_nodes(data: object) -> list[dict[str, str]]:
    """Keep each provider profile intact and in its original order.

    A JSON profile can contain several cooperating proxy outbounds, DNS rules,
    balancers and observatory settings. Those are routing legs of one server
    profile, not independent nodes. Splitting them changes connection semantics
    and produces hundreds of misleading entries in the bot.
    """

    configs = data if isinstance(data, list) else [data]
    nodes: list[dict[str, str]] = []
    for index, config in enumerate(configs, start=1):
        if not isinstance(config, dict):
            continue
        nodes.append(
            {
                "raw_link": json.dumps(
                    config, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ),
                "original_name": _profile_name(config, f"Custom Node {index}"),
            }
        )
    return nodes


def _node_connection_key(raw_link: str) -> str:
    """Return a stable connection identity while ignoring a display fragment."""

    value = str(raw_link or "").strip()
    if not value or value.startswith(("{", "[")):
        return value
    try:
        parsed = urllib.parse.urlsplit(value)
        if not parsed.scheme or not parsed.netloc:
            return value
        query = urllib.parse.urlencode(
            sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        )
        return urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, parsed.path, query, "")
        )
    except ValueError:
        return value.split("#", 1)[0]


def _deduplicate_subscription_nodes(nodes: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the provider's first occurrence of an identical connection."""

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in nodes:
        key = _node_connection_key(node.get("raw_link", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(node)
    return result


def parse_subscription_content(content: str) -> list[dict[str, str]]:
    """Parse common subscription formats without performing network I/O."""
    padded = content.strip() + "=" * ((4 - len(content.strip()) % 4) % 4)
    decoded = content
    for altchars in (None, b"-_"):
        try:
            decoded = base64.b64decode(padded, altchars=altchars, validate=True).decode("utf-8")
            break
        except (ValueError, UnicodeDecodeError):
            continue

    lines = [line.strip() for line in decoded.splitlines() if line.strip()]
    nodes: list[dict[str, str]] = []

    # Turn each proxy outbound into one independently selectable lossless JSON
    # profile. This preserves full transport settings without hiding nodes.
    full_text = "\n".join(lines).strip()
    if full_text.startswith(("{", "[")):
        try:
            data = json.loads(full_text)
        except json.JSONDecodeError:
            data = None
        json_nodes = _json_subscription_nodes(data)
        if json_nodes:
            return _deduplicate_subscription_nodes(json_nodes)

    for line in lines:
        if line.startswith(("{", "[")):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, (dict, list)):
                nodes.extend(_json_subscription_nodes(data))
                continue

        if "://" not in line:
            continue
        protocol = line.split("://", 1)[0].lower()
        if protocol == "vmess":
            try:
                payload = line[8:] + "=" * ((4 - len(line[8:]) % 4) % 4)
                vmess = json.loads(base64.b64decode(payload).decode("utf-8"))
                name = vmess.get("ps", "Unknown vmess Node")
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                name = "Unknown vmess Node"
        else:
            decoded_link = urllib.parse.unquote(line)
            name = (
                decoded_link.split("#", 1)[1] if "#" in decoded_link else f"Unknown {protocol} Node"
            )
        nodes.append({"raw_link": line, "original_name": str(name)[:200]})

    return _deduplicate_subscription_nodes(nodes)


async def _ensure_public_subscription_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Поддерживаются только публичные HTTP(S)-ссылки")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Домен подписки не разрешается")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Локальные и служебные адреса запрещены")


async def fetch_and_parse_subscription(
    url: str, hwid: str, user_agent: str
) -> list[dict[str, str]]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "X-App-Version": "4.14.0",
        "X-Device-Locale": "ru",
        "X-Device-Model": "iPhone 15 Pro Max",
        "X-Device-Os": "iOS",
        "X-Hwid": hwid,
        "X-Ver-Os": "26.5",
    }
    current_url = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=20.0) as client:
        for redirect_count in range(MAX_SUBSCRIPTION_REDIRECTS + 1):
            # Revalidate every redirect target. Validating only the first URL
            # allows a public endpoint to redirect the importer into a private
            # network (SSRF).
            await _ensure_public_subscription_url(current_url)
            async with client.stream("GET", current_url, headers=headers) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    if redirect_count >= MAX_SUBSCRIPTION_REDIRECTS:
                        raise ValueError("Слишком много перенаправлений подписки")
                    current_url = urllib.parse.urljoin(current_url, location)
                    continue

                response.raise_for_status()
                if int(response.headers.get("content-length") or 0) > MAX_SUBSCRIPTION_BYTES:
                    raise ValueError("Подписка превышает допустимый размер")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_SUBSCRIPTION_BYTES:
                        raise ValueError("Подписка превышает допустимый размер")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                content = b"".join(chunks).decode(encoding, errors="strict")
                return parse_subscription_content(content)

    raise ValueError("Не удалось получить подписку")


def _normalized_profile_name(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def clean_node_display_name(value: str) -> str:
    """Remove the legacy category prefix without touching the actual name."""

    return " ".join(_RESERVE_PREFIX_RE.sub("", str(value or "")).split())


def renamed_node_display_name(
    requested_name: str, *, original_name: str, current_name: str = ""
) -> str:
    """Change only node text while preserving its existing country flag."""

    requested = clean_node_display_name(requested_name)
    existing_flag_match = _COUNTRY_FLAG_RE.search(current_name) or _COUNTRY_FLAG_RE.search(
        original_name
    )
    if not existing_flag_match:
        return requested[:200]

    flag = existing_flag_match.group(0)
    text = " ".join(_COUNTRY_FLAG_RE.sub("", requested).split())
    if not text:
        return ""

    placement_source = clean_node_display_name(current_name)
    if flag not in placement_source:
        placement_source = clean_node_display_name(original_name)
    if placement_source.startswith(flag):
        text = text[: 200 - len(flag) - 1]
        return f"{flag} {text}"
    text = text[: 200 - len(flag) - 1]
    return f"{text} {flag}"


async def synchronize_integration_nodes(
    session, link_id: int, incoming_nodes: list[dict[str, str]]
) -> dict[str, int]:
    """Atomically mirror one provider snapshot while preserving active choices.

    Exact raw profiles win. If a provider rotates connection credentials but
    keeps a unique profile name, update that row in place so its active state and
    administrator-defined display name survive. Rows absent from the new source
    snapshot are removed to prevent stale credentials from being published.
    """

    result = await session.execute(select(IntegrationNode).filter_by(link_id=link_id))
    existing = list(result.scalars().all())
    by_raw: dict[str, list[IntegrationNode]] = defaultdict(list)
    by_name: dict[str, list[IntegrationNode]] = defaultdict(list)
    for node in existing:
        by_raw[node.raw_link].append(node)
        by_name[_normalized_profile_name(node.original_name)].append(node)

    clean_incoming: list[dict[str, str]] = []
    seen_raw: set[str] = set()
    for source_position, item in enumerate(incoming_nodes):
        raw_link = str(item.get("raw_link") or "").strip()
        original_name = str(item.get("original_name") or "Custom Node").strip()[:200]
        if not raw_link or raw_link in seen_raw:
            continue
        seen_raw.add(raw_link)
        clean_incoming.append(
            {
                "raw_link": raw_link,
                "original_name": original_name,
                "source_position": source_position,
            }
        )

    incoming_name_counts = Counter(
        _normalized_profile_name(item["original_name"]) for item in clean_incoming
    )
    retained_ids: set[int] = set()
    added = updated = 0

    def available(candidates: list[IntegrationNode]) -> list[IntegrationNode]:
        return [node for node in candidates if node.id not in retained_ids]

    for item in clean_incoming:
        raw_link = item["raw_link"]
        original_name = item["original_name"]
        source_position = item["source_position"]
        candidates = available(by_raw.get(raw_link, []))
        candidate = next((node for node in candidates if node.is_active), None)
        candidate = candidate or (candidates[0] if candidates else None)

        normalized_name = _normalized_profile_name(original_name)
        if candidate is None and incoming_name_counts[normalized_name] == 1:
            candidates = available(by_name.get(normalized_name, []))
            candidate = next((node for node in candidates if node.is_active), None)
            candidate = candidate or (candidates[0] if candidates else None)

        if candidate is None:
            session.add(
                IntegrationNode(
                    link_id=link_id,
                    raw_link=raw_link,
                    original_name=original_name,
                    display_name=original_name,
                    source_position=source_position,
                )
            )
            added += 1
            continue

        retained_ids.add(candidate.id)
        if (
            candidate.raw_link != raw_link
            or candidate.original_name != original_name
            or candidate.source_position != source_position
        ):
            candidate.raw_link = raw_link
            candidate.original_name = original_name
            candidate.source_position = source_position
            updated += 1

    removed = 0
    for node in existing:
        if node.id not in retained_ids:
            await session.delete(node)
            removed += 1

    return {"added": added, "updated": updated, "removed": removed}


@integration_router.message(Command("integrate"))
async def start_integration(message: Message) -> None:
    settings = get_settings()
    if message.from_user.id not in settings.admin_ids:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /integrate <ссылка_на_подписку>")
        return

    url = parts[1].strip()
    status_msg = await message.answer(
        "🕵️‍♂️ Скачиваю подписку в режиме 'Стелс' (ищу ваш настоящий HWID в базе, чтобы обмануть их защиту)..."
    )

    hwid = None
    async with SessionFactory() as session:
        result = await session.execute(
            select(Customer)
            .options(selectinload(Customer.subscriptions).selectinload(Subscription.devices))
            .filter_by(telegram_id=message.from_user.id)
        )
        customer = result.scalars().first()
        if customer:
            for sub in customer.subscriptions:
                for device in sub.devices:
                    if device.user_agent and "|HWID:" in device.user_agent:
                        hwid = device.user_agent.split("|HWID:")[-1].split("|")[0].strip()
                        break
                    elif device.remnawave_uuid and len(device.remnawave_uuid) > 5:
                        hwid = device.remnawave_uuid
                        # don't break, keep looking for a real HWID
                if hwid and len(hwid) == 16:
                    break  # if we found a 16-char hex HWID, we're good

    if not hwid:
        hwid = "".join(random.choices(string.digits, k=16))

    user_agent = f"Happ/4.14.0/ios/{hwid}"

    try:
        nodes = await fetch_and_parse_subscription(url, hwid, user_agent)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка скачивания: {str(e)}")
        return

    if not nodes:
        await status_msg.edit_text("❌ В подписке не найдены серверы VLESS/Hysteria.")
        return

    async with SessionFactory() as session:
        link = IntegrationLink(url=url, hwid=hwid, user_agent=user_agent)
        session.add(link)
        await session.flush()

        for source_position, n in enumerate(nodes):
            node = IntegrationNode(
                link_id=link.id,
                raw_link=n["raw_link"],
                original_name=n["original_name"],
                display_name=n["original_name"],
                source_position=source_position,
            )
            session.add(node)

        await session.commit()
        await show_integration_menu(status_msg, link.id, session, is_edit=True)


async def show_integration_menu(
    message_or_query, link_id: int, session, is_edit=False, chat_id=None, msg_id=None
):
    result = await session.execute(
        select(IntegrationNode)
        .filter_by(link_id=link_id)
        .order_by(IntegrationNode.source_position, IntegrationNode.id)
    )
    nodes = result.scalars().all()

    keyboard = []
    for node in nodes:
        status = "✅" if node.is_active else "❌"
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {node.display_name}", callback_data=f"intg_toggle_{node.id}"
                ),
                InlineKeyboardButton(text="✏️", callback_data=f"intg_edit_{node.id}"),
            ]
        )

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    text = f"<b>Интеграция успешно сохранена!</b>\n\nВключено серверов: <b>{sum(1 for n in nodes if n.is_active)}/{len(nodes)}</b>\n<i>Включенные серверы автоматически добавятся ко всем вашим клиентам. Оригинальный провайдер не увидит ваших клиентов.</i>"

    if chat_id and msg_id:
        await message_or_query.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=msg_id,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    elif isinstance(message_or_query, Message):
        if is_edit:
            await message_or_query.edit_text(
                text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        else:
            await message_or_query.answer(
                text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
    else:
        await message_or_query.message.edit_text(
            text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )


@integration_router.callback_query(F.data.startswith("intg_toggle_"))
async def toggle_node(callback_query: CallbackQuery) -> None:
    settings = get_settings()
    if callback_query.from_user.id not in settings.admin_ids:
        return

    node_id = int(callback_query.data.split("_")[-1])
    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if node:
            node.is_active = not node.is_active
            await session.commit()
            await show_integration_menu(callback_query, node.link_id, session)


@integration_router.callback_query(F.data.startswith("intg_edit_"))
async def prompt_edit_name(callback_query: CallbackQuery, state: FSMContext) -> None:
    settings = get_settings()
    if callback_query.from_user.id not in settings.admin_ids:
        return

    node_id = int(callback_query.data.split("_")[-1])
    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if node:
            msg = await callback_query.message.answer(
                "Введите новое имя для сервера:\n\n"
                f"Текущее: <b>{html.escape(node.display_name)}</b>\n\n"
                "<i>Просто отправьте новое имя текстом.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True),
            )
            await state.set_state(IntegrationState.waiting_for_name)
            await state.update_data(
                node_id=node.id,
                prompt_msg_id=msg.message_id,
                menu_msg_id=callback_query.message.message_id,
            )
            await callback_query.answer()


@integration_router.message(IntegrationState.waiting_for_name)
async def process_new_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    node_id = data.get("node_id")
    requested_name = " ".join(str(message.text or "").split())[:200]
    if not requested_name:
        await message.answer("❌ Имя не может быть пустым. Отправьте новое название текстом.")
        return

    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if node:
            new_name = renamed_node_display_name(
                requested_name,
                original_name=node.original_name,
                current_name=node.display_name,
            )
            if not new_name:
                await message.answer(
                    "❌ Имя не может быть пустым. Отправьте новое название текстом."
                )
                return
            node.display_name = new_name
            await session.commit()

            try:
                await message.delete()
                await message.bot.delete_message(
                    chat_id=message.chat.id, message_id=data.get("prompt_msg_id")
                )
            except Exception:
                pass

            try:
                await show_integration_menu(
                    message.bot,
                    node.link_id,
                    session,
                    chat_id=message.chat.id,
                    msg_id=data.get("menu_msg_id"),
                )
            except Exception:
                pass

    await state.clear()


# ─────────────────────────────────────────────────────────────────────────────
#  /nodes — полный менеджер интегрированных серверов
# ─────────────────────────────────────────────────────────────────────────────


def _short_url(url: str, max_len: int = 45) -> str:
    """Trim long URLs to fit a button label."""
    return url if len(url) <= max_len else url[: max_len - 1] + "…"


def _compact_node_name(value: str, max_len: int = 46) -> str:
    """Shorten a Telegram label while keeping a differentiating outbound tag."""

    name = " ".join(str(value or "").split())
    if len(name) <= max_len:
        return name
    if " · " in name:
        prefix, suffix = name.rsplit(" · ", 1)
        suffix = suffix[:18]
        prefix_budget = max_len - len(suffix) - 3
        if prefix_budget >= 4:
            return f"{prefix[: prefix_budget - 1]}… · {suffix}"
    return name[: max_len - 1] + "…"


def parse_node_address(raw_link: str) -> tuple[str, int] | tuple[None, None]:
    try:
        if raw_link.startswith("vmess://"):
            data = json.loads(base64.b64decode(raw_link[8:]).decode("utf-8"))
            return data.get("add"), int(data.get("port", 0))
        elif raw_link.startswith(("vless://", "trojan://", "ss://")):
            parsed = urllib.parse.urlparse(raw_link)
            if parsed.hostname and parsed.port:
                return parsed.hostname, int(parsed.port)
        elif raw_link.startswith("{"):
            data = json.loads(raw_link)
            configured = data.get("outbounds")
            outbounds = configured if isinstance(configured, list) else [data]
            candidates = [
                outbound
                for outbound in outbounds
                if isinstance(outbound, dict)
                and outbound.get("protocol") in {"vless", "vmess", "trojan", "shadowsocks"}
            ]
            primary = next(
                (
                    outbound
                    for outbound in candidates
                    if str(outbound.get("tag") or "").strip().lower()
                    in {"proxy", "primary", "main", "default"}
                ),
                candidates[0] if candidates else None,
            )
            if primary:
                settings = primary.get("settings") or {}
                servers = settings.get("vnext") or settings.get("servers") or []
                if servers and isinstance(servers[0], dict):
                    host = servers[0].get("address") or servers[0].get("server")
                    port = servers[0].get("port") or servers[0].get("server_port")
                    if host and port:
                        return str(host), int(port)

            # Also support lightweight JSON formats with top-level endpoint keys.
            host = data.get("server") or data.get("address") or data.get("add")
            port = data.get("server_port") or data.get("port")
            if host and port:
                return host, int(port)
    except Exception:
        pass
    return None, None


async def tcp_ping(host: str, port: int, connect_timeout: float = 1.0) -> float | None:
    if not host or not port:
        return None
    start = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=connect_timeout
        )
        writer.close()
        await writer.wait_closed()
        return (time.perf_counter() - start) * 1000
    except Exception:
        return None


async def _nodes_links_keyboard(session) -> tuple[str, InlineKeyboardMarkup]:
    """Build the top-level list of all integration links."""
    result = await session.execute(
        select(
            IntegrationLink,
            sa_func.count(IntegrationNode.id).label("total"),
            sa_func.sum(case((IntegrationNode.is_active.is_(True), 1), else_=0)).label("active"),
        )
        .outerjoin(IntegrationNode, IntegrationNode.link_id == IntegrationLink.id)
        .group_by(IntegrationLink.id)
        .order_by(IntegrationLink.id)
    )
    rows = result.all()

    # Deduplicate by URL, keep only the one with the most active nodes (or most nodes total)
    seen: dict[str, tuple] = {}
    for link, total, active in rows:
        key = link.url
        active_n = active or 0
        total_n = total or 0
        if key not in seen or (active_n, total_n) > seen[key][1:]:
            seen[key] = (link, active_n, total_n)

    if not seen:
        text = "📭 <b>Нет добавленных интеграций.</b>\n\nИспользуйте /integrate <ссылка> чтобы добавить."
        return text, InlineKeyboardMarkup(inline_keyboard=[])

    keyboard = []
    for url, (link, active_n, total_n) in seen.items():
        label = f"{'✅' if active_n else '❌'} {_short_url(url)} ({active_n}/{total_n})"
        keyboard.append(
            [
                InlineKeyboardButton(text=label, callback_data=f"nodes_link_{link.id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"nodes_del_link_{link.id}"),
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="nodes_refresh_list")]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                text="👁 Показать все включенные серверы", callback_data="nodes_global_active_0"
            )
        ]
    )

    text = (
        "🗂 <b>Все интегрированные подписки</b>\n\n"
        "Нажмите на строку чтобы управлять серверами.\n"
        "🗑 — удалить всю подписку вместе с её серверами."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _nodes_link_keyboard(
    link_id: int, session, page: int = 0, only_active: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the per-link server list keyboard."""
    link = await session.get(IntegrationLink, link_id)
    result = await session.execute(
        select(IntegrationNode)
        .filter_by(link_id=link_id)
        .order_by(IntegrationNode.source_position, IntegrationNode.id)
    )
    nodes = result.scalars().all()

    active_count = sum(1 for n in nodes if n.is_active)

    if only_active:
        filtered_nodes = [n for n in nodes if n.is_active]
    else:
        filtered_nodes = nodes

    # Keep the exact provider order, with the nodes already published to users
    # at the top. A control-server TCP timing is useful diagnostics, but it is
    # not the same measurement as the end user's Happ latency and must not
    # reshuffle the provider's list.
    sorted_nodes = sorted(
        filtered_nodes,
        key=lambda node: (not node.is_active, node.source_position, node.id),
    )

    PAGE_SIZE = 8
    total_pages = max(1, (len(sorted_nodes) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_nodes = sorted_nodes[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    # Probe only the visible page. This keeps /nodes responsive even when a
    # provider sends hundreds of entries.
    ping_tasks = []

    async def _none_ping():
        return None

    for node in page_nodes:
        host, port = parse_node_address(node.raw_link)
        if host and port:
            ping_tasks.append(tcp_ping(host, port, connect_timeout=1.5))
        else:
            ping_tasks.append(_none_ping())

    pings = await asyncio.gather(*ping_tasks)

    pings_map = {node.id: ping for node, ping in zip(page_nodes, pings, strict=True)}

    text = (
        f"📡 <b>{_short_url(link.url)}</b>\n\n"
        f"Серверов: <b>{active_count}/{len(nodes)} активны</b>\n"
        f"<i>Сначала включённые, затем порядок источника</i>\n"
        f"<i>TCP — проверка от панели, пинг в Happ измеряется с устройства</i>\n\n"
        "✅/❌ — вкл/выкл · ✏️ — переименовать · 🗑 — удалить"
    )

    keyboard = []
    for node in page_nodes:
        icon = "✅" if node.is_active else "❌"
        ping = pings_map[node.id]
        ping_str = f"{int(ping)}ms" if ping is not None else "нет TCP"
        name = f"{_compact_node_name(node.display_name)} ({ping_str})"
        filter_flag = 1 if only_active else 0
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {name}",
                    callback_data=f"nodes_toggle_{node.id}_{link_id}_{page}_{filter_flag}",
                ),
                InlineKeyboardButton(
                    text="✏️", callback_data=f"nodes_rename_{node.id}_{link_id}_{page}_{filter_flag}"
                ),
                InlineKeyboardButton(
                    text="🗑", callback_data=f"nodes_del_{node.id}_{link_id}_{page}_{filter_flag}"
                ),
            ]
        )

    nav = []
    filter_flag = 1 if only_active else 0
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️", callback_data=f"nodes_page_{link_id}_{page - 1}_{filter_flag}"
            )
        )
    if total_pages > 1:
        nav.append(
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="nodes_noop")
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="▶️", callback_data=f"nodes_page_{link_id}_{page + 1}_{filter_flag}"
            )
        )
    if nav:
        keyboard.append(nav)

    filter_text = "👁 Показать все" if only_active else "👁 Только включенные"
    filter_val = 0 if only_active else 1
    keyboard.append(
        [
            InlineKeyboardButton(
                text=filter_text, callback_data=f"nodes_filter_{link_id}_{filter_val}"
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить с источника", callback_data=f"nodes_resync_{link_id}"
            ),
            InlineKeyboardButton(text="◀️ Назад", callback_data="nodes_back_list"),
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _nodes_global_active_keyboard(session, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Build a global list of ALL active servers across all links."""
    from sqlalchemy import select

    result = await session.execute(
        select(IntegrationNode)
        .filter(IntegrationNode.is_active.is_(True))
        .order_by(IntegrationNode.id)
    )
    nodes = result.scalars().all()

    ping_tasks = []

    async def _none_ping():
        return None

    for node in nodes:
        host, port = parse_node_address(node.raw_link)
        if host and port:
            ping_tasks.append(tcp_ping(host, port, connect_timeout=1.5))
        else:
            ping_tasks.append(_none_ping())

    pings = await asyncio.gather(*ping_tasks)

    node_pings = list(zip(nodes, pings, strict=True))

    def sort_key(item):
        ping = item[1]
        return ping if ping is not None else float("inf")

    node_pings.sort(key=sort_key)
    sorted_nodes = [item[0] for item in node_pings]
    pings_map = {item[0].id: item[1] for item in node_pings}

    PAGE_SIZE = 8
    total_pages = max(1, (len(sorted_nodes) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_nodes = sorted_nodes[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    text = (
        f"🌐 <b>Все включенные серверы</b>\n\n"
        f"Всего включено: <b>{len(nodes)}</b>\n"
        f"<i>(Отсортировано по пингу ⚡️)</i>\n\n"
        "✅/❌ — вкл/выкл · ✏️ — переименовать"
    )

    keyboard = []
    for node in page_nodes:
        icon = "✅" if node.is_active else "❌"
        ping = pings_map[node.id]
        ping_str = f"{int(ping)}ms" if ping is not None else "нет TCP"
        name = f"{_compact_node_name(node.display_name)} ({ping_str})"
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {name}", callback_data=f"nodes_gtoggle_{node.id}_{page}"
                ),
                InlineKeyboardButton(text="✏️", callback_data=f"nodes_grename_{node.id}_{page}"),
            ]
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"nodes_global_active_{page - 1}"))
    if total_pages > 1:
        nav.append(
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="nodes_noop")
        )
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"nodes_global_active_{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append(
        [
            InlineKeyboardButton(text="◀️ Назад к подпискам", callback_data="nodes_back_list"),
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


@integration_router.message(Command("nodes"))
async def cmd_nodes(message: Message) -> None:
    settings = get_settings()
    if message.from_user.id not in settings.admin_ids:
        return
    async with SessionFactory() as session:
        text, markup = await _nodes_links_keyboard(session)
    await message.answer(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


@integration_router.callback_query(F.data == "nodes_refresh_list")
async def nodes_refresh_list(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    async with SessionFactory() as session:
        text, markup = await _nodes_links_keyboard(session)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer("Обновлено")


@integration_router.callback_query(F.data.startswith("nodes_link_"))
async def nodes_open_link(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    link_id = int(cb.data.split("_")[-1])
    async with SessionFactory() as session:
        text, markup = await _nodes_link_keyboard(link_id, session)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer()


@integration_router.callback_query(F.data == "nodes_back_list")
async def nodes_back_list(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    async with SessionFactory() as session:
        text, markup = await _nodes_links_keyboard(session)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer()


@integration_router.callback_query(F.data == "nodes_noop")
async def nodes_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@integration_router.callback_query(F.data.startswith("nodes_page_"))
async def nodes_page(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    parts = cb.data.split("_")
    link_id, page = int(parts[2]), int(parts[3])
    only_active = bool(int(parts[4])) if len(parts) > 4 else False
    async with SessionFactory() as session:
        text, markup = await _nodes_link_keyboard(link_id, session, page, only_active)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer()


@integration_router.callback_query(F.data.startswith("nodes_toggle_"))
async def nodes_toggle(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    parts = cb.data.split("_")
    node_id, link_id, page = int(parts[2]), int(parts[3]), int(parts[4])
    only_active = bool(int(parts[5])) if len(parts) > 5 else False
    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if node:
            node.is_active = not node.is_active
            await session.commit()
        text, markup = await _nodes_link_keyboard(link_id, session, page, only_active)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer("✅ Включён" if node and node.is_active else "❌ Выключен")


@integration_router.callback_query(F.data.startswith("nodes_rename_"))
async def nodes_rename_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    parts = cb.data.split("_")
    node_id, link_id, page = int(parts[2]), int(parts[3]), int(parts[4])
    only_active = int(parts[5]) if len(parts) > 5 else 0
    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if not node:
            await cb.answer("Сервер не найден")
            return
        current_name = node.display_name

    prompt = await cb.message.answer(
        f"✏️ Введите новое имя для сервера:\n\n"
        f"Текущее: <b>{html.escape(current_name)}</b>\n\n"
        f"<i>Просто отправьте новое имя текстом.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(selective=True),
    )
    await state.set_state(NodesState.waiting_for_rename)
    await state.update_data(
        node_id=node_id,
        link_id=link_id,
        page=page,
        only_active=only_active,
        prompt_msg_id=prompt.message_id,
        menu_msg_id=cb.message.message_id,
    )
    await cb.answer()


@integration_router.message(NodesState.waiting_for_rename)
async def nodes_rename_apply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    requested_name = " ".join(str(message.text or "").split())[:200]
    if not requested_name:
        await message.answer("❌ Имя не может быть пустым. Отправьте новое название текстом.")
        return
    node_id = data.get("node_id")
    page = data.get("page", 0)
    saved = False
    new_name = ""

    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if not node:
            await state.clear()
            await message.answer("❌ Сервер не найден. Откройте /nodes и повторите.")
            return
        new_name = renamed_node_display_name(
            requested_name,
            original_name=node.original_name,
            current_name=node.display_name,
        )
        if not new_name:
            await message.answer("❌ Имя не может быть пустым. Отправьте новое название текстом.")
            return
        node.display_name = new_name
        await session.commit()
        saved = True

        if data.get("is_global"):
            text, markup = await _nodes_global_active_keyboard(session, page)
        else:
            link_id = data.get("link_id")
            only_active = bool(data.get("only_active", 0))
            text, markup = await _nodes_link_keyboard(link_id, session, page, only_active)

    try:
        await message.delete()
        await message.bot.delete_message(message.chat.id, data["prompt_msg_id"])
    except Exception:
        pass

    try:
        await message.bot.edit_message_text(
            text,
            chat_id=message.chat.id,
            message_id=data["menu_msg_id"],
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        pass

    await state.clear()
    if saved:
        await message.answer(
            f"✅ Название сохранено: <b>{html.escape(new_name)}</b>\n"
            "Обновите подписку в Happ/Incy — новое имя появится без изменения VPN-конфигурации.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.answer("❌ Сервер не найден. Откройте /nodes и повторите.")


@integration_router.callback_query(
    F.data.startswith("nodes_del_") & ~F.data.startswith("nodes_del_link_")
)
async def nodes_delete_node(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    parts = cb.data.split("_")
    node_id, link_id, page = int(parts[2]), int(parts[3]), int(parts[4])
    only_active = bool(int(parts[5])) if len(parts) > 5 else False
    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if node:
            await session.delete(node)
            await session.commit()
        text, markup = await _nodes_link_keyboard(link_id, session, page, only_active)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer("🗑 Сервер удалён")


@integration_router.callback_query(F.data.startswith("nodes_del_link_"))
async def nodes_delete_link(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    link_id = int(cb.data.split("_")[-1])
    async with SessionFactory() as session:
        link = await session.get(IntegrationLink, link_id)
        if link:
            await session.delete(link)  # CASCADE deletes nodes too
            await session.commit()
        text, markup = await _nodes_links_keyboard(session)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer("🗑 Подписка удалена")


@integration_router.callback_query(F.data.startswith("nodes_filter_"))
async def nodes_filter_toggle(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    parts = cb.data.split("_")
    link_id = int(parts[2])
    only_active = bool(int(parts[3]))
    async with SessionFactory() as session:
        text, markup = await _nodes_link_keyboard(link_id, session, 0, only_active)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer()


@integration_router.callback_query(F.data.startswith("nodes_resync_"))
async def nodes_resync(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    link_id = int(cb.data.split("_")[-1])

    await cb.answer("🔄 Обновляю с источника…")
    await cb.message.edit_text(
        "🔄 <b>Скачиваю обновлённый список серверов…</b>", parse_mode=ParseMode.HTML
    )

    async with SessionFactory() as session:
        link = await session.get(IntegrationLink, link_id)
        if not link:
            await cb.message.edit_text("❌ Подписка не найдена.")
            return
        url, hwid, user_agent = link.url, link.hwid, link.user_agent

    try:
        new_nodes = await fetch_and_parse_subscription(url, hwid, user_agent)
    except Exception as e:
        await cb.message.edit_text(
            f"❌ Ошибка скачивания:\n<code>{e}</code>", parse_mode=ParseMode.HTML
        )
        return

    if not new_nodes:
        await cb.message.edit_text("❌ В подписке не найдены серверы.")
        return

    async with SessionFactory() as session:
        changes = await synchronize_integration_nodes(session, link_id, new_nodes)
        link_obj = await session.get(IntegrationLink, link_id)
        if link_obj:
            link_obj.last_fetched_at = datetime.now(UTC)
        await session.commit()
        text, markup = await _nodes_link_keyboard(link_id, session)

    await cb.message.edit_text(
        text
        + (
            "\n\n✅ Синхронизация завершена: "
            f"добавлено <b>{changes['added']}</b>, "
            f"обновлено <b>{changes['updated']}</b>, "
            f"удалено устаревших <b>{changes['removed']}</b>."
        ),
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


@integration_router.callback_query(F.data.startswith("nodes_global_active_"))
async def nodes_global_active(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    page = int(cb.data.split("_")[-1])
    async with SessionFactory() as session:
        text, markup = await _nodes_global_active_keyboard(session, page)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer()


@integration_router.callback_query(F.data.startswith("nodes_gtoggle_"))
async def nodes_gtoggle(cb: CallbackQuery) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    parts = cb.data.split("_")
    node_id, page = int(parts[2]), int(parts[3])
    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if node:
            node.is_active = not node.is_active
            await session.commit()
        text, markup = await _nodes_global_active_keyboard(session, page)
    await cb.message.edit_text(
        text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    await cb.answer("❌ Выключен" if node and not node.is_active else "✅ Включён")


@integration_router.callback_query(F.data.startswith("nodes_grename_"))
async def nodes_grename_prompt(cb: CallbackQuery, state: FSMContext) -> None:
    settings = get_settings()
    if cb.from_user.id not in settings.admin_ids:
        await cb.answer()
        return
    parts = cb.data.split("_")
    node_id, page = int(parts[2]), int(parts[3])
    async with SessionFactory() as session:
        node = await session.get(IntegrationNode, node_id)
        if not node:
            await cb.answer("Сервер не найден")
            return
        current_name = node.display_name

    prompt = await cb.message.answer(
        f"✏️ Введите новое имя для сервера:\n\n"
        f"Текущее: <b>{html.escape(current_name)}</b>\n\n"
        f"<i>Просто отправьте новое имя текстом.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(selective=True),
    )
    await state.set_state(NodesState.waiting_for_rename)
    await state.update_data(
        node_id=node_id,
        is_global=True,
        page=page,
        prompt_msg_id=prompt.message_id,
        menu_msg_id=cb.message.message_id,
    )
    await cb.answer()
