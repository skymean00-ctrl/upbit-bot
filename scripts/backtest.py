#!/usr/bin/env python3
"""Run a simple backtest for a configured strategy."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from upbit_bot.config import load_settings
from upbit_bot.services.backtest import Backtester
from upbit_bot.strategies import Candle, CombinedStrategy, MovingAverageCrossoverStrategy, MixedBBRSIMAStrategy, RSITrendFilterStrategy, VolatilityBreakoutStrategy, BaseStrategy, StrategySignal
from upbit_bot.utils.logging import configure_logging

logger = logging.getLogger(__name__)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a simple backtest.")
    parser.add_argument(
        "--data-path",
        type=str,
        required=True,
        help="Path to the CSV file containing historical candle data.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="backtest_results.json",
        help="Path to save the backtest results.",
    )
    parser.add_argument(
        "--verbose", "-v", action="count", default=0, help="Increase verbosity (e.g., -v, -vv)"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    configure_logging(args.verbose)

    logger.info("백테스트 시작...")

    settings = load_settings()

    # === 전략 로딩 부분 시작 ===
    sub_strategies_configs = settings.get("combined_strategies", [])
    if not sub_strategies_configs:
        # 기존 단일 전략 로직 (호환성을 위해 유지)
        strategy_name = settings.strategy.name
        strategy_params = settings.strategy.config
        
        if strategy_name == "ma_crossover":
            strategy: BaseStrategy = MovingAverageCrossoverStrategy(**strategy_params)
        elif strategy_name == "mixed_bb_rsi_ma":
            strategy = MixedBBRSIMAStrategy(**strategy_params)
        elif strategy_name == "rsi_trend_filter":
            strategy = RSITrendFilterStrategy(**strategy_params)
        elif strategy_name == "volatility_breakout":
            strategy = VolatilityBreakoutStrategy(**strategy_params)
        else:
            logger.error(f"알 수 없는 단일 전략: {strategy_name}")
            return
        logger.info(f"단일 전략 \"{strategy_name}\" 사용.")
    else:
        sub_strategies: list[BaseStrategy] = []
        for strat_config in sub_strategies_configs:
            strategy_name = strat_config.get("name")
            strategy_params = strat_config.get("config", {})
            
            if strategy_name == "ma_crossover":
                sub_strategies.append(MovingAverageCrossoverStrategy(**strategy_params))
            elif strategy_name == "mixed_bb_rsi_ma":
                sub_strategies.append(MixedBBRSIMAStrategy(**strategy_params))
            elif strategy_name == "rsi_trend_filter":
                sub_strategies.append(RSITrendFilterStrategy(**strategy_params))
            elif strategy_name == "volatility_breakout":
                sub_strategies.append(VolatilityBreakoutStrategy(**strategy_params))
            else:
                logger.warning(f"알 수 없는 하위 전략: {strategy_name}. 이 전략은 무시됩니다.")
        
        if not sub_strategies:
            logger.error("CombinedStrategy에 추가할 유효한 하위 전략이 없습니다. 봇을 종료합니다.")
            return
        
        strategy = CombinedStrategy(sub_strategies=sub_strategies)
        logger.info(f"복합 전략 \"{strategy.name}\" 사용. 하위 전략: {[s.name for s in sub_strategies]}")
    # === 전략 로딩 부분 끝 ===

    # 백테스트 데이터 로드
    data_path = Path(args.data_path)
    if not data_path.exists():
        logger.error(f"데이터 파일이 없습니다: {data_path}")
        return

    df = pd.read_csv(data_path, parse_dates=["timestamp"], index_col="timestamp")
    candles = [Candle(**row.to_dict()) for _, row in df.iterrows()]

    # settings.backtest.initial_balance 가 정의되어 있지 않을 수 있으므로, 기본값 제공
    initial_balance = getattr(settings, "backtest", {}).get("initial_balance", 1000000) # 기본값 100만원

    backtester = Backtester(strategy, initial_balance=initial_balance)
    results = backtester.run(candles)

    # 결과 저장
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results.model_dump(), f, indent=4)

    logger.info(f"백테스트 결과가 {output_path}에 저장되었습니다.")


if __name__ == "__main__":
    main()

