import csv
from pathlib import Path

import pytest

from backtesting.engine import BacktestingEngine, BacktestResult
from upbit_bot.strategies import (
    BreakoutStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    candles_from_rows,
)

DATA_PATH = Path(__file__).parent / "data" / "test_prices.csv"


def load_rows():
    with DATA_PATH.open() as stream:
        return list(csv.DictReader(stream))


def run_engine(strategy) -> BacktestResult:
    engine = BacktestingEngine(strategy, initial_capital=1_000_000)
    return engine.run(load_rows())


def test_momentum_strategy_generates_single_loss_trade():
    result = run_engine(MomentumStrategy())
    assert result.trades == 1
    assert pytest.approx(result.total_return, rel=1e-6) == -2.1019999999552965e-06
    assert result.win_rate == 0
    assert len(result.equity_curve) == 12


def test_mean_reversion_strategy_handles_pullback_signal():
    result = run_engine(MeanReversionStrategy(window=4, threshold=0.008))
    assert result.trades == 1
    assert pytest.approx(result.total_return, rel=1e-6) == -2.1010000000242144e-06
    assert result.win_rate == 0
    assert result.equity_curve[-1] == pytest.approx(999997.899)


def test_breakout_strategy_stays_flat_when_no_breakout():
    result = run_engine(BreakoutStrategy(lookback=3))
    assert result.trades == 0
    assert result.total_return == 0
    assert all(value == 1_000_000 for value in result.equity_curve)


def test_engine_accepts_candle_objects():
    rows = load_rows()
    candles = candles_from_rows(rows)
    engine = BacktestingEngine(MomentumStrategy())
    result = engine.run(candles)
    assert isinstance(result, BacktestResult)
    assert result.trades >= 0
