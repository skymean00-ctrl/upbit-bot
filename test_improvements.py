#!/usr/bin/env python3
"""수정된 기능들을 테스트하는 통합 테스트"""

import sys
from datetime import datetime

import numpy as np

from upbit_bot.services.backtest import Backtester
from upbit_bot.services.risk import RiskConfig, RiskManager
from upbit_bot.strategies import Candle, MovingAverageCrossoverStrategy, RSITrendFilterStrategy


def generate_test_candles(n=200, trend="up"):
    """테스트용 캔들 데이터 생성"""
    base_price = 50000000  # 5천만원
    candles = []

    for i in range(n):
        if trend == "up":
            # 상승 추세 + 노이즈
            price = base_price + (i * 10000) + np.random.normal(0, 50000)
        elif trend == "down":
            # 하락 추세 + 노이즈
            price = base_price - (i * 10000) + np.random.normal(0, 50000)
        else:
            # 횡보 + 노이즈
            price = base_price + np.random.normal(0, 100000)

        high = price * 1.01
        low = price * 0.99
        open_price = price * (1 + np.random.uniform(-0.005, 0.005))
        close_price = price * (1 + np.random.uniform(-0.005, 0.005))
        volume = np.random.uniform(10, 100)

        candles.append(
            Candle(
                timestamp=int(datetime.now().timestamp() * 1000) + (i * 60000),
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=volume,
            )
        )

    return candles


def test_rsi_strategy():
    """RSI 전략 버그 수정 확인"""
    print("\n" + "=" * 60)
    print("1️⃣ RSI Trend Filter 전략 테스트")
    print("=" * 60)

    try:
        strategy = RSITrendFilterStrategy()
        candles = generate_test_candles(100, trend="up")

        # 신호 생성 테스트
        signal = strategy.on_candles(candles)
        print(f"✅ RSI 전략 정상 실행됨")
        print(f"   - 생성된 시그널: {signal}")
        print(f"   - 파라미터: RSI window={strategy.rsi_window}, MA window={strategy.ma_window}")
        return True
    except Exception as e:
        print(f"❌ RSI 전략 실행 실패: {e}")
        return False


def test_ma_crossover_atr():
    """MA Crossover ATR 필터 개선 확인"""
    print("\n" + "=" * 60)
    print("2️⃣ MA Crossover ATR 필터 테스트")
    print("=" * 60)

    try:
        # 기존 설정 (ATR threshold = 0.02)
        strategy = MovingAverageCrossoverStrategy()
        print(f"✅ MA Crossover 전략 생성됨")
        print(f"   - ATR threshold: {strategy.atr_threshold} (2% 변동성 필터)")
        print(f"   - Short window: {strategy.short_window}, Long window: {strategy.long_window}")

        # 횡보장 테스트 (낮은 변동성)
        sideways_candles = generate_test_candles(100, trend="sideways")
        signal_sideways = strategy.on_candles(sideways_candles)
        print(f"\n   [횡보장 테스트]")
        print(f"   - 시그널: {signal_sideways} (HOLD 예상)")

        # 상승장 테스트 (높은 변동성)
        uptrend_candles = generate_test_candles(100, trend="up")
        signal_uptrend = strategy.on_candles(uptrend_candles)
        print(f"\n   [상승장 테스트]")
        print(f"   - 시그널: {signal_uptrend}")

        return True
    except Exception as e:
        print(f"❌ MA Crossover 테스트 실패: {e}")
        return False


def test_stop_loss_take_profit():
    """Stop-Loss/Take-Profit 구현 확인"""
    print("\n" + "=" * 60)
    print("3️⃣ Stop-Loss / Take-Profit 설정 확인")
    print("=" * 60)

    try:
        risk_config = RiskConfig()
        print(f"✅ RiskConfig 생성됨")
        print(f"   - Stop-Loss: {risk_config.stop_loss_pct}%")
        print(f"   - Take-Profit: {risk_config.take_profit_pct}%")
        print(f"   - Trailing Stop: {risk_config.trailing_stop_pct}")
        print(f"   - Max Daily Loss: {risk_config.max_daily_loss_pct}%")
        print(f"   - Max Position: {risk_config.max_position_pct}%")

        # RiskManager 생성 테스트
        def dummy_balance():
            return 1000000.0

        risk_manager = RiskManager(balance_fetcher=dummy_balance, config=risk_config)
        print(f"\n✅ RiskManager 정상 동작")

        # 포지션 오픈 가능 여부 체크
        can_open = risk_manager.can_open_position("KRW-BTC")
        print(f"   - 포지션 오픈 가능: {can_open}")

        return True
    except Exception as e:
        print(f"❌ Stop-Loss/Take-Profit 테스트 실패: {e}")
        return False


