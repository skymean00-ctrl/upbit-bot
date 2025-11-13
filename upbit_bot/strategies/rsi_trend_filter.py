from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from upbit_bot.strategies.base import Candle, Strategy, StrategySignal


class RSITrendFilterStrategy(Strategy):
    name = "rsi_trend_filter"

    def __init__(
        self,
        rsi_window: int = 14,
        ma_window: int = 50,
        rsi_oversold: int = 30,
        rsi_overbought: int = 70,
    ) -> None:
        self.rsi_window = rsi_window
        self.ma_window = ma_window
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        buffer = list(candles)
        required = max(self.rsi_window, self.ma_window) + 1
        if len(buffer) < required:
            return StrategySignal.HOLD

        df = pd.DataFrame([c.__dict__ for c in buffer])
        closes = df["close"]

        ma = closes.rolling(window=self.ma_window, min_periods=1).mean()

        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(window=self.rsi_window, min_periods=1).mean()
        loss = (-delta.clip(upper=0)).rolling(window=self.rsi_window, min_periods=1).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs.fillna(0)))

        previous_rsi = rsi.iloc[-2]
        current_rsi = rsi.iloc[-1]
        current_close = closes.iloc[-1]
        previous_ma = ma.iloc[-2]
        current_ma = ma.iloc[-1]

        if (
            previous_rsi <= self.rsi_oversold
            and current_rsi > self.rsi_oversold
            and current_close > current_ma
        ):
            return StrategySignal.BUY

        if (
            previous_rsi >= self.rsi_overbought
            and current_rsi < self.rsi_overbought
            and current_close < current_ma
        ):
            return StrategySignal.SELL

        return StrategySignal.HOLD
