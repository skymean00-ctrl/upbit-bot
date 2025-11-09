#!/usr/bin/env python3
"""페이퍼 트레이딩 실행 스크립트"""

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트를 Python path에 추가
sys.path.insert(0, str(Path(__file__).parent))

from upbit_bot.core import UpbitClient
from upbit_bot.services.execution import ExecutionEngine
from upbit_bot.services.risk import RiskConfig, RiskManager, PositionSizer
from upbit_bot.strategies import WeightedCombinedStrategy, MovingAverageCrossoverStrategy

# 환경 변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


class PaperTradingTracker:
    """페이퍼 트레이딩 성과 추적"""

    def __init__(self, initial_balance: float):
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.trades = []
        self.start_time = datetime.now()

    def record_trade(self, trade_info: dict):
        """거래 기록"""
        trade_info["timestamp"] = datetime.now().isoformat()
        self.trades.append(trade_info)

        # 잔고 업데이트 (간단한 시뮬레이션)
        if trade_info.get("signal") == "buy":
            logger.info(f"📈 매수 신호: {trade_info.get('stake', 0):,.0f}원")
        elif trade_info.get("signal") == "sell":
            pnl_pct = trade_info.get("pnl_pct", 0)
            logger.info(f"📉 매도 신호: PnL {pnl_pct:+.2f}%")

    def get_stats(self) -> dict:
        """통계 계산"""
        total_trades = len(self.trades)
        buy_trades = sum(1 for t in self.trades if t.get("signal") == "buy")
        sell_trades = sum(1 for t in self.trades if t.get("signal") == "sell")

        # 수익률 계산 (단순화)
        pnl_pcts = [t.get("pnl_pct", 0) for t in self.trades if t.get("signal") == "sell"]
        avg_pnl = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0
        wins = sum(1 for p in pnl_pcts if p > 0)
        win_rate = (wins / len(pnl_pcts) * 100) if pnl_pcts else 0

        elapsed = datetime.now() - self.start_time

        return {
            "initial_balance": self.initial_balance,
            "total_trades": total_trades,
            "buy_count": buy_trades,
            "sell_count": sell_trades,
            "avg_pnl_pct": avg_pnl,
            "win_rate": win_rate,
            "elapsed_hours": elapsed.total_seconds() / 3600,
        }

    def print_summary(self):
        """요약 출력"""
        stats = self.get_stats()

        print("\n" + "="*80)
        print("📊 페이퍼 트레이딩 현재 성과")
        print("="*80)
        print(f"초기 잔고:     {stats['initial_balance']:>12,.0f}원")
        print(f"총 거래 횟수:  {stats['total_trades']:>12}회")
        print(f"  - 매수:      {stats['buy_count']:>12}회")
        print(f"  - 매도:      {stats['sell_count']:>12}회")
        print(f"평균 수익률:   {stats['avg_pnl_pct']:>12.2f}%")
        print(f"승률:          {stats['win_rate']:>12.1f}%")
        print(f"실행 시간:     {stats['elapsed_hours']:>12.1f}시간")
        print("="*80)


def create_strategy(strategy_name: str):
    """전략 생성"""
    if strategy_name == "weighted_combined":
        logger.info("🎯 가중 복합 전략 (RSI 0.3 + MA 0.7) 사용")
        return WeightedCombinedStrategy(
            rsi_window=int(os.getenv("RSI_WINDOW", "14")),
            rsi_ma_window=int(os.getenv("RSI_MA_WINDOW", "50")),
            rsi_oversold=int(os.getenv("RSI_OVERSOLD", "30")),
            rsi_overbought=int(os.getenv("RSI_OVERBOUGHT", "70")),
            ma_short_window=int(os.getenv("MA_SHORT_WINDOW", "14")),
            ma_long_window=int(os.getenv("MA_LONG_WINDOW", "20")),
            ma_atr_threshold=float(os.getenv("MA_ATR_THRESHOLD", "0.02")),
            rsi_weight=float(os.getenv("WEIGHTED_RSI_WEIGHT", "0.3")),
            ma_weight=float(os.getenv("WEIGHTED_MA_WEIGHT", "0.7")),
        )
    elif strategy_name == "ma_crossover":
        logger.info("📉 MA Crossover 전략 사용")
        return MovingAverageCrossoverStrategy(
            short_window=int(os.getenv("MA_SHORT_WINDOW", "14")),
            long_window=int(os.getenv("MA_LONG_WINDOW", "20")),
            atr_threshold=float(os.getenv("MA_ATR_THRESHOLD", "0.02")),
        )
    else:
        raise ValueError(f"알 수 없는 전략: {strategy_name}")


