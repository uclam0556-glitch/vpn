from __future__ import annotations

import html
import json
import os
from functools import lru_cache
from typing import Any

from .config import get_settings

FALLBACKS: dict[str, str] = {
    "brand": "🛡",
    "wave": "👋",
    "gift": "🎁",
    "card": "💳",
    "user": "👤",
    "star": "⭐️",
    "book": "📘",
    "diamond": "💎",
    "chat": "💬",
    "doc": "📄",
    "phone": "📲",
    "refresh": "🔄",
    "repeat": "🔁",
    "home": "🏠",
    "support": "💬",
    "shield": "🛡",
    "lightning": "⚡️",
    "rocket": "🚀",
    "sparkles": "✨",
    "fire": "🔥",
    "speed": "⚡️",
    "connect": "📲",
    "active": "🟢",
    "calendar": "📅",
    "money": "💰",
    "bank": "🏦",
    "link": "🔗",
    "check": "✅",
    "warning": "⚠️",
    "blocked": "🚫",
    "world": "🌍",
    "lock": "🔒",
    "scroll": "📜",
    "key": "🔑",
    "clock": "⏳",
    "green": "🟢",
    "yellow": "🟡",
    "red": "🔴",
    "white": "⚪️",
    "apple": "🍎",
    "android": "🤖",
    "desktop": "💻",
}


def _parse_mapping(raw: str) -> dict[str, str]:
    raw = raw.strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        return {
            str(key).strip(): str(value).strip()
            for key, value in parsed.items()
            if str(key).strip() and str(value).strip()
        }

    mapping: dict[str, str] = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            mapping[key] = value
    return mapping


@lru_cache(maxsize=1)
def premium_emoji_ids() -> dict[str, str]:
    settings = get_settings()
    raw = os.getenv("PREMIUM_EMOJI_JSON", "") or settings.premium_emoji_json
    return _parse_mapping(raw)


def ce(name: str, fallback: str | None = None) -> str:
    """Return Telegram HTML custom emoji tag with a safe unicode fallback.

    Inline keyboard buttons do not support message entities, so this helper is
    only for HTML messages/captions.
    """

    fallback = fallback or FALLBACKS.get(name, "")
    emoji_id = premium_emoji_ids().get(name)
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{html.escape(emoji_id, quote=True)}">{html.escape(fallback)}</tg-emoji>'


def plain(name: str, fallback: str | None = None) -> str:
    """Plain unicode icon for places where Telegram entities are unsupported."""

    return fallback or FALLBACKS.get(name, "")


def _index_from_utf16(text: str, target_units: int) -> int:
    units = 0
    for index, char in enumerate(text):
        if units >= target_units:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def _slice_by_utf16(text: str, offset: int, length: int) -> str:
    start = _index_from_utf16(text, offset)
    end = _index_from_utf16(text, offset + length)
    return text[start:end]


def collect_custom_emojis(message: Any) -> list[dict[str, str]]:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    entities = list(getattr(message, "entities", None) or [])
    entities.extend(getattr(message, "caption_entities", None) or [])

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for entity in entities:
        if getattr(entity, "type", None) != "custom_emoji":
            continue
        custom_emoji_id = getattr(entity, "custom_emoji_id", None)
        if not custom_emoji_id or custom_emoji_id in seen:
            continue
        seen.add(custom_emoji_id)
        result.append(
            {
                "fallback": _slice_by_utf16(
                    text,
                    int(getattr(entity, "offset", 0) or 0),
                    int(getattr(entity, "length", 0) or 0),
                ),
                "custom_emoji_id": str(custom_emoji_id),
            }
        )
    return result
