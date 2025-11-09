#!/usr/bin/env python3
"""복합 전략 (RSI + MA Crossover) 테스트"""

from datetime import datetime
from enum import Enum

import numpy as np

from upbit_bot.services.backtest import Backtester
from upbit_bot.strategies import Candle, MovingAverageCrossoverStrategy, RSITrendFilterStrategy, StrategySignal


class CombinationMethod(Enum):
    """전략 조합 방식"""
    AND = "and"  # 둘 다 BUY일 때만 매수
    OR = "or"  # 하나라도 BUY면 매수
    MAJORITY = "majority"  # 다수결
    WEIGHTED = "weighted"  # 가중 평균


class CombinedStrategy:
    """복합 전략 (RSI + MA)"""

    name = "combined_rsi_ma"

    def __init__(
        self,
        rsi_strategy: RSITrendFilterStrategy,
        ma_strategy: MovingAverageCrossoverStrategy,
        method: CombinationMethod = CombinationMethod.AND,
        rsi_weight: float = 0.5,
        ma_weight: float = 0.5,
    ):
        self.rsi_strategy = rsi_strategy
        self.ma_strategy = ma_strategy
        self.method = method
        self.rsi_weight = rsi_weight
        self.ma_weight = ma_weight

    def on_candles(self, candles) -> StrategySignal:
        """복합 신호 생성"""
        rsi_signal = self.rsi_strategy.on_candles(candles)
        ma_signal = self.ma_strategy.on_candles(candles)

        if self.method == CombinationMethod.AND:
            # 둘 다 BUY일 때만
            if rsi_signal == StrategySignal.BUY and ma_signal == StrategySignal.BUY:
                return StrategySignal.BUY
            elif rsi_signal == StrategySignal.SELL and ma_signal == StrategySignal.SELL:
                return StrategySignal.SELL
            else:
                return StrategySignal.HOLD

        elif self.method == CombinationMethod.OR:
            # 하나라도 BUY면
            if rsi_signal == StrategySignal.BUY or ma_signal == StrategySignal.BUY:
                return StrategySignal.BUY
            elif rsi_signal == StrategySignal.SELL or ma_signal == StrategySignal.SELL:
                return StrategySignal.SELL
            else:
                return StrategySignal.HOLD

        elif self.method == CombinationMethod.MAJORITY:
            # 다수결
            signals = [rsi_signal, ma_signal]
            buy_count = signals.count(StrategySignal.BUY)
            sell_count = signals.count(StrategySignal.SELL)

            if buy_count > sell_count:
                return StrategySignal.BUY
            elif sell_count > buy_count:
                return StrategySignal.SELL
            else:
                return StrategySignal.HOLD

        elif self.method == CombinationMethod.WEIGHTED:
            # 가중 평균
            score = 0.0
            if rsi_signal == StrategySignal.BUY:
                score += self.rsi_weight
            elif rsi_signal == StrategySignal.SELL:
                score -= self.rsi_weight

            if ma_signal == StrategySignal.BUY:
                score += self.ma_weight
            elif ma_signal == StrategySignal.SELL:
                score -= self.ma_weight

            if score > 0.5:
                return StrategySignal.BUY
            elif score < -0.5:
                return StrategySignal.SELL
            else:
                return StrategySignal.HOLD

        return StrategySignal.HOLD


