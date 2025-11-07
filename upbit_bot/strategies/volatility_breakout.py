from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
from pydantic import Field

from upbit_bot.strategies.base import BaseStrategy, StrategySignal

if TYPE_CHECKING:
    from upbit_bot.strategies import Candle


class VolatilityBreakoutStrategy(BaseStrategy):
    """
    이전 캔들의 변동폭을 기준으로 돌파를 감지하는 매매 전략.
    """

    k_factor: float = Field(0.5, description="변동폭에 곱할 K 값 (0.0 ~ 1.0)")
    
    name = "volatility_breakout"

    def determine_signal(self, candles: list[Candle]) -> StrategySignal:
        if len(candles) < 2:
            return StrategySignal.HOLD  # 최소 2개의 캔들이 필요 (현재, 이전)

        current_candle = candles[-1]
        previous_candle = candles[-2]

        # 이전 캔들의 변동폭 (고가 - 저가)
        price_range = previous_candle.high - previous_candle.low

        # 매수 기준 가격
        buy_target_price = previous_candle.open + (price_range * self.k_factor)

        # 매도 기준 가격 (예시: 여기서는 매수 전략에 초점)
        # sell_target_price = previous_candle.open - (price_range * self.k_factor)

        if current_candle.close > buy_target_price:
            return StrategySignal.BUY
        # elif current_candle.close < sell_target_price:
        #     return StrategySignal.SELL
        else:
            return StrategySignal.HOLD

