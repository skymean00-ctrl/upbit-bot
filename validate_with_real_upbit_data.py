#!/usr/bin/env python3
"""실제 업비트 데이터로 전략 재검증"""

from datetime import datetime

import requests

from upbit_bot.services.backtest import Backtester
from upbit_bot.strategies import Candle, WeightedCombinedStrategy, MovingAverageCrossoverStrategy


def fetch_real_upbit_candles(market: str = "KRW-BTC", unit: int = 60, count: int = 1000) -> list[Candle]:
    """
    실제 업비트 API에서 캔들 데이터 수집 (공개 API, 인증 불필요)

    Args:
        market: 마켓 코드 (예: KRW-BTC, KRW-ETH)
        unit: 캔들 단위 (분) - 1, 3, 5, 15, 10, 30, 60, 240
        count: 수집할 캔들 개수 (최대 200)

    Returns:
        Candle 리스트
    """
    base_url = "https://api.upbit.com/v1"

    print(f"\n{'='*80}")
    print(f"📡 업비트 실제 데이터 수집 중...")
    print(f"{'='*80}")
    print(f"마켓: {market}")
    print(f"캔들 단위: {unit}분")
    print(f"수집 개수: {count}개 (200개씩 분할 수집)")

    all_candles = []
    remaining = count

    while remaining > 0:
        fetch_count = min(remaining, 200)  # 업비트 API 제한: 최대 200개

        try:
            print(f"\n📥 {fetch_count}개 캔들 수집 중... (총 {len(all_candles)}/{count})")

            # 공개 API 직접 호출 (인증 불필요)
            url = f"{base_url}/candles/minutes/{unit}"
            params = {
                "market": market,
                "count": fetch_count,
            }

            response = requests.get(url, params=params, timeout=10)

            if response.status_code != 200:
                print(f"❌ API 오류: {response.status_code} - {response.text}")
                break

            raw_candles = response.json()

            if not raw_candles:
                print("⚠️  더 이상 데이터가 없습니다.")
                break

            # Candle 객체로 변환
            candles = [
                Candle(
                    timestamp=int(item["timestamp"]),
                    open=float(item["opening_price"]),
                    high=float(item["high_price"]),
                    low=float(item["low_price"]),
                    close=float(item["trade_price"]),
                    volume=float(item["candle_acc_trade_volume"]),
                )
                for item in reversed(raw_candles)
            ]

            all_candles.extend(candles)
            remaining -= len(candles)

            # 마지막 캔들 시간 확인
            last_timestamp = raw_candles[0]["timestamp"]
            last_time = datetime.fromtimestamp(last_timestamp / 1000)
            print(f"✓ 수집 완료: 마지막 캔들 시간 = {last_time}")

            if len(raw_candles) < fetch_count:
                print("⚠️  요청한 개수보다 적은 데이터가 반환되었습니다.")
                break

        except Exception as e:
            print(f"❌ 데이터 수집 실패: {e}")
            import traceback
            traceback.print_exc()
            break

    print(f"\n✅ 총 {len(all_candles)}개 캔들 수집 완료")
    return all_candles


