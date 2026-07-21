from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, KeyboardButton

from .premium_emoji import plain, premium_emoji_ids

# Production already has a curated core icon pack. New button concepts reuse the
# closest icon until their dedicated emoji is added; exact mappings always win.
_ICON_ALIASES = {
    "rocket": "connect",
    "lightning": "speed",
    "card": "diamond",
    "sparkles": "active",
    "link": "connect",
    "check": "active",
    "warning": "shield",
    "blocked": "shield",
    "world": "brand",
    "lock": "shield",
    "scroll": "doc",
    "money": "diamond",
    "bank": "diamond",
    "refresh": "calendar",
}


def _visual(text: str, icon: str | None) -> tuple[str, str | None]:
    if not icon:
        return text, None
    icons = premium_emoji_ids()
    icon_id = icons.get(icon) or icons.get(_ICON_ALIASES.get(icon, ""))
    if icon_id:
        return text, icon_id
    fallback = plain(icon)
    return (f"{fallback} {text}" if fallback else text), None


def inline_button(
    text: str,
    *,
    icon: str | None = None,
    style: str | None = None,
    **action: Any,
) -> InlineKeyboardButton:
    """Build a modern Telegram button with a Unicode-safe fallback."""

    label, icon_id = _visual(text, icon)
    return InlineKeyboardButton(
        text=label,
        icon_custom_emoji_id=icon_id,
        style=style,
        **action,
    )


def reply_button(
    text: str,
    *,
    icon: str | None = None,
    style: str | None = None,
    **action: Any,
) -> KeyboardButton:
    """Build a persistent-menu button using the same visual language."""

    label, icon_id = _visual(text, icon)
    return KeyboardButton(
        text=label,
        icon_custom_emoji_id=icon_id,
        style=style,
        **action,
    )