def main():
    """페이퍼 트레이딩 메인"""

    print("\n" + "="*80)
    print("🚀 페이퍼 트레이딩 시작")
    print("="*80)
    print("⚠️  실제 거래가 아닙니다. 시뮬레이션 모드입니다.")
    print("="*80)

    # 설정 로드
    market = os.getenv("UPBIT_MARKET", "KRW-BTC")
    strategy_name = os.getenv("STRATEGY_NAME", "weighted_combined")
    initial_balance = float(os.getenv("PAPER_INITIAL_BALANCE", "1000000"))
    poll_interval = int(os.getenv("POLL_INTERVAL", "60"))
    candle_count = int(os.getenv("CANDLE_COUNT", "200"))
    candle_unit = int(os.getenv("CANDLE_UNIT", "60"))

    # DRY_RUN 확인
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if not dry_run:
        print("\n⚠️  경고: DRY_RUN=false로 설정되어 있습니다!")
        print("⚠️  페이퍼 트레이딩을 위해 DRY_RUN=true를 권장합니다.")
        response = input("계속하시겠습니까? (yes/no): ")
        if response.lower() != "yes":
            print("종료합니다.")
            return

    print(f"\n📊 설정:")
    print(f"  마켓:          {market}")
    print(f"  전략:          {strategy_name}")
    print(f"  초기 잔고:     {initial_balance:,.0f}원")
    print(f"  폴링 간격:     {poll_interval}초")
    print(f"  캔들 개수:     {candle_count}개 ({candle_unit}분봉)")
    print(f"  드라이런:      {dry_run}")

    # 전략 생성
    strategy = create_strategy(strategy_name)

    # UpbitClient 생성 (더미 키 사용 - 공개 API만 사용)
    client = UpbitClient(
        access_key=os.getenv("UPBIT_ACCESS_KEY", "dummy"),
        secret_key=os.getenv("UPBIT_SECRET_KEY", "dummy"),
    )

    # 리스크 관리 설정
    risk_config = RiskConfig(
        max_daily_loss_pct=float(os.getenv("RISK_MAX_DAILY_LOSS_PCT", "3.0")),
        max_position_pct=float(os.getenv("RISK_MAX_POSITION_PCT", "5.0")),
        max_open_positions=int(os.getenv("RISK_MAX_OPEN_POSITIONS", "1")),
        min_balance_krw=float(os.getenv("RISK_MIN_BALANCE_KRW", "10000")),
        stop_loss_pct=float(os.getenv("RISK_STOP_LOSS_PCT", "-5.0")),
        take_profit_pct=float(os.getenv("RISK_TAKE_PROFIT_PCT", "10.0")),
    )

    risk_manager = RiskManager(config=risk_config)

    # PositionSizer (고정 금액)
    def balance_fetcher():
        return initial_balance

    position_sizer = PositionSizer(
        balance_fetcher=balance_fetcher,
        position_pct=risk_config.max_position_pct,
    )

    # 페이퍼 트레이딩 트래커
    tracker = PaperTradingTracker(initial_balance)

    # ExecutionEngine 생성
    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market=market,
        candle_unit=candle_unit,
        candle_count=candle_count,
        poll_interval=poll_interval,
        dry_run=True,  # 항상 페이퍼 트레이딩
        risk_manager=risk_manager,
        position_sizer=position_sizer,
    )

    print(f"\n✅ 엔진 초기화 완료")
    print(f"✅ {poll_interval}초마다 시장 데이터를 체크합니다")
    print(f"\n종료하려면 Ctrl+C를 누르세요\n")

    # 통계 출력 카운터
    iteration = 0
    summary_interval = 10  # 10번마다 요약 출력

    try:
        while True:
            iteration += 1

            # 한 번 실행
            try:
                result = engine.run_once()

                # 거래 발생 시 기록
                if result:
                    tracker.record_trade(result)

                # 주기적으로 요약 출력
                if iteration % summary_interval == 0:
                    tracker.print_summary()

                    # 현재 상태 출력
                    if engine.last_signal:
                        print(f"\n마지막 신호: {engine.last_signal.value}")
                    if engine.position_price:
                        print(f"현재 포지션: {engine.position_price:,.0f}원")

            except Exception as e:
                logger.error(f"실행 중 오류: {e}", exc_info=True)

            # 대기
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\n🛑 사용자가 중단했습니다")

    finally:
        # 최종 통계
        tracker.print_summary()

        print("\n" + "="*80)
        print("📝 상세 거래 내역")
        print("="*80)

        if tracker.trades:
            for i, trade in enumerate(tracker.trades, 1):
                signal = trade.get("signal", "unknown")
                timestamp = trade.get("timestamp", "N/A")
                print(f"\n{i}. [{timestamp}]")
                print(f"   신호: {signal}")

                if signal == "buy":
                    print(f"   금액: {trade.get('stake', 0):,.0f}원")
                    print(f"   가격: {trade.get('price', 0):,.0f}원")
                elif signal == "sell":
                    print(f"   가격: {trade.get('price', 0):,.0f}원")
                    print(f"   수익: {trade.get('pnl_pct', 0):+.2f}%")
        else:
            print("\n거래 내역이 없습니다.")

        print("\n" + "="*80)
        print("✅ 페이퍼 트레이딩 종료")
        print("="*80)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"치명적 오류: {e}", exc_info=True)
        sys.exit(1)
