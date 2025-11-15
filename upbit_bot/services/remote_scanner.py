"""원격 스캐너 클라이언트 - 서버에서 스캔 결과를 가져오는 HTTP 클라이언트."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)


class RemoteScannerClient:
    """서버의 스캔 결과를 HTTP로 가져오는 클라이언트."""

    def __init__(self, api_url: str, max_age_seconds: int = 120) -> None:
        """
        원격 스캐너 클라이언트 초기화.

        Args:
            api_url: 서버 API URL (예: http://SERVER_IP:8080/api/scan-results)
            max_age_seconds: 최대 데이터 나이 (초, 기본값: 120초 = 2분)
        """
        self.api_url = api_url
        self.max_age_seconds = max_age_seconds
        self.cache: list[dict[str, Any]] = []
        self.cache_time: float | None = None

        LOGGER.info(f"원격 스캐너 클라이언트 초기화: {api_url} (최대 나이: {max_age_seconds}초)")

    def fetch_scan_results(
        self, limit: int = 50, use_cache: bool = True
    ) -> list[dict[str, Any]]:
        """
        서버에서 스캔 결과 가져오기.

        Args:
            limit: 가져올 결과 수 (기본값: 50)
            use_cache: 캐시 사용 여부 (기본값: True, 30초 캐시)

        Returns:
            스캔 결과 리스트
        """
        # 캐시 확인 (30초)
        if use_cache and self.cache and self.cache_time:
            cache_age = time.time() - self.cache_time
            if cache_age < 30:
                LOGGER.debug(f"캐시된 스캔 결과 사용 (나이: {cache_age:.1f}초)")
                return self.cache

        # HTTP 요청 (재시도 3회)
        for attempt in range(3):
            try:
                max_age_minutes = self.max_age_seconds // 60
                url = f"{self.api_url}?limit={limit}&max_age_minutes={max_age_minutes}"

                LOGGER.debug(f"스캔 결과 조회 시도 {attempt + 1}/3: {url}")

                response = requests.get(url, timeout=10)

                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])

                LOGGER.info(
                    f"서버에서 {len(results)}개 스캔 결과 가져옴 "
                    f"(요청: {limit}개, 최대 나이: {max_age_minutes}분)"
                )

                # 캐시 업데이트
                self.cache = results
                self.cache_time = time.time()

                return results

            except requests.exceptions.Timeout:
                LOGGER.warning(
                    f"스캔 결과 조회 시도 {attempt + 1}/3: 타임아웃 (10초)"
                )
                if attempt < 2:
                    time.sleep(2**attempt)
                else:
                    raise

            except requests.exceptions.RequestException as e:
                LOGGER.warning(f"스캔 결과 조회 시도 {attempt + 1}/3 실패: {e}")
                if attempt < 2:
                    time.sleep(2**attempt)
                else:
                    LOGGER.error("모든 재시도 실패")
                    raise

        return []

    def get_fresh_results(self) -> list[dict[str, Any]]:
        """
        신선한 데이터만 필터링 (2분 이내).

        Returns:
            신선한 스캔 결과 리스트 (max_age_seconds 이내)
        """
        results = self.fetch_scan_results()

        fresh = [
            r
            for r in results
            if r.get("age_seconds", 999) <= self.max_age_seconds
        ]

        LOGGER.info(
            f"신선한 데이터: {len(fresh)}/{len(results)}개 "
            f"(최대 {self.max_age_seconds}초, "
            f"필터링: {len(results) - len(fresh)}개 제외)"
        )

        return fresh

