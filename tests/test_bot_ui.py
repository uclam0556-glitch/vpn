from pathlib import Path
from types import SimpleNamespace

import pytest

import hamalivpn.telegram_ui as telegram_ui
from hamalivpn.bot import (
    home_keyboard,
    is_news_channel_member,
    main_reply_keyboard,
    mini_app_url,
    news_channel_url,
    trial_gate_keyboard,
    welcome_text,
)
from hamalivpn.payments import PLANS, buy_keyboard
from hamalivpn.telegram_ui import inline_button


def test_welcome_is_compact_and_centers_mini_app() -> None:
    text = welcome_text("Иван")

    assert len(text) < 360
    assert "Mini App" in text
    assert "Откройте HamaliVPN" in text


def test_main_menu_has_one_primary_web_app_action() -> None:
    markup = home_keyboard()
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert len(buttons) == 6
    assert markup.inline_keyboard[0][0].web_app is not None
    assert markup.inline_keyboard[0][0].web_app.url.endswith("/tma/?screen=home")
    assert {button.callback_data for button in buttons if button.callback_data} >= {
        "subscription:show",
        "menu:buy",
        "trial:create",
        "menu:referrals",
        "help:connect",
    }


def test_persistent_keyboard_keeps_three_quick_actions() -> None:
    markup = main_reply_keyboard()
    buttons = [button for row in markup.keyboard for button in row]

    assert markup.is_persistent is True
    assert markup.resize_keyboard is True
    assert [button.text.split()[-1] for button in buttons] == [
        "HamaliVPN",
        "Подключить",
        "Помощь",
    ]
    assert buttons[0].style == "primary"
    assert buttons[1].style == "success"
    assert buttons[0].web_app is not None


def test_tariffs_keep_direct_payments_and_offer_mini_app() -> None:
    markup = buy_keyboard()
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert buttons[0].web_app is not None
    assert buttons[0].web_app.url.endswith("/tma/?screen=tariffs")
    assert {button.callback_data for button in buttons if button.callback_data} >= {
        f"platega:{code}" for code in PLANS
    }


def test_mini_app_supports_contextual_launch() -> None:
    assert mini_app_url(screen="subscription", action="connect").endswith(
        "/tma/?screen=subscription&action=connect"
    )
    root = Path(__file__).parents[1] / "src" / "hamalivpn" / "tma_web"
    script = (root / "app.js").read_text()
    page = (root / "index.html").read_text()

    assert 'launchAction === "connect"' in script
    assert 'launchScreen !== "home"' in script
    assert "/tma/app.js?v=4" in page
    assert "waitForInitData" in script
    assert "tgWebAppData" in script
    assert "sessionStorage" in script


def test_modern_button_uses_custom_icon_and_native_style(monkeypatch) -> None:
    monkeypatch.setattr(
        telegram_ui,
        "premium_emoji_ids",
        lambda: {"rocket": "custom-emoji-id"},
    )

    button = inline_button(
        "Открыть HamaliVPN",
        icon="rocket",
        style="primary",
        callback_data="open",
    )

    assert button.text == "Открыть HamaliVPN"
    assert button.icon_custom_emoji_id == "custom-emoji-id"
    assert button.style == "primary"
    assert button.callback_data == "open"


def test_modern_button_has_safe_unicode_fallback(monkeypatch) -> None:
    monkeypatch.setattr(telegram_ui, "premium_emoji_ids", lambda: {})

    button = inline_button("Подключить", icon="lightning", callback_data="connect")

    assert button.text == "⚡️ Подключить"
    assert button.icon_custom_emoji_id is None


def test_modern_button_reuses_curated_icon_pack(monkeypatch) -> None:
    monkeypatch.setattr(
        telegram_ui,
        "premium_emoji_ids",
        lambda: {"speed": "speed-emoji-id"},
    )

    button = inline_button("Подключить", icon="lightning", callback_data="connect")

    assert button.text == "Подключить"
    assert button.icon_custom_emoji_id == "speed-emoji-id"


def test_trial_gate_points_to_news_channel_and_rechecks_membership() -> None:
    markup = trial_gate_keyboard()
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert news_channel_url() == "https://t.me/hamalivpn"
    assert buttons[0].url == news_channel_url()
    assert buttons[0].style == "primary"
    assert buttons[1].callback_data == "trial:check"
    assert buttons[1].style == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "is_member", "expected"),
    [
        ("member", False, True),
        ("administrator", False, True),
        ("restricted", True, True),
        ("left", False, False),
        ("kicked", False, False),
    ],
)
async def test_news_channel_membership_statuses(
    status: str, is_member: bool, expected: bool
) -> None:
    class FakeBot:
        async def get_chat_member(self, *, chat_id: str, user_id: int):
            assert chat_id == "@hamalivpn"
            assert user_id == 42
            return SimpleNamespace(
                status=SimpleNamespace(value=status),
                is_member=is_member,
            )

    assert await is_news_channel_member(FakeBot(), 42) is expected