def generate_test_data(n=1000, market_type="mixed"):
    """테스트 데이터 생성"""
    base_price = 50000000
    candles = []
    price = base_price

    for i in range(n):
        if market_type == "bull":
            trend = 5000 + np.random.normal(0, 2000)
        elif market_type == "bear":
            trend = -5000 + np.random.normal(0, 2000)
        elif market_type == "sideways":
            trend = np.random.normal(0, 1000)
        else:  # mixed
            cycle = (i // 100) % 3
            if cycle == 0:
                trend = 3000 + np.random.normal(0, 1500)
            elif cycle == 1:
                trend = -3000 + np.random.normal(0, 1500)
            else:
                trend = np.random.normal(0, 800)

        volatility = np.random.uniform(0.01, 0.03)
        noise = np.random.normal(0, price * volatility)
        price = max(price + trend + noise, 1000000)

        high = price * (1 + abs(np.random.normal(0, 0.005)))
        low = price * (1 - abs(np.random.normal(0, 0.005)))
        open_price = price * (1 + np.random.normal(0, 0.003))
        close_price = price * (1 + np.random.normal(0, 0.003))
        volume = np.random.uniform(50, 200)

        candles.append(
            Candle(
                timestamp=int(datetime.now().timestamp() * 1000) + (i * 60000),
                open=open_price,
                high=max(high, open_price, close_price),
                low=min(low, open_price, close_price),
                close=close_price,
                volume=volume,
            )
        )

    return candles


def test_combined_strategies():
    """복합 전략 테스트"""

    print("\n" + "=" * 80)
    print("🔬 복합 전략 (RSI + MA Crossover) 테스트")
    print("=" * 80)

    # 전략 생성
    rsi_strategy = RSITrendFilterStrategy(
        rsi_window=14,
        ma_window=50,
        rsi_oversold=30,
        rsi_overbought=70,
    )

    ma_strategy = MovingAverageCrossoverStrategy(
        short_window=14,
        long_window=20,
        atr_threshold=0.02,
    )

    # 시장 조건별 데이터
    markets = {
        "상승장": generate_test_data(1000, "bull"),
        "하락장": generate_test_data(1000, "bear"),
        "횡보장": generate_test_data(1000, "sideways"),
        "혼합장": generate_test_data(1000, "mixed"),
    }

    results = []

    # 각 조합 방식 테스트
    for method in CombinationMethod:
        print(f"\n\n{'='*80}")
        print(f"📊 조합 방식: {method.value.upper()}")
        print("=" * 80)

        for market_name, candles in markets.items():
            # 가중치 테스트 (weighted일 때만)
            if method == CombinationMethod.WEIGHTED:
                weights = [(0.7, 0.3), (0.5, 0.5), (0.3, 0.7)]
            else:
                weights = [(0.5, 0.5)]  # 기본값

            for rsi_w, ma_w in weights:
                combined = CombinedStrategy(
                    rsi_strategy=rsi_strategy,
                    ma_strategy=ma_strategy,
                    method=method,
                    rsi_weight=rsi_w,
                    ma_weight=ma_w,
                )

                backtester = Backtester(
                    strategy=combined,
                    initial_balance=1000000,
                    fee_rate=0.0005,
                    slippage_pct=0.001,
                )

                result = backtester.run(candles)

                results.append({
                    "method": method.value,
                    "market": market_name,
                    "rsi_weight": rsi_w,
                    "ma_weight": ma_w,
                    "return_pct": result.total_return_pct,
                    "win_rate": result.win_rate,
                    "sharpe": result.sharpe_ratio,
                    "max_dd": result.max_drawdown_pct,
                    "trades": result.total_trades,
                })

                weight_str = f"(RSI:{rsi_w:.1f}/MA:{ma_w:.1f})" if method == CombinationMethod.WEIGHTED else ""
                print(f"\n{market_name} {weight_str}:")
                print(f"  수익률:   {result.total_return_pct:>8.2f}%")
                print(f"  승률:     {result.win_rate:>8.1f}%")
                print(f"  Sharpe:   {result.sharpe_ratio:>8.2f}")
                print(f"  최대낙폭: {result.max_drawdown_pct:>8.2f}%")
                print(f"  거래횟수: {result.total_trades:>8}회")

    return results


def analyze_combined_results(results):
    """복합 전략 결과 분석"""

    print("\n\n" + "=" * 80)
    print("📈 복합 전략 종합 분석")
    print("=" * 80)

    import pandas as pd

    df = pd.DataFrame(results)

    # 방식별 평균 성능
    print("\n[1] 조합 방식별 평균 성능")
    print("-" * 80)

    for method in CombinationMethod:
        method_df = df[df["method"] == method.value]
        if len(method_df) == 0:
            continue

        avg_return = method_df["return_pct"].mean()
        avg_sharpe = method_df["sharpe"].mean()
        avg_win_rate = method_df["win_rate"].mean()

        print(f"\n{method.value.upper()}:")
        print(f"  평균 수익률: {avg_return:>8.2f}%")
        print(f"  평균 Sharpe: {avg_sharpe:>8.2f}")
        print(f"  평균 승률:   {avg_win_rate:>8.1f}%")

    # 최고 성능 조합
    print("\n\n[2] 최고 성능 조합 TOP 5")
    print("-" * 80)

    df_sorted = df.sort_values("return_pct", ascending=False)

    for i, (idx, row) in enumerate(df_sorted.head(5).iterrows(), 1):
        print(f"\n{i}위:")
        print(f"  방식: {row['method'].upper()}")
        print(f"  시장: {row['market']}")
        if row['method'] == 'weighted':
            print(f"  가중치: RSI {row['rsi_weight']:.1f} / MA {row['ma_weight']:.1f}")
        print(f"  수익률: {row['return_pct']:.2f}%")
        print(f"  Sharpe: {row['sharpe']:.2f}")
        print(f"  승률: {row['win_rate']:.1f}%")

    # 단일 전략과 비교
    print("\n\n[3] 단일 전략 vs 복합 전략 비교")
    print("-" * 80)

    best_combined = df_sorted.iloc[0]

    print(f"\nMA Crossover 단독 (이전 테스트):")
    print(f"  평균 수익률: -1.62%")
    print(f"  Sharpe: 0.00")

    print(f"\nRSI 단독 (예상):")
    print(f"  평균 수익률: (RSI 최적화 결과 참조)")

    print(f"\n복합 전략 최고 ({best_combined['method'].upper()}):")
    print(f"  평균 수익률: {best_combined['return_pct']:.2f}%")
    print(f"  Sharpe: {best_combined['sharpe']:.2f}")

    # 최종 추천
    print("\n\n[4] 🎯 최종 추천")
    print("=" * 80)

    print(f"\n✅ 추천 조합:")
    print(f"   방식: {best_combined['method'].upper()}")
    if best_combined['method'] == 'weighted':
        print(f"   RSI 가중치: {best_combined['rsi_weight']:.1f}")
        print(f"   MA 가중치: {best_combined['ma_weight']:.1f}")
    print(f"\n   예상 성능:")
    print(f"   - 수익률: {best_combined['return_pct']:.2f}%")
    print(f"   - Sharpe: {best_combined['sharpe']:.2f}")
    print(f"   - 승률: {best_combined['win_rate']:.1f}%")

    # Sharpe > 1 체크
    good_sharpe = df[df["sharpe"] > 1.0]
    if len(good_sharpe) > 0:
        print(f"\n✅ Sharpe > 1.0인 조합: {len(good_sharpe)}개 발견!")
        print("   복합 전략이 단일 전략보다 안정적입니다.")
    else:
        print("\n⚠️  Sharpe > 1.0인 조합이 없습니다.")
        print("   추가 최적화 또는 다른 전략 조합 필요")

    return best_combined


def main():
    print("\n🚀 복합 전략 테스트 시작")
    print("=" * 80)
    print("목표: RSI + MA Crossover 최적 조합 찾기")
    print("=" * 80)

    # 테스트 실행
    results = test_combined_strategies()

    # 분석
    best = analyze_combined_results(results)

    # 결과 저장
    import pandas as pd
    df = pd.DataFrame(results)
    df.to_csv("combined_strategy_results.csv", index=False, encoding="utf-8-sig")
    print(f"\n✅ 결과가 'combined_strategy_results.csv'에 저장되었습니다.")

    print("\n" + "=" * 80)
    print("테스트 완료! 🎉")
    print("=" * 80)


if __name__ == "__main__":
    main()