def test_with_real_data():
    """실제 업비트 데이터로 전략 테스트"""

    print("\n" + "="*80)
    print("🔬 실제 업비트 데이터로 전략 재검증")
    print("="*80)

    # 다양한 마켓 테스트
    markets = [
        ("KRW-BTC", "비트코인"),
        ("KRW-ETH", "이더리움"),
        ("KRW-XRP", "리플"),
    ]

    results = []

    for market_code, market_name in markets:
        print(f"\n{'='*80}")
        print(f"📊 {market_name} ({market_code}) 분석")
        print(f"{'='*80}")

        try:
            # 실제 데이터 수집 (1시간봉, 최대 200개)
            candles = fetch_real_upbit_candles(market=market_code, unit=60, count=200)

            if len(candles) < 100:
                print(f"⚠️  데이터 부족 ({len(candles)}개): 스킵")
                continue

            print(f"\n수집 기간:")
            print(f"  시작: {datetime.fromtimestamp(candles[0].timestamp / 1000)}")
            print(f"  종료: {datetime.fromtimestamp(candles[-1].timestamp / 1000)}")
            print(f"  총 {len(candles)}개 캔들")

            # 가격 범위 확인
            prices = [c.close for c in candles]
            print(f"\n가격 범위:")
            print(f"  최저: {min(prices):,.0f}원")
            print(f"  최고: {max(prices):,.0f}원")
            print(f"  현재: {prices[-1]:,.0f}원")
            print(f"  변동: {(prices[-1] - prices[0]) / prices[0] * 100:+.2f}%")

            # 1. 가중 복합 전략 테스트 (최적)
            print(f"\n{'─'*80}")
            print("🎯 가중 복합 전략 (RSI 0.3 + MA 0.7)")
            print(f"{'─'*80}")

            strategy_weighted = WeightedCombinedStrategy(
                rsi_window=14,
                rsi_ma_window=50,
                rsi_oversold=30,
                rsi_overbought=70,
                ma_short_window=14,
                ma_long_window=20,
                ma_atr_threshold=0.02,
                rsi_weight=0.3,
                ma_weight=0.7,
            )

            backtester_weighted = Backtester(
                strategy=strategy_weighted,
                initial_balance=1000000,
                fee_rate=0.0005,
                slippage_pct=0.001,
            )

            result_weighted = backtester_weighted.run(candles)

            print(f"\n결과:")
            print(f"  수익률:       {result_weighted.total_return_pct:>8.2f}%")
            print(f"  최종 잔고:    {result_weighted.final_balance:>12,.0f}원")
            print(f"  총 거래:      {result_weighted.total_trades:>8}회")
            print(f"  승률:         {result_weighted.win_rate:>8.1f}%")
            print(f"  Sharpe:       {result_weighted.sharpe_ratio:>8.2f}")
            print(f"  최대 낙폭:    {result_weighted.max_drawdown_pct:>8.2f}%")

            # 2. MA Crossover 단독 비교
            print(f"\n{'─'*80}")
            print("📉 MA Crossover 단독 (비교용)")
            print(f"{'─'*80}")

            strategy_ma = MovingAverageCrossoverStrategy(
                short_window=14,
                long_window=20,
                atr_threshold=0.02,
            )

            backtester_ma = Backtester(
                strategy=strategy_ma,
                initial_balance=1000000,
                fee_rate=0.0005,
                slippage_pct=0.001,
            )

            result_ma = backtester_ma.run(candles)

            print(f"\n결과:")
            print(f"  수익률:       {result_ma.total_return_pct:>8.2f}%")
            print(f"  최종 잔고:    {result_ma.final_balance:>12,.0f}원")
            print(f"  총 거래:      {result_ma.total_trades:>8}회")
            print(f"  승률:         {result_ma.win_rate:>8.1f}%")
            print(f"  Sharpe:       {result_ma.sharpe_ratio:>8.2f}")
            print(f"  최대 낙폭:    {result_ma.max_drawdown_pct:>8.2f}%")

            # 3. 개선 비교
            improvement = result_weighted.total_return_pct - result_ma.total_return_pct

            print(f"\n{'─'*80}")
            print(f"📈 개선 효과")
            print(f"{'─'*80}")
            print(f"  수익률 개선:  {improvement:>8.2f}%p")
            print(f"  Sharpe 개선:  {result_weighted.sharpe_ratio - result_ma.sharpe_ratio:>8.2f}")

            results.append({
                "market": market_name,
                "market_code": market_code,
                "candles": len(candles),
                "weighted_return": result_weighted.total_return_pct,
                "weighted_sharpe": result_weighted.sharpe_ratio,
                "weighted_win_rate": result_weighted.win_rate,
                "ma_return": result_ma.total_return_pct,
                "ma_sharpe": result_ma.sharpe_ratio,
                "improvement": improvement,
            })

        except Exception as e:
            print(f"\n❌ {market_name} 테스트 실패: {e}")
            import traceback
            traceback.print_exc()

    # 최종 요약
    print("\n" + "="*80)
    print("📊 전체 마켓 요약")
    print("="*80)

    if results:
        print(f"\n{'마켓':<10} {'캔들':<8} {'가중전략':<12} {'MA단독':<12} {'개선':<10}")
        print("─"*80)

        for r in results:
            print(
                f"{r['market']:<10} "
                f"{r['candles']:<8} "
                f"{r['weighted_return']:>8.2f}% "
                f"({r['weighted_sharpe']:>4.2f}) "
                f"{r['ma_return']:>8.2f}% "
                f"({r['ma_sharpe']:>4.2f}) "
                f"{r['improvement']:>+8.2f}%p"
            )

        # 평균 계산
        avg_weighted = sum(r['weighted_return'] for r in results) / len(results)
        avg_ma = sum(r['ma_return'] for r in results) / len(results)
        avg_improvement = sum(r['improvement'] for r in results) / len(results)

        print("─"*80)
        print(f"{'평균':<10} {'─':<8} {avg_weighted:>8.2f}% {avg_ma:>18.2f}% {avg_improvement:>18.2f}%p")

        print(f"\n{'='*80}")
        print("✅ 실제 업비트 데이터 검증 완료!")
        print("="*80)

        if avg_improvement > 0:
            print(f"\n🎉 가중 복합 전략이 평균 {avg_improvement:.2f}%p 더 우수합니다!")
        else:
            print(f"\n⚠️  실제 데이터에서는 MA 단독이 {-avg_improvement:.2f}%p 더 우수합니다.")
            print("    → 전략 재조정 필요")

        print("\n⚠️  주의사항:")
        print("  1. 과거 데이터 성능이 미래 수익을 보장하지 않습니다")
        print("  2. 페이퍼 트레이딩 3개월 검증 필수")
        print("  3. 실거래는 소액(10만원)부터 시작")
        print("  4. 손절(-5%), 익절(+10%) 철저히 준수")

    else:
        print("\n❌ 검증 가능한 데이터가 없습니다.")

    return results


if __name__ == "__main__":
    test_with_real_data()
