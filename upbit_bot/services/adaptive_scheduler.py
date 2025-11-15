"""적응형 스캔 스케줄러."""

from __future__ import annotations

import logging
import statistics
import time
from typing import Any

LOGGER = logging.getLogger(__name__)


class AdaptiveScanScheduler:
    """서버 부하에 따라 스캔 간격을 자동으로 조정하는 스케줄러."""

    def __init__(self, initial_interval: int = 180) -> None:
        """
        적응형 스케줄러 초기화.

        Args:
            initial_interval: 초기 스캔 간격 (초, 기본값: 180초 = 3분)
        """
        self.interval = initial_interval
        self.scan_durations: list[float] = []
        self.max_history = 10  # 최근 10개 스캔 기록만 유지

        LOGGER.info(f"적응형 스케줄러 초기화: 초기 간격 {initial_interval}초")

    def record_scan_duration(self, duration: float) -> None:
        """
        스캔 소요 시간 기록.

        Args:
            duration: 스캔 소요 시간 (초)
        """
        self.scan_durations.append(duration)

        # 최대 기록 수 제한
        if len(self.scan_durations) > self.max_history:
            self.scan_durations.pop(0)

        LOGGER.debug(f"스캔 소요 시간 기록: {duration:.1f}초 (기록 수: {len(self.scan_durations)})")

    def calculate_next_interval(self) -> int:
        """
        다음 스캔 간격 계산.

        최근 스캔 소요 시간의 평균을 기반으로 여유 시간 30%를 확보한 간격을 계산.

        Returns:
            다음 스캔 간격 (초, 최소 120초, 최대 600초)
        """
        if not self.scan_durations:
            return self.interval

        # 평균 소요 시간 계산
        avg_duration = statistics.mean(self.scan_durations)

        # 여유 시간 30% 확보 (avg_duration / 0.7)
        target_interval = avg_duration / 0.7

        # 최소 120초, 최대 600초로 제한
        calculated = int(max(120, min(600, target_interval)))

        LOGGER.info(
            f"간격 계산: 평균 {avg_duration:.1f}초 → 목표 {target_interval:.1f}초 → "
            f"최종 {calculated}초 (기록 수: {len(self.scan_durations)})"
        )

        return calculated

    def get_current_interval(self) -> int:
        """
        현재 설정된 스캔 간격 반환.

        Returns:
            현재 스캔 간격 (초)
        """
        return self.interval

    def update_interval(self) -> None:
        """다음 간격으로 업데이트."""
        old_interval = self.interval
        self.interval = self.calculate_next_interval()

        if old_interval != self.interval:
            LOGGER.info(
                f"스캔 간격 변경: {old_interval}초 → {self.interval}초 "
                f"(변화: {self.interval - old_interval:+d}초)"
            )

