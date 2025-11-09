from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from upbit_bot.strategies.base import Candle, Strategy, StrategySignal


class RSITrendFilterStrategy(Strategy):
    """
    RSI와 이동평균선을 결합한 매매 전략.
    RSI 과매수/과매도 신호를 이동평균선 추세로 필터링합니다.
    """

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
        if len(buffer) < max(self.ma_window, self.rsi_window):
            return StrategySignal.HOLD  # 충분한 데이터가 없으면 신호 없음

        # Candle dataclass를 dict로 변환
        data = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in buffer
        ]
        df = pd.DataFrame(data)

        # RSI 계산
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).fillna(0)
        loss = (-delta.where(delta < 0, 0)).fillna(0)

        avg_gain = gain.rolling(window=self.rsi_window, min_periods=1).mean()
        avg_loss = loss.rolling(window=self.rsi_window, min_periods=1).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        df["rsi"] = rsi

        # 이동평균선 계산
        df["ma"] = df["close"].rolling(window=self.ma_window, min_periods=1).mean()

        current_rsi = df["rsi"].iloc[-1]
        previous_rsi = df["rsi"].iloc[-2] if len(df) > 1 else None
        current_close = df["close"].iloc[-1]
        current_ma = df["ma"].iloc[-1]

        # 매수 조건:
        # 1. RSI가 과매도 구간(rsi_oversold) 아래에 있다가 위로 상승
        # 2. 현재 가격이 이동평균선 위에 있음 (상승 추세 필터링)
        if (
            previous_rsi is not None
            and previous_rsi <= self.rsi_oversold
            and current_rsi > self.rsi_oversold
            and current_close > current_ma
        ):
            return StrategySignal.BUY

        # 매도 조건:
        # 1. RSI가 과매수 구간(rsi_overbought) 위에 있다가 아래로 하락
        # 2. 현재 가격이 이동평균선 아래에 있음 (하락 추세 필터링)
        if (
            previous_rsi is not None
            and previous_rsi >= self.rsi_overbought
            and current_rsi < self.rsi_overbought
            and current_close < current_ma
        ):
            return StrategySignal.SELL

        return StrategySignal.HOLD  # 매매 신호 없음

