from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
from pydantic import Field
import numpy as np # 볼린저 밴드 계산을 위해 numpy 추가

from upbit_bot.strategies.base import BaseStrategy, StrategySignal

if TYPE_CHECKING:
    from upbit_bot.strategies import Candle


class MixedBBRSIMAStrategy(BaseStrategy):
    """
    RSI, 이동평균선, 볼린저 밴드를 결합한 매매 전략.
    RSI 과매수/과매도 신호를 MA 추세와 볼린저 밴드 위치로 필터링합니다.
    """

    rsi_window: int = Field(14, description="RSI 계산에 사용할 기간 (window)")
    ma_window: int = Field(50, description="이동평균선 계산에 사용할 기간 (window)")
    bb_window: int = Field(20, description="볼린저 밴드 계산에 사용할 기간 (window)")
    bb_num_std_dev: float = Field(2.0, description="볼린저 밴드 표준편차 배수")

    # RSI 과매수/과매도 기준
    rsi_overbought: int = Field(70, description="RSI 과매수 기준")
    rsi_oversold: int = Field(30, description="RSI 과매도 기준")

    def generate_signal(self, candles: list[Candle]) -> StrategySignal:
        if len(candles) < max(self.rsi_window, self.ma_window, self.bb_window):
            return StrategySignal.HOLD

        df = pd.DataFrame([c.__dict__ for c in candles])
        # pandas_ta 호환을 위해 close_price 컬럼 추가 (필요시)
        # df["close_price"] = df["close"] 

        # 이동평균선 (MA) 계산
        df["ma"] = df["close"].rolling(window=self.ma_window, min_periods=1).mean()

        # RSI 계산
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_window, min_periods=1).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_window, min_periods=1).mean()
        # 0으로 나누는 오류 방지
        rs = np.where(loss == 0, np.inf, gain / loss) 
        df["rsi"] = 100 - (100 / (1 + rs))

        # 볼린저 밴드 (BB) 계산
        df["bb_middle"] = df["close"].rolling(window=self.bb_window, min_periods=1).mean()
        df["bb_std"] = df["close"].rolling(window=self.bb_window, min_periods=1).std()
        df["bb_upper"] = df["bb_middle"] + (df["bb_std"] * self.bb_num_std_dev)
        df["bb_lower"] = df["bb_middle"] - (df["bb_std"] * self.bb_num_std_dev)

        # 최신 데이터 추출
        last_row = df.iloc[-1]
        previous_row = df.iloc[-2] if len(df) > 1 else None

        current_close = last_row["close"]
        current_ma = last_row["ma"]
        current_rsi = last_row["rsi"]
        current_bb_upper = last_row["bb_upper"]
        current_bb_lower = last_row["bb_lower"]

        if previous_row is None:
            return StrategySignal.HOLD

        previous_ma = previous_row["ma"]

        # MA 추세 확인
        is_ma_uptrend = current_ma > previous_ma
        is_ma_downtrend = current_ma < previous_ma

        # 매수 신호 조건
        buy_signal = False
        if (current_rsi < self.rsi_oversold and is_ma_uptrend and current_close < current_bb_lower):
            buy_signal = True

        # 매도 신호 조건
        sell_signal = False
        if (current_rsi > self.rsi_overbought and is_ma_downtrend and current_close > current_bb_upper):
            sell_signal = True

        if buy_signal:
            return StrategySignal.BUY
        elif sell_signal:
            return StrategySignal.SELL
        else:
            return StrategySignal.HOLD

