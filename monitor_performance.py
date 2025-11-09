#!/usr/bin/env python3
"""페이퍼 트레이딩 성과 모니터링"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

# 거래 로그 파일 경로
TRADES_LOG_FILE = Path("paper_trading_trades.json")


def load_trades():
    """거래 내역 로드"""
    if not TRADES_LOG_FILE.exists():
        return []

    with open(TRADES_LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def calculate_metrics(trades: list, initial_balance: float = 1000000):
    """성과 지표 계산"""

    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0,
            "avg_pnl_pct": 0,
            "total_pnl_pct": 0,
            "sharpe_ratio": 0,
            "max_drawdown": 0,
        }

    # 매도 거래만 필터
    sell_trades = [t for t in trades if t.get("signal") == "sell"]

    if not sell_trades:
        return {
            "total_trades": len(trades),
            "win_rate": 0,
            "avg_pnl_pct": 0,
            "total_pnl_pct": 0,
            "sharpe_ratio": 0,
            "max_drawdown": 0,
        }

    # 수익률 계산
    pnl_pcts = [t.get("pnl_pct", 0) for t in sell_trades]
    wins = sum(1 for p in pnl_pcts if p > 0)
    win_rate = (wins / len(pnl_pcts) * 100) if pnl_pcts else 0

    # 평균/총 수익률
    avg_pnl = sum(pnl_pcts) / len(pnl_pcts)
    total_pnl = sum(pnl_pcts)

    # Sharpe Ratio (간단 계산)
    if len(pnl_pcts) > 1:
        mean_return = sum(pnl_pcts) / len(pnl_pcts)
        variance = sum((p - mean_return) ** 2 for p in pnl_pcts) / (len(pnl_pcts) - 1)
        std_dev = variance ** 0.5
        sharpe = (mean_return / std_dev) if std_dev > 0 else 0
    else:
        sharpe = 0

    # 최대 낙폭 (간단 계산)
    cumulative_returns = []
    cum_return = 0
    for pnl in pnl_pcts:
        cum_return += pnl
        cumulative_returns.append(cum_return)

    max_drawdown = 0
    peak = cumulative_returns[0] if cumulative_returns else 0
    for ret in cumulative_returns:
        if ret > peak:
            peak = ret
        drawdown = peak - ret
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    return {
        "total_trades": len(trades),
        "sell_count": len(sell_trades),
        "win_rate": win_rate,
        "avg_pnl_pct": avg_pnl,
        "total_pnl_pct": total_pnl,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "wins": wins,
        "losses": len(pnl_pcts) - wins,
    }


def print_dashboard():
    """대시보드 출력"""

    trades = load_trades()

    print("\n" + "="*80)
    print("📊 페이퍼 트레이딩 성과 대시보드")
    print("="*80)

    if not trades:
        print("\n⚠️  아직 거래 내역이 없습니다.")
        print("페이퍼 트레이딩을 먼저 실행하세요: python run_paper_trading.py")
        print("="*80)
        return

    # 기본 정보
    first_trade = trades[0]
    last_trade = trades[-1]

    print(f"\n📅 기간:")
    print(f"  시작: {first_trade.get('timestamp', 'N/A')}")
    print(f"  종료: {last_trade.get('timestamp', 'N/A')}")

    # 성과 지표
    initial_balance = 1000000  # .env에서 로드하면 더 좋음
    metrics = calculate_metrics(trades, initial_balance)

    print(f"\n💰 성과 지표:")
    print(f"  총 거래 횟수:  {metrics['total_trades']:>8}회")
    print(f"  청산 횟수:     {metrics['sell_count']:>8}회")
    print(f"  승:            {metrics['wins']:>8}회")
    print(f"  패:            {metrics['losses']:>8}회")
    print(f"  승률:          {metrics['win_rate']:>8.1f}%")
    print(f"  평균 수익률:   {metrics['avg_pnl_pct']:>8.2f}%")
    print(f"  총 수익률:     {metrics['total_pnl_pct']:>8.2f}%")
    print(f"  Sharpe Ratio:  {metrics['sharpe_ratio']:>8.2f}")
    print(f"  최대 낙폭:     {metrics['max_drawdown']:>8.2f}%")

    # 예상 잔고
    estimated_balance = initial_balance * (1 + metrics['total_pnl_pct'] / 100)
    profit = estimated_balance - initial_balance

    print(f"\n💵 잔고 (추정):")
    print(f"  초기 잔고:     {initial_balance:>12,.0f}원")
    print(f"  현재 잔고:     {estimated_balance:>12,.0f}원")
    print(f"  손익:          {profit:>+12,.0f}원")

    # 최근 5개 거래
    print(f"\n📝 최근 거래 (최대 5개):")
    print("─"*80)

    recent_trades = trades[-5:]
    for i, trade in enumerate(reversed(recent_trades), 1):
        signal = trade.get("signal", "unknown")
        timestamp = trade.get("timestamp", "N/A")[:19]  # 초까지만

        if signal == "buy":
            stake = trade.get("stake", 0)
            price = trade.get("price", 0)
            print(f"{i}. [{timestamp}] 📈 매수: {stake:,.0f}원 @ {price:,.0f}원")
        elif signal == "sell":
            price = trade.get("price", 0)
            pnl = trade.get("pnl_pct", 0)
            emoji = "✅" if pnl > 0 else "❌"
            print(f"{i}. [{timestamp}] {emoji} 매도: @ {price:,.0f}원 (PnL: {pnl:+.2f}%)")

    # 평가
    print("\n" + "="*80)
    print("📈 평가:")

    if metrics['total_pnl_pct'] > 10:
        print("✅ 우수: 목표 수익률 달성!")
    elif metrics['total_pnl_pct'] > 0:
        print("✅ 양호: 플러스 수익 유지")
    elif metrics['total_pnl_pct'] > -5:
        print("⚠️  주의: 소폭 손실 중")
    else:
        print("❌ 경고: 큰 손실 발생 - 전략 재검토 필요")

    if metrics['sharpe_ratio'] > 1.5:
        print("✅ Sharpe Ratio 우수 (>1.5)")
    elif metrics['sharpe_ratio'] > 1.0:
        print("✅ Sharpe Ratio 양호 (>1.0)")
    else:
        print("⚠️  Sharpe Ratio 미흡 (<1.0)")

    if metrics['win_rate'] > 50:
        print("✅ 승률 우수 (>50%)")
    elif metrics['win_rate'] > 40:
        print("✅ 승률 양호 (>40%)")
    else:
        print("⚠️  승률 미흡 (<40%)")

    print("="*80)


def print_daily_summary():
    """일일 요약"""

    trades = load_trades()

    if not trades:
        print("거래 내역이 없습니다.")
        return

    # 오늘 날짜
    today = datetime.now().date()

    # 오늘 거래만 필터
    today_trades = [
        t for t in trades
        if datetime.fromisoformat(t.get("timestamp", "2000-01-01")).date() == today
    ]

    if not today_trades:
        print(f"\n{today} 거래 내역이 없습니다.")
        return

    print(f"\n📅 {today} 거래 요약:")
    print("─"*80)

    metrics = calculate_metrics(today_trades)

    print(f"거래 횟수: {metrics['total_trades']}회")
    print(f"승률: {metrics['win_rate']:.1f}%")
    print(f"수익률: {metrics['total_pnl_pct']:+.2f}%")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--daily":
        print_daily_summary()
    else:
        print_dashboard()
