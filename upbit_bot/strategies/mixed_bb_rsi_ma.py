from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from upbit_bot.strategies.base import Candle, Strategy, StrategySignal


class MixedBBRSIMAStrategy(Strategy):
    name = "mixed_bb_rsi_ma"

    def __init__(
        self,
        rsi_window: int = 14,
        ma_window: int = 50,
        bb_window: int = 20,
        bb_num_std_dev: float = 2.0,
        rsi_overbought: int = 70,
        rsi_oversold: int = 30,
    ) -> None:
        self.rsi_window = rsi_window
        self.ma_window = ma_window
        self.bb_window = bb_window
        self.bb_num_std_dev = bb_num_std_dev
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        buffer = list(candles)
        min_length = max(self.rsi_window, self.ma_window, self.bb_window) + 1
        if len(buffer) < min_length:
            return StrategySignal.HOLD

        df = pd.DataFrame([c.__dict__ for c in buffer])
        closes = df["close"]

        ma = closes.rolling(window=self.ma_window, min_periods=1).mean()
        std = closes.rolling(window=self.bb_window, min_periods=1).std().fillna(0.0)
        upper_band = ma + std * self.bb_num_std_dev
        lower_band = ma - std * self.bb_num_std_dev

        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(window=self.rsi_window, min_periods=1).mean()
        loss = (-delta.clip(upper=0)).rolling(window=self.rsi_window, min_periods=1).mean()
        rs = gain / (loss.replace(0, np.nan))
        rsi = 100 - (100 / (1 + rs.fillna(0)))

        current_close = closes.iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_ma = ma.iloc[-1]
        previous_ma = ma.iloc[-2]
        current_upper = upper_band.iloc[-1]
        current_lower = lower_band.iloc[-1]

        is_uptrend = current_ma > previous_ma
        is_downtrend = current_ma < previous_ma

        if (
            current_rsi < self.rsi_oversold
            and is_uptrend
            and current_close < current_lower
        ):
            return StrategySignal.BUY

        if (
            current_rsi > self.rsi_overbought
            and is_downtrend
            and current_close > current_upper
        ):
            return StrategySignal.SELL

        return StrategySignal.HOLD
