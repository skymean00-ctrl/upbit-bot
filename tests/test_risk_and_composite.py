from dataclasses import dataclass

from upbit_bot.services.risk import PositionSizer, RiskConfig, RiskManager
from upbit_bot.strategies import Candle, CompositeStrategy, Strategy, StrategySignal


@dataclass
class DummyStrategy(Strategy):
    name: str
    signal: StrategySignal

    def on_candles(self, candles):
        return self.signal


def test_composite_strategy_weighted_vote():
    candles = [Candle(timestamp=0, open=1, high=1, low=1, close=1, volume=1)]
    s1 = DummyStrategy(name="s1", signal=StrategySignal.BUY)
    s2 = DummyStrategy(name="s2", signal=StrategySignal.SELL)
    composite = CompositeStrategy([(s1, 0.7), (s2, 0.3)])
    assert composite.on_candles(candles) is StrategySignal.BUY


def test_risk_manager_blocks_after_loss():
    balance = 100_000

    def fetch_balance():
        return balance

    config = RiskConfig(max_daily_loss_pct=1.0, max_open_positions=1)
    manager = RiskManager(balance_fetcher=fetch_balance, config=config)
    assert manager.can_open_position("KRW-BTC")
    manager.register_entry("KRW-BTC", stake_pct=5.0)
    manager.register_exit("KRW-BTC", pnl_pct=-2.0)
    assert manager.can_open_position("KRW-ETH") is False


def test_position_sizer_uses_balance():
    def fetch_balance():
        return 200_000

    config = RiskConfig(max_position_pct=5.0)
    sizer = PositionSizer(balance_fetcher=fetch_balance, config=config)
    assert sizer.krw_stake() == 10_000
