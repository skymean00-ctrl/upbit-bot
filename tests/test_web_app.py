from __future__ import annotations

from fastapi.testclient import TestClient

from upbit_bot.config import Settings
from upbit_bot.web import create_app


class DummyUpbitClient:
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401
        self.accounts = [
            {
                "currency": "KRW",
                "balance": "12345",
                "locked": "0",
                "avg_buy_price": "0",
            }
        ]

    def get_accounts(self):  # noqa: D401
        return self.accounts

    def get_candles(self, *args, **kwargs):  # noqa: D401
        return [
            {
                "timestamp": 0,
                "opening_price": 10000,
                "high_price": 10000,
                "low_price": 10000,
                "trade_price": 10000,
                "candle_acc_trade_volume": 1,
            }
        ]


def test_dashboard_endpoints(monkeypatch):
    dummy_settings = Settings.model_validate(
        {
            "UPBIT_ACCESS_KEY": "test",
            "UPBIT_SECRET_KEY": "test",
            "UPBIT_MARKET": "KRW-BTC",
        }
    )

    monkeypatch.setattr("upbit_bot.web.app.load_settings", lambda *args, **kwargs: dummy_settings)
    monkeypatch.setattr("upbit_bot.web.app.UpbitClient", DummyUpbitClient)

    app = create_app()
    client = TestClient(app)

    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["market"] == "KRW-BTC"

    response = client.get("/balance")
    assert response.status_code == 200
    balance_data = response.json()
    assert balance_data["krw_balance"] == 12345.0
