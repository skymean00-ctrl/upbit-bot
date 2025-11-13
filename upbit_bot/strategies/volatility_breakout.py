from __future__ import annotations

from collections.abc import Iterable

from upbit_bot.strategies.base import Candle, Strategy, StrategySignal


class VolatilityBreakoutStrategy(Strategy):
    name = "volatility_breakout"

    def __init__(self, k_factor: float = 0.5) -> None:
        self.k_factor = k_factor

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        buffer = list(candles)
        if len(buffer) < 2:
            return StrategySignal.HOLD

        previous_candle = buffer[-2]
        current_candle = buffer[-1]
        price_range = previous_candle.high - previous_candle.low

        buy_target_price = previous_candle.open + price_range * self.k_factor

        if current_candle.close > buy_target_price:
            return StrategySignal.BUY

        return StrategySignal.HOLD
