from pathlib import Path

from hamalivpn.bot import home_keyboard, main_reply_keyboard, mini_app_url, welcome_text
from hamalivpn.payments import PLANS, buy_keyboard


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
    assert [button.text for button in buttons] == [
        "🚀 Открыть HamaliVPN",
        "⚡ Подключить",
        "🛟 Помощь",
    ]
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
    assert "/tma/app.js?v=3" in page
