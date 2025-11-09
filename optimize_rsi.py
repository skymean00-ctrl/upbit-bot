#!/usr/bin/env python3
"""RSI Trend Filter 전략 파라미터 Grid Search 최적화"""

import itertools
from datetime import datetime

import numpy as np
import pandas as pd

from upbit_bot.services.backtest import Backtester
from upbit_bot.strategies import Candle, RSITrendFilterStrategy


def generate_realistic_market_data(n=1000, market_type="mixed"):
    """현실적인 시장 데이터 생성"""
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


def run_rsi_grid_search():
    """RSI 전략 Grid Search"""

    print("=" * 80)
    print("📊 RSI Trend Filter 전략 파라미터 최적화")
    print("=" * 80)

    # 테스트할 파라미터 범위
    rsi_windows = [7, 10, 14, 20]
    ma_windows = [20, 30, 50, 100]
    rsi_oversolds = [20, 25, 30, 35]
    rsi_overboughts = [65, 70, 75, 80]

    # 시장 조건별 테스트 데이터
    market_types = {
        "상승장": generate_realistic_market_data(1000, "bull"),
        "하락장": generate_realistic_market_data(1000, "bear"),
        "횡보장": generate_realistic_market_data(1000, "sideways"),
        "혼합장": generate_realistic_market_data(1000, "mixed"),
    }

    results = []
    total_tests = len(rsi_windows) * len(ma_windows) * len(rsi_oversolds) * len(rsi_overboughts) * len(market_types)
    test_count = 0

    print(f"\n총 {total_tests:,}개 조합 테스트 시작...\n")

    for market_name, candles in market_types.items():
        for rsi_w, ma_w, oversold, overbought in itertools.product(
            rsi_windows, ma_windows, rsi_oversolds, rsi_overboughts
        ):
            test_count += 1

            # oversold >= overbought 인 경우 스킵
            if oversold >= overbought:
                continue

            try:
                strategy = RSITrendFilterStrategy(
                    rsi_window=rsi_w,
                    ma_window=ma_w,
                    rsi_oversold=oversold,
                    rsi_overbought=overbought,
                )

                backtester = Backtester(
                    strategy=strategy,
                    initial_balance=1000000,
                    fee_rate=0.0005,
                    slippage_pct=0.001,
                )
                result = backtester.run(candles)

                results.append({
                    "market": market_name,
                    "rsi_window": rsi_w,
                    "ma_window": ma_w,
                    "rsi_oversold": oversold,
                    "rsi_overbought": overbought,
                    "return_pct": result.total_return_pct,
                    "win_rate": result.win_rate,
                    "sharpe": result.sharpe_ratio,
                    "max_dd": result.max_drawdown_pct,
                    "trades": result.total_trades,
                    "avg_win": result.avg_win_pct,
                    "avg_loss": result.avg_loss_pct,
                })

                if test_count % 100 == 0:
                    print(f"진행률: {test_count}/{total_tests} ({test_count/total_tests*100:.1f}%)")

            except Exception as e:
                print(f"오류 발생 - {market_name} (RSI:{rsi_w}/MA:{ma_w}/{oversold}/{overbought}): {e}")
                continue

    return pd.DataFrame(results)


