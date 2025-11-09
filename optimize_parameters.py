#!/usr/bin/env python3
"""MA Crossover 전략 파라미터 Grid Search 최적화"""

import itertools
from datetime import datetime

import numpy as np
import pandas as pd

from upbit_bot.services.backtest import Backtester
from upbit_bot.strategies import Candle, MovingAverageCrossoverStrategy


def generate_realistic_market_data(n=1000, market_type="mixed"):
    """
    더 현실적인 시장 데이터 생성

    Args:
        n: 캔들 개수
        market_type: "bull" (상승장), "bear" (하락장), "sideways" (횡보), "mixed" (혼합)
    """
    base_price = 50000000  # 5천만원
    candles = []

    price = base_price

    for i in range(n):
        # 시장 유형별 트렌드
        if market_type == "bull":
            trend = 5000 + np.random.normal(0, 2000)
        elif market_type == "bear":
            trend = -5000 + np.random.normal(0, 2000)
        elif market_type == "sideways":
            trend = np.random.normal(0, 1000)
        else:  # mixed
            # 주기적으로 트렌드 변경
            cycle = (i // 100) % 3
            if cycle == 0:
                trend = 3000 + np.random.normal(0, 1500)  # 상승
            elif cycle == 1:
                trend = -3000 + np.random.normal(0, 1500)  # 하락
            else:
                trend = np.random.normal(0, 800)  # 횡보

        # 가격 업데이트 (일일 변동성 1-3%)
        volatility = np.random.uniform(0.01, 0.03)
        noise = np.random.normal(0, price * volatility)
        price = max(price + trend + noise, 1000000)  # 최소 100만원

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


def run_grid_search():
    """MA Crossover 파라미터 Grid Search"""

    print("=" * 80)
    print("📊 MA Crossover 전략 파라미터 최적화 (Grid Search)")
    print("=" * 80)

    # 테스트할 파라미터 범위
    short_windows = [5, 7, 10, 14, 20]
    long_windows = [20, 30, 37, 50, 100]
    atr_thresholds = [0.0, 0.01, 0.02, 0.03]

    # 시장 조건별 테스트 데이터
    market_types = {
        "상승장": generate_realistic_market_data(1000, "bull"),
        "하락장": generate_realistic_market_data(1000, "bear"),
        "횡보장": generate_realistic_market_data(1000, "sideways"),
        "혼합장": generate_realistic_market_data(1000, "mixed"),
    }

    results = []
    total_tests = len(short_windows) * len(long_windows) * len(atr_thresholds) * len(market_types)
    test_count = 0

    print(f"\n총 {total_tests:,}개 조합 테스트 시작...\n")

    for market_name, candles in market_types.items():
        for short, long, atr in itertools.product(short_windows, long_windows, atr_thresholds):
            test_count += 1

            # short >= long 인 경우 스킵
            if short >= long:
                continue

            try:
                # 전략 생성
                strategy = MovingAverageCrossoverStrategy(
                    short_window=short,
                    long_window=long,
                    atr_threshold=atr,
                )

                # 백테스트 실행 (수수료/슬리피지 포함)
                backtester = Backtester(
                    strategy=strategy,
                    initial_balance=1000000,
                    fee_rate=0.0005,
                    slippage_pct=0.001,
                )
                result = backtester.run(candles)

                # 결과 저장
                results.append({
                    "market": market_name,
                    "short": short,
                    "long": long,
                    "atr": atr,
                    "return_pct": result.total_return_pct,
                    "win_rate": result.win_rate,
                    "sharpe": result.sharpe_ratio,
                    "max_dd": result.max_drawdown_pct,
                    "trades": result.total_trades,
                    "avg_win": result.avg_win_pct,
                    "avg_loss": result.avg_loss_pct,
                })

                if test_count % 50 == 0:
                    print(f"진행률: {test_count}/{total_tests} ({test_count/total_tests*100:.1f}%)")

            except Exception as e:
                print(f"오류 발생 - {market_name} ({short}/{long}/{atr}): {e}")
                continue

    return pd.DataFrame(results)


def analyze_results(df):
    """결과 분석 및 최적 파라미터 추천"""

    print("\n" + "=" * 80)
    print("📈 분석 결과")
    print("=" * 80)

    # 1. 전체 시장에서 평균 성능이 좋은 파라미터
    print("\n[1] 전체 시장 평균 성능 TOP 10")
    print("-" * 80)

    avg_performance = df.groupby(["short", "long", "atr"]).agg({
        "return_pct": "mean",
        "win_rate": "mean",
        "sharpe": "mean",
        "max_dd": "mean",
        "trades": "mean",
    }).reset_index()

    # 종합 점수 계산 (수익률 + Sharpe - 낙폭)
    avg_performance["score"] = (
        avg_performance["return_pct"] * 0.4 +
        avg_performance["sharpe"] * 10 * 0.3 +
        avg_performance["win_rate"] * 0.2 -
        avg_performance["max_dd"] * 0.1
    )

    top10 = avg_performance.nlargest(10, "score")

    for idx, row in top10.iterrows():
        print(f"\n순위 {idx+1}:")
        print(f"  파라미터: short={row['short']}, long={row['long']}, atr={row['atr']:.2f}")
        print(f"  평균 수익률: {row['return_pct']:.2f}%")
        print(f"  평균 승률: {row['win_rate']:.1f}%")
        print(f"  평균 Sharpe: {row['sharpe']:.2f}")
        print(f"  평균 낙폭: {row['max_dd']:.2f}%")
        print(f"  평균 거래: {row['trades']:.1f}회")
        print(f"  종합 점수: {row['score']:.2f}")

    # 2. 시장별 최고 성능 파라미터
    print("\n\n[2] 시장별 최고 성능 파라미터")
    print("-" * 80)

    for market in df["market"].unique():
        market_df = df[df["market"] == market]
        best = market_df.nlargest(1, "return_pct").iloc[0]

        print(f"\n{market}:")
        print(f"  파라미터: short={best['short']}, long={best['long']}, atr={best['atr']:.2f}")
        print(f"  수익률: {best['return_pct']:.2f}%")
        print(f"  승률: {best['win_rate']:.1f}%")
        print(f"  Sharpe: {best['sharpe']:.2f}")
        print(f"  최대 낙폭: {best['max_dd']:.2f}%")
        print(f"  거래 횟수: {best['trades']}회")

    # 3. 안정적인 파라미터 (Sharpe Ratio 기준)
    print("\n\n[3] 위험 대비 수익이 좋은 파라미터 (Sharpe > 1.0)")
    print("-" * 80)

    stable = avg_performance[avg_performance["sharpe"] > 1.0].nlargest(5, "sharpe")

    if len(stable) > 0:
        for idx, row in stable.iterrows():
            print(f"\n파라미터: short={row['short']}, long={row['long']}, atr={row['atr']:.2f}")
            print(f"  Sharpe Ratio: {row['sharpe']:.2f}")
            print(f"  평균 수익률: {row['return_pct']:.2f}%")
            print(f"  평균 승률: {row['win_rate']:.1f}%")
    else:
        print("Sharpe > 1.0인 파라미터가 없습니다.")

    # 4. 추천 파라미터
    print("\n\n[4] 🎯 최종 추천 파라미터")
    print("-" * 80)

    best_overall = top10.iloc[0]

    print(f"\n✅ 종합 1순위: short={int(best_overall['short'])}, long={int(best_overall['long'])}, atr={best_overall['atr']:.2f}")
    print(f"   - 평균 수익률: {best_overall['return_pct']:.2f}%")
    print(f"   - 평균 승률: {best_overall['win_rate']:.1f}%")
    print(f"   - 평균 Sharpe: {best_overall['sharpe']:.2f}")
    print(f"   - 평균 낙폭: {best_overall['max_dd']:.2f}%")

    # 보수적 추천 (낙폭 최소화)
    conservative = avg_performance.nsmallest(10, "max_dd").nlargest(1, "return_pct").iloc[0]
    print(f"\n✅ 보수적 추천: short={int(conservative['short'])}, long={int(conservative['long'])}, atr={conservative['atr']:.2f}")
    print(f"   - 평균 수익률: {conservative['return_pct']:.2f}%")
    print(f"   - 평균 낙폭: {conservative['max_dd']:.2f}% (낮은 리스크)")

    # 공격적 추천 (수익률 최대화)
    aggressive = avg_performance.nlargest(1, "return_pct").iloc[0]
    print(f"\n✅ 공격적 추천: short={int(aggressive['short'])}, long={int(aggressive['long'])}, atr={aggressive['atr']:.2f}")
    print(f"   - 평균 수익률: {aggressive['return_pct']:.2f}% (높은 수익)")
    print(f"   - 평균 Sharpe: {aggressive['sharpe']:.2f}")

    return best_overall, conservative, aggressive


def main():
    print("\n🚀 MA Crossover 전략 최적화 시작")
    print("=" * 80)
    print("목표: 실전에 사용 가능한 최적 파라미터 찾기")
    print("방법: Grid Search (4가지 시장 조건에서 테스트)")
    print("=" * 80)

    # Grid Search 실행
    results_df = run_grid_search()

    # 결과 저장
    results_df.to_csv("optimization_results.csv", index=False, encoding="utf-8-sig")
    print(f"\n✅ 결과가 'optimization_results.csv'에 저장되었습니다.")

    # 분석
    best, conservative, aggressive = analyze_results(results_df)

    # 추천 설정 출력
    print("\n" + "=" * 80)
    print("💡 실전 적용 가이드")
    print("=" * 80)
    print("\n1. .env 파일에 다음과 같이 설정하세요:")
    print("\n# 종합 1순위 (균형)")
    print(f"MA_SHORT_WINDOW={int(best['short'])}")
    print(f"MA_LONG_WINDOW={int(best['long'])}")
    print(f"MA_ATR_THRESHOLD={best['atr']:.2f}")

    print("\n2. Stop-Loss/Take-Profit 설정:")
    print("RISK_STOP_LOSS_PCT=-5.0")
    print("RISK_TAKE_PROFIT_PCT=10.0")

    print("\n3. 리스크 관리:")
    print("RISK_MAX_DAILY_LOSS_PCT=3.0")
    print("RISK_MAX_POSITION_PCT=5.0")

    print("\n4. 페이퍼 트레이딩으로 3개월 검증 후 실거래 진행")

    print("\n" + "=" * 80)
    print("최적화 완료! 🎉")
    print("=" * 80)


if __name__ == "__main__":
    main()
