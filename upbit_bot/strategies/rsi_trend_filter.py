from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
from pydantic import Field

from upbit_bot.strategies.base import BaseStrategy, StrategySignal

if TYPE_CHECKING:
    from upbit_bot.strategies import Candle


class RSITrendFilterStrategy(BaseStrategy):
    """
    RSI와 이동평균선을 결합한 매매 전략.
    RSI 과매수/과매도 신호를 이동평균선 추세로 필터링합니다.
    """

    rsi_window: int = Field(14, description="RSI 계산에 사용할 기간 (window)")
    ma_window: int = Field(50, description="이동평균선 계산에 사용할 기간 (window)"ption="이동평균선 계산에 사용할 기간 (window)")
    bb_window: int = Field(20, description="볼린저 밴드 계산에 사용할 기간 (window)")
    bb_num_std_dev: float = Field(2.0, description="볼린저 밴드 표준편차 배수")
    bb_window: int = Field(20, description="볼린저 밴드 계산에 사용할 기간 (window)")
    bb_num_std_dev: float = Field(2.0, description="볼린저 밴드 표준편차 배수")ption="이동평균선 계산에 사용할 기간 (window)")ption="이동평균선 계산에 사용할 기간 (window)")ption="이동평균선 계산에 사용할 기간 (window)")
    bb_window: int = Field(20, description="볼린저 밴드 계산에 사용할 기간 (window)")
    bb_num_std_dev: float = Field(2.0, description="볼린저 밴드 표준편차 배수")ption="이동평균선 계산에 사용할 기간 (window)")
    rsi_oversold: int = Field(30, description="RSI 과매도 기준")
    rsi_overbought: int = Field(70, description="RSI 과매수 기준")

    def get_signal(self, candles: list[Candle]) -> StrategySignal | None:
        df = pd.DataFrame([c.model_dump() for c in candles])
        df[close] = pd.to_numeric(df[trade_price])

        # RSI 계산
        delta = df[close].diff()
        gain = (delta.where(delta > 0, 0)).fillna(0)
        loss = (-delta.where(delta < 0, 0)).fillna(0)

        avg_gain = gain.rolling(window=self.rsi_window, min_periods=1).mean()
        avg_loss = loss.rolling(window=self.rsi_window, min_periods=1).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        df[rsi] = rsi

        # 이동평균선 계산
        df[ma] = df[close].rolling(window=self.ma_window, min_periods=1).mean()

        if len(df) < self.ma_window or len(df) < self.rsi_window:
            return None # 충분한 데이터가 없으면 신호 없음

        current_rsi = df[rsi].iloc[-1]
        previous_rsi = df[rsi].iloc[-2] if len(df) > 1 else None
        current_close = df[close].iloc[-1]
        current_ma = df[ma].iloc[-1]

        # 매수 조건:
        # 1. RSI가 과매도 구간(rsi_oversold) 아래에 있다가 위로 상승
        # 2. 현재 가격이 이동평균선 위에 있음 (상승 추세 필터링)
        if previous_rsi is not None and \
           previous_rsi <= self.rsi_oversold and current_rsi > self.rsi_oversold and \
           current_close > current_ma:
            return StrategySignal.BUY

        # 매도 조건:
        # 1. RSI가 과매수 구간(rsi_overbought) 위에 있다가 아래로 하락
        # 2. 현재 가격이 이동평균선 아래에 있음 (하락 추세 필터링)
        if previous_rsi is not None and \
           previous_rsi >= self.rsi_overbought and current_rsi < self.rsi_overbought and \
           current_close < current_ma:
            return StrategySignal.SELL

        return None # 매매 신호 없음

