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
from upbit_bot.strategies import Candle, get_strategy
from upbit_bot.utils.logging import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest a strategy using OHLCV data in CSV format.",
    )
    parser.add_argument(
        "csv",
        help="Path to CSV file with columns: timestamp, open, high, low, close, volume.",
    )
    parser.add_argument("--env-file", help="Path to .env file.")
    parser.add_argument(
        "--short-window",
        type=int,
        default=14,
        help="Short moving average window.",
    )
    parser.add_argument(
        "--long-window",
        type=int,
        default=37,
        help="Long moving average window.",
    )
    parser.add_argument(
        "--atr-threshold",
        type=float,
        default=0.0,
        help="ATR filter threshold.",
    )
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=0.0005,
        help="Trading fee rate (default 0.05%).",
    )
    parser.add_argument("--output", help="Optional output JSON file for results.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def load_candles(path: str) -> list[Candle]:
    df = pd.read_csv(path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise ValueError(f"CSV missing required columns: {missing}")
    return [
        Candle(
            timestamp=int(row["timestamp"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
        for row in df.to_dict("records")
    ]


def main() -> int:
    args = parse_args()
    configure_logging(level=args.log_level)
    settings = load_settings(env_path=args.env_file)

    # 기본 전략을 rsi_trend_filter로 설정합니다.
    # TODO: 추후 --strategy 인자를 추가하여 동적으로 전략을 선택할 수 있도록 개선 필요
    strategy = get_strategy("rsi_trend_filter")
    strategy = get_strategy(
        settings.strategy,
        short_window=args.short_window,
        long_window=args.long_window,
        atr_threshold=args.atr_threshold,
    )
    backtester = Backtester(strategy=strategy, fee_rate=args.fee_rate)
    candles = load_candles(args.csv)
    result = backtester.run(candles)

    logging.info(
        "Total return: %.2f%% | Win rate: %.2f%% | Max DD: %.2f%% | Trades: %d",
        result.total_return_pct,
        result.win_rate,
        result.max_drawdown_pct,
        len(result.trades),
    )

    if args.output:
        payload = {
            "total_return_pct": result.total_return_pct,
            "win_rate": result.win_rate,
            "max_drawdown_pct": result.max_drawdown_pct,
            "trades": [trade.__dict__ for trade in result.trades],
        }
        Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
