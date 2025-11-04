from upbit_bot.strategies import Candle, MovingAverageCrossoverStrategy, StrategySignal


def build_candle(price: float, ts: int) -> Candle:
    return Candle(timestamp=ts, open=price, high=price + 1, low=price - 1, close=price, volume=1)


def test_ma_crossover_buy_signal():
    strategy = MovingAverageCrossoverStrategy(short_window=3, long_window=5, atr_window=2)
    candles = [build_candle(price, idx) for idx, price in enumerate([1, 1, 1, 1, 1, 1, 3], start=1)]
    signal = strategy.on_candles(candles)
    assert signal is StrategySignal.BUY


def test_ma_crossover_sell_signal():
    strategy = MovingAverageCrossoverStrategy(short_window=3, long_window=5, atr_window=2)
    candles = [build_candle(price, idx) for idx, price in enumerate([3, 3, 3, 3, 3, 3, 1], start=1)]
    signal = strategy.on_candles(candles)
    assert signal is StrategySignal.SELL


def test_ma_crossover_hold_signal_when_insufficient_data():
    strategy = MovingAverageCrossoverStrategy(short_window=3, long_window=5, atr_window=2)
    candles = [build_candle(price, idx) for idx, price in enumerate([1, 2, 3], start=1)]
    signal = strategy.on_candles(candles)
    assert signal is StrategySignal.HOLD
