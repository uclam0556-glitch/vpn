import pytest
from pydantic import SecretStr

import hamalivpn.payments as payments
from hamalivpn.api import FK_PLAN_DAYS, FK_PLAN_DEVICES


class _FakePlategaResponse:
    is_error = False
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {
            "transactionId": "tx_123",
            "redirect": "https://pay.example.test/tx_123",
            "status": "PENDING",
        }


class _FakeAsyncClient:
    def __init__(self, *, timeout: int):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict):
        assert url.endswith("/v2/transaction/process")
        assert json["paymentDetails"] == {"amount": 150, "currency": "RUB"}
        assert json["payload"] == "order_123"
        assert headers == {"X-MerchantId": "merchant_123", "X-Secret": "secret_123"}
        return _FakePlategaResponse()


@pytest.mark.asyncio
async def test_create_platega_link_accepts_official_redirect_field(monkeypatch) -> None:
    monkeypatch.setattr(payments.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(payments.settings, "platega_merchant_id", "merchant_123")
    monkeypatch.setattr(payments.settings, "platega_api_key", SecretStr("secret_123"))
    monkeypatch.setattr(payments.settings, "platega_api_base_url", "https://app.platega.io")
    monkeypatch.setattr(payments.settings, "bot_username", "HamaliVpn_bot")

    data = await payments.create_platega_link(
        order_id="order_123",
        amount=150,
        description="HamaliVPN · 1 месяц",
        telegram_id=5392719643,
        username="khamid",
    )

    assert data["transactionId"] == "tx_123"
    assert data["url"] == "https://pay.example.test/tx_123"


def test_payment_plan_device_limits_match_bot_and_portal_webhook() -> None:
    for code, plan in payments.PLANS.items():
        assert FK_PLAN_DAYS[code] == plan["days"]
        assert FK_PLAN_DEVICES[code] == plan["devices"]