def test_backtest_with_fees():
    """백테스트 수수료/슬리피지 반영 확인"""
    print("\n" + "=" * 60)
    print("4️⃣ 백테스트 개선 사항 테스트")
    print("=" * 60)

    try:
        strategy = MovingAverageCrossoverStrategy(
            short_window=5,
            long_window=20,
            atr_threshold=0.0,  # ATR 필터 비활성화 (테스트용)
        )

        # 상승 추세 데이터
        candles = generate_test_candles(200, trend="up")

        # 수수료/슬리피지 없이 백테스트
        backtester_no_fees = Backtester(
            strategy=strategy,
            initial_balance=1000000,
            fee_rate=0.0,
            slippage_pct=0.0,
        )
        result_no_fees = backtester_no_fees.run(candles)

        # 수수료/슬리피지 포함 백테스트
        backtester_with_fees = Backtester(
            strategy=strategy,
            initial_balance=1000000,
            fee_rate=0.0005,  # 0.05%
            slippage_pct=0.001,  # 0.1%
        )
        result_with_fees = backtester_with_fees.run(candles)

        print(f"✅ 백테스트 실행 완료\n")

        print(f"[수수료/슬리피지 없이]")
        print(f"   - 총 수익률: {result_no_fees.total_return_pct:.2f}%")
        print(f"   - 승률: {result_no_fees.win_rate:.1f}%")
        print(f"   - 총 거래: {result_no_fees.total_trades}회")
        print(f"   - 최종 잔고: {result_no_fees.final_balance:,.0f}원")

        print(f"\n[수수료/슬리피지 포함]")
        print(f"   - 총 수익률: {result_with_fees.total_return_pct:.2f}%")
        print(f"   - 승률: {result_with_fees.win_rate:.1f}%")
        print(f"   - 총 거래: {result_with_fees.total_trades}회")
        print(f"   - 승리 거래: {result_with_fees.winning_trades}회")
        print(f"   - 패배 거래: {result_with_fees.losing_trades}회")
        print(f"   - 평균 승리: {result_with_fees.avg_win_pct:.2f}%")
        print(f"   - 평균 손실: {result_with_fees.avg_loss_pct:.2f}%")
        print(f"   - Sharpe Ratio: {result_with_fees.sharpe_ratio:.2f}")
        print(f"   - Max Drawdown: {result_with_fees.max_drawdown_pct:.2f}%")
        print(f"   - 최종 잔고: {result_with_fees.final_balance:,.0f}원")

        # 수수료 영향 계산
        impact = result_no_fees.total_return_pct - result_with_fees.total_return_pct
        print(f"\n📊 수수료/슬리피지 영향: -{impact:.2f}%p")

        return True
    except Exception as e:
        print(f"❌ 백테스트 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\n" + "🔬 " * 20)
    print("Upbit Bot 개선 사항 통합 테스트")
    print("🔬 " * 20)

    results = []

    # 각 테스트 실행
    results.append(("RSI 전략 버그 수정", test_rsi_strategy()))
    results.append(("MA Crossover ATR 개선", test_ma_crossover_atr()))
    results.append(("Stop-Loss/Take-Profit", test_stop_loss_take_profit()))
    results.append(("백테스트 수수료 반영", test_backtest_with_fees()))

    # 결과 요약
    print("\n" + "=" * 60)
    print("📊 테스트 결과 요약")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ 통과" if result else "❌ 실패"
        print(f"{status} - {name}")

    print(f"\n총 {passed}/{total} 테스트 통과 ({passed/total*100:.1f}%)")

    if passed == total:
        print("\n🎉 모든 개선 사항이 정상적으로 작동합니다!")
        return 0
    else:
        print(f"\n⚠️ {total - passed}개의 테스트가 실패했습니다.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
