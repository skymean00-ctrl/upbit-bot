import pytest

from upbit_bot.live.logging import TradeLogger
from upbit_bot.live.notifications import NotificationError, NotificationManager


def test_trade_logger_writes_json(tmp_path):
    logger = TradeLogger(log_dir=tmp_path)
    logger.log_execution("order-1", "buy", 10_000, 0.1, extra={"market": "KRW-BTC"})
    logger.log_pnl(10_000, 500)
    log_file = tmp_path / "trading.log"
    assert log_file.exists()
    content = log_file.read_text().splitlines()
    assert any("execution" in line for line in content)
    assert any("pnl" in line for line in content)


def test_notification_manager_bubbles_up_errors():
    class Dummy:
        def __init__(self):
            self.calls = 0

        def send(self, message: str) -> None:
            self.calls += 1
            if message == "fail":
                raise RuntimeError("boom")

    manager = NotificationManager([Dummy()])
    with pytest.raises(NotificationError):
        manager.notify("fail")