def analyze_rsi_results(df):
    """RSI 결과 분석"""

    print("\n" + "=" * 80)
    print("📈 RSI 전략 분석 결과")
    print("=" * 80)

    # 거래 발생한 경우만 필터링
    df_traded = df[df["trades"] > 0].copy()

    print(f"\n총 테스트: {len(df)}개")
    print(f"거래 발생: {len(df_traded)}개 ({len(df_traded)/len(df)*100:.1f}%)")

    if len(df_traded) == 0:
        print("\n⚠️ 거래가 발생한 케이스가 없습니다!")
        return None, None, None

    # 평균 성능
    avg_perf = df_traded.groupby(["rsi_window", "ma_window", "rsi_oversold", "rsi_overbought"]).agg({
        "return_pct": "mean",
        "win_rate": "mean",
        "sharpe": "mean",
        "max_dd": "mean",
        "trades": "mean",
    }).reset_index()

    # 종합 점수
    avg_perf["score"] = (
        avg_perf["return_pct"] * 0.4 +
        avg_perf["sharpe"] * 10 * 0.3 +
        avg_perf["win_rate"] * 0.2 -
        avg_perf["max_dd"] * 0.1
    )

    # TOP 10
    print("\n[1] 종합 성능 TOP 10")
    print("-" * 80)

    top10 = avg_perf.nlargest(10, "score")

    for i, (idx, row) in enumerate(top10.iterrows(), 1):
        print(f"\n{i}위:")
        print(f"  파라미터: RSI={int(row['rsi_window'])}, MA={int(row['ma_window'])}, "
              f"Oversold={int(row['rsi_oversold'])}, Overbought={int(row['rsi_overbought'])}")
        print(f"  평균 수익률: {row['return_pct']:>8.2f}%")
        print(f"  평균 승률:   {row['win_rate']:>8.1f}%")
        print(f"  Sharpe:      {row['sharpe']:>8.2f}")
        print(f"  최대 낙폭:   {row['max_dd']:>8.2f}%")
        print(f"  평균 거래:   {row['trades']:>8.1f}회")
        print(f"  종합 점수:   {row['score']:>8.2f}")

    # 시장별 최고 성능
    print("\n\n[2] 시장별 최고 수익률")
    print("-" * 80)

    for market in df_traded["market"].unique():
        market_df = df_traded[df_traded["market"] == market]
        best = market_df.nlargest(1, "return_pct").iloc[0]

        print(f"\n📈 {market}:")
        print(f"  파라미터: RSI={int(best['rsi_window'])}, MA={int(best['ma_window'])}, "
              f"Oversold={int(best['rsi_oversold'])}, Overbought={int(best['rsi_overbought'])}")
        print(f"  수익률:   {best['return_pct']:>8.2f}%")
        print(f"  승률:     {best['win_rate']:>8.1f}%")
        print(f"  Sharpe:   {best['sharpe']:>8.2f}")
        print(f"  낙폭:     {best['max_dd']:>8.2f}%")
        print(f"  거래:     {int(best['trades'])}회")

    # Sharpe > 1.0
    high_sharpe = avg_perf[avg_perf["sharpe"] > 1.0]

    print("\n\n[3] Sharpe Ratio > 1.0 (안정적 수익)")
    print("-" * 80)

    if len(high_sharpe) > 0:
        for i, (idx, row) in enumerate(high_sharpe.nlargest(5, "sharpe").iterrows(), 1):
            print(f"\n{i}위:")
            print(f"  파라미터: RSI={int(row['rsi_window'])}, MA={int(row['ma_window'])}, "
                  f"Oversold={int(row['rsi_oversold'])}, Overbought={int(row['rsi_overbought'])}")
            print(f"  Sharpe:      {row['sharpe']:>8.2f}")
            print(f"  평균 수익률: {row['return_pct']:>8.2f}%")
            print(f"  평균 승률:   {row['win_rate']:>8.1f}%")
    else:
        print("⚠️ Sharpe Ratio > 1.0인 파라미터가 없습니다.")

    # 최종 추천
    print("\n\n[4] 🎯 최종 추천")
    print("=" * 80)

    best = top10.iloc[0]
    print(f"\n✅ RSI 전략 1순위:")
    print(f"   rsi_window={int(best['rsi_window'])}")
    print(f"   ma_window={int(best['ma_window'])}")
    print(f"   rsi_oversold={int(best['rsi_oversold'])}")
    print(f"   rsi_overbought={int(best['rsi_overbought'])}")
    print(f"\n   예상 성능:")
    print(f"   - 평균 수익률: {best['return_pct']:.2f}%")
    print(f"   - 평균 승률: {best['win_rate']:.1f}%")
    print(f"   - Sharpe Ratio: {best['sharpe']:.2f}")
    print(f"   - 최대 낙폭: {best['max_dd']:.2f}%")

    # MA Crossover와 비교
    print("\n\n[5] 📊 MA Crossover vs RSI 비교")
    print("=" * 80)

    print(f"\nMA Crossover 최고 성능:")
    print(f"  - 평균 수익률: -1.62%")
    print(f"  - Sharpe: 0.00")

    print(f"\nRSI Trend Filter 최고 성능:")
    print(f"  - 평균 수익률: {best['return_pct']:.2f}%")
    print(f"  - Sharpe: {best['sharpe']:.2f}")

    improvement = best['return_pct'] - (-1.62)
    print(f"\n개선도: {improvement:+.2f}%p")

    return best, avg_perf, high_sharpe


def main():
    print("\n🚀 RSI Trend Filter 전략 최적화 시작")
    print("=" * 80)

    # Grid Search 실행
    results_df = run_rsi_grid_search()

    # 결과 저장
    results_df.to_csv("rsi_optimization_results.csv", index=False, encoding="utf-8-sig")
    print(f"\n✅ 결과가 'rsi_optimization_results.csv'에 저장되었습니다.")

    # 분석
    best, avg_perf, high_sharpe = analyze_rsi_results(results_df)

    if best is not None:
        print("\n" + "=" * 80)
        print("💡 적용 방법")
        print("=" * 80)
        print("\n.env 파일에 다음과 같이 설정:")
        print(f"\nSTRATEGY_NAME=rsi_trend_filter")
        print(f"RSI_WINDOW={int(best['rsi_window'])}")
        print(f"RSI_MA_WINDOW={int(best['ma_window'])}")
        print(f"RSI_OVERSOLD={int(best['rsi_oversold'])}")
        print(f"RSI_OVERBOUGHT={int(best['rsi_overbought'])}")

    print("\n" + "=" * 80)
    print("최적화 완료! 🎉")
    print("=" * 80)


if __name__ == "__main__":
    main()
