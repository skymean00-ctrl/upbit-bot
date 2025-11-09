#!/usr/bin/env python3
"""최적화 결과 재분석"""

import pandas as pd

# 결과 로드
df = pd.read_csv("optimization_results.csv")

print("=" * 80)
print("📊 최적화 결과 재분석 (거래가 발생한 경우만)")
print("=" * 80)

# 거래가 발생한 경우만 필터링
df_traded = df[df["trades"] > 0].copy()

print(f"\n총 테스트: {len(df)}개")
print(f"거래 발생: {len(df_traded)}개 ({len(df_traded)/len(df)*100:.1f}%)")

if len(df_traded) == 0:
    print("\n⚠️ 거래가 발생한 케이스가 없습니다!")
    print("ATR threshold가 너무 높거나 데이터에 문제가 있을 수 있습니다.")
    exit(1)

# 시장별 통계
print("\n\n[1] 시장별 거래 발생 현황")
print("-" * 80)
for market in df["market"].unique():
    market_df = df[df["market"] == market]
    traded_df = market_df[market_df["trades"] > 0]
    print(f"{market}: {len(traded_df)}/{len(market_df)} 조합에서 거래 발생")

# 평균 성능 계산
avg_perf = df_traded.groupby(["short", "long", "atr"]).agg({
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
print("\n\n[2] 종합 성능 TOP 10")
print("-" * 80)

top10 = avg_perf.nlargest(10, "score")

for i, (idx, row) in enumerate(top10.iterrows(), 1):
    print(f"\n{i}위:")
    print(f"  파라미터: short={int(row['short'])}, long={int(row['long'])}, atr={row['atr']:.2f}")
    print(f"  평균 수익률: {row['return_pct']:>8.2f}%")
    print(f"  평균 승률:   {row['win_rate']:>8.1f}%")
    print(f"  Sharpe:      {row['sharpe']:>8.2f}")
    print(f"  최대 낙폭:   {row['max_dd']:>8.2f}%")
    print(f"  평균 거래:   {row['trades']:>8.1f}회")
    print(f"  종합 점수:   {row['score']:>8.2f}")

# 시장별 최고 성능
print("\n\n[3] 시장별 최고 수익률 파라미터")
print("-" * 80)

for market in df_traded["market"].unique():
    market_df = df_traded[df_traded["market"] == market]
    best = market_df.nlargest(1, "return_pct").iloc[0]

    print(f"\n📈 {market}:")
    print(f"  파라미터: short={int(best['short'])}, long={int(best['long'])}, atr={best['atr']:.2f}")
    print(f"  수익률:   {best['return_pct']:>8.2f}%")
    print(f"  승률:     {best['win_rate']:>8.1f}%")
    print(f"  Sharpe:   {best['sharpe']:>8.2f}")
    print(f"  낙폭:     {best['max_dd']:>8.2f}%")
    print(f"  거래:     {int(best['trades'])}회")

# Sharpe > 1 필터링
high_sharpe = avg_perf[avg_perf["sharpe"] > 1.0]

print("\n\n[4] 위험 조정 수익률이 우수한 파라미터 (Sharpe > 1.0)")
print("-" * 80)

if len(high_sharpe) > 0:
    for i, (idx, row) in enumerate(high_sharpe.nlargest(5, "sharpe").iterrows(), 1):
        print(f"\n{i}위:")
        print(f"  파라미터: short={int(row['short'])}, long={int(row['long'])}, atr={row['atr']:.2f}")
        print(f"  Sharpe:      {row['sharpe']:>8.2f}")
        print(f"  평균 수익률: {row['return_pct']:>8.2f}%")
        print(f"  평균 승률:   {row['win_rate']:>8.1f}%")
        print(f"  최대 낙폭:   {row['max_dd']:>8.2f}%")
else:
    print("⚠️ Sharpe Ratio > 1.0인 파라미터가 없습니다.")
    print("현재 전략으로는 안정적인 수익을 기대하기 어렵습니다.")

# 최종 추천
print("\n\n[5] 🎯 최종 추천 파라미터")
print("=" * 80)

best_overall = top10.iloc[0]
print(f"\n✅ 종합 1순위 (균형형):")
print(f"   short_window={int(best_overall['short'])}")
print(f"   long_window={int(best_overall['long'])}")
print(f"   atr_threshold={best_overall['atr']:.2f}")
print(f"\n   예상 성능:")
print(f"   - 평균 수익률: {best_overall['return_pct']:.2f}%")
print(f"   - 평균 승률: {best_overall['win_rate']:.1f}%")
print(f"   - Sharpe Ratio: {best_overall['sharpe']:.2f}")
print(f"   - 최대 낙폭: {best_overall['max_dd']:.2f}%")
print(f"   - 평균 거래: {best_overall['trades']:.0f}회")

# 보수적 (낮은 낙폭)
safe = avg_perf.nsmallest(5, "max_dd").nlargest(1, "return_pct").iloc[0]
print(f"\n✅ 보수적 추천 (안정형):")
print(f"   short_window={int(safe['short'])}")
print(f"   long_window={int(safe['long'])}")
print(f"   atr_threshold={safe['atr']:.2f}")
print(f"\n   예상 성능:")
print(f"   - 평균 수익률: {safe['return_pct']:.2f}%")
print(f"   - 최대 낙폭: {safe['max_dd']:.2f}% ⬇️ (낮은 리스크)")

# 공격적 (높은 수익)
aggressive = avg_perf.nlargest(1, "return_pct").iloc[0]
print(f"\n✅ 공격적 추천 (수익형):")
print(f"   short_window={int(aggressive['short'])}")
print(f"   long_window={int(aggressive['long'])}")
print(f"   atr_threshold={aggressive['atr']:.2f}")
print(f"\n   예상 성능:")
print(f"   - 평균 수익률: {aggressive['return_pct']:.2f}% ⬆️ (높은 수익)")
print(f"   - Sharpe Ratio: {aggressive['sharpe']:.2f}")
print(f"   - 최대 낙폭: {aggressive['max_dd']:.2f}%")

# 환경 설정 파일 생성
print("\n\n[6] 💾 .env 설정 파일 예시")
print("=" * 80)

env_config = f"""
# MA Crossover 전략 파라미터 (종합 1순위)
MA_SHORT_WINDOW={int(best_overall['short'])}
MA_LONG_WINDOW={int(best_overall['long'])}
MA_ATR_THRESHOLD={best_overall['atr']:.2f}

# Stop-Loss / Take-Profit (필수!)
RISK_STOP_LOSS_PCT=-5.0
RISK_TAKE_PROFIT_PCT=10.0

# 일일 리스크 관리
RISK_MAX_DAILY_LOSS_PCT=3.0
RISK_MAX_POSITION_PCT=5.0
RISK_MAX_OPEN_POSITIONS=3

# Upbit API (본인의 키로 변경)
UPBIT_ACCESS_KEY=your_access_key_here
UPBIT_SECRET_KEY=your_secret_key_here

# 시장 설정
UPBIT_MARKET=KRW-BTC
"""

print(env_config)

# 경고 메시지
print("\n" + "=" * 80)
print("⚠️  주의사항")
print("=" * 80)

if best_overall["return_pct"] < 0:
    print("\n🚨 경고: 최적 파라미터도 평균 수익률이 마이너스입니다!")
    print("   현재 전략으로는 실거래를 권장하지 않습니다.")
    print("   다음을 고려하세요:")
    print("   1. 다른 전략 추가 (RSI, Bollinger Bands 등)")
    print("   2. 포트폴리오 다각화")
    print("   3. 더 긴 기간의 실제 데이터로 재검증")

if best_overall["sharpe"] < 1.0:
    print("\n⚠️  Sharpe Ratio가 1.0 미만입니다.")
    print("   위험 대비 수익이 충분하지 않습니다.")
    print("   페이퍼 트레이딩으로 충분히 검증하세요.")

if best_overall["win_rate"] < 40:
    print("\n⚠️  승률이 40% 미만입니다.")
    print("   손절을 확실히 하고, 평균 손실을 낮춰야 합니다.")

print("\n✅ 실거래 전 필수 단계:")
print("   1. 페이퍼 트레이딩 최소 3개월")
print("   2. Sharpe Ratio > 1.5 확인")
print("   3. 승률 > 50% 또는 평균 수익 > 평균 손실 * 2 확인")
print("   4. 최대 낙폭 < 10% 확인")

print("\n" + "=" * 80)
print("분석 완료!")
print("=" * 80)
