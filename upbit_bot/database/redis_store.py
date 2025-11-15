"""Redis storage layer for scan results."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import redis

LOGGER = logging.getLogger(__name__)


class RedisScanStore:
    """Redis 기반 스캔 결과 저장소."""

    def __init__(self, redis_url: str) -> None:
        """
        Redis 스토어 초기화.

        Args:
            redis_url: Redis 연결 URL (예: redis://localhost:6379/0)
        """
        try:
            self.redis = redis.from_url(redis_url, decode_responses=True)
            # 연결 테스트
            self.redis.ping()
            LOGGER.info(f"Redis 연결 성공: {redis_url}")
        except Exception as e:
            LOGGER.error(f"Redis 연결 실패: {e}")
            raise

    def save_scan_result(self, market: str, result: dict[str, Any]) -> None:
        """
        스캔 결과 저장 (TTL 10분).

        Args:
            market: 마켓 이름 (예: "KRW-BTC")
            result: 스캔 결과 딕셔너리
        """
        key = f"scan:results:{market}"

        # 타임스탬프 추가
        result["timestamp"] = datetime.now(UTC).isoformat()

        # 문자열로 변환 (Redis는 문자열만 저장 가능)
        redis_data: dict[str, str] = {}
        for k, v in result.items():
            if isinstance(v, (int, float, bool)):
                redis_data[k] = str(v)
            elif isinstance(v, str):
                redis_data[k] = v
            elif isinstance(v, dict):
                import json

                redis_data[k] = json.dumps(v, ensure_ascii=False)
            elif v is None:
                redis_data[k] = ""
            else:
                redis_data[k] = str(v)

        try:
            self.redis.hset(key, mapping=redis_data)
            self.redis.expire(key, 600)  # 10분 TTL

            # Pub/Sub 알림
            self.redis.publish("scan:updates", market)

            LOGGER.debug(f"스캔 결과 저장: {market}")
        except Exception as e:
            LOGGER.error(f"스캔 결과 저장 실패 ({market}): {e}")
            raise

    def get_scan_results(self, max_age_seconds: int = 300) -> list[dict[str, Any]]:
        """
        최신 스캔 결과 조회.

        Args:
            max_age_seconds: 최대 나이 (초). 이보다 오래된 결과는 제외

        Returns:
            스캔 결과 리스트 (점수 내림차순 정렬)
        """
        results: list[dict[str, Any]] = []
        now = datetime.now(UTC)

        try:
            # 모든 스캔 결과 키 조회
            keys = self.redis.keys("scan:results:*")

            for key in keys:
                try:
                    data = self.redis.hgetall(key)

                    if not data:
                        continue

                    # 타임스탬프 파싱
                    timestamp_str = data.get("timestamp", "")
                    if not timestamp_str:
                        continue

                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                    age = (now - timestamp).total_seconds()

                    # 최대 나이 체크
                    if age > max_age_seconds:
                        continue

                    # 딕셔너리 복원
                    result: dict[str, Any] = {}
                    for k, v in data.items():
                        # JSON 문자열 복원 시도
                        if v.startswith("{") or v.startswith("["):
                            try:
                                import json

                                result[k] = json.loads(v)
                            except Exception:
                                result[k] = v
                        # 숫자 변환 시도
                        elif v.replace(".", "", 1).replace("-", "", 1).isdigit():
                            try:
                                if "." in v:
                                    result[k] = float(v)
                                else:
                                    result[k] = int(v)
                            except Exception:
                                result[k] = v
                        # 불린 변환
                        elif v.lower() in ("true", "false"):
                            result[k] = v.lower() == "true"
                        # 빈 문자열은 None
                        elif v == "":
                            result[k] = None
                        else:
                            result[k] = v

                    # 메타데이터 추가
                    result["age_seconds"] = age
                    result["freshness"] = self._calculate_freshness(age)
                    result["market"] = key.split(":")[-1]  # 마켓 이름 추출

                    results.append(result)

                except Exception as e:
                    LOGGER.warning(f"스캔 결과 파싱 실패 ({key}): {e}")
                    continue

            # 점수 기준 내림차순 정렬
            results.sort(key=lambda x: float(x.get("score", 0)), reverse=True)

            LOGGER.debug(f"스캔 결과 조회: {len(results)}개 (max_age={max_age_seconds}초)")

        except Exception as e:
            LOGGER.error(f"스캔 결과 조회 실패: {e}")
            raise

        return results

    def _calculate_freshness(self, age_seconds: float) -> str:
        """
        데이터 신선도 계산.

        Args:
            age_seconds: 데이터 나이 (초)

        Returns:
            "fresh" (0-60초), "stale" (60-120초), "expired" (120초+)
        """
        if age_seconds < 60:
            return "fresh"
        elif age_seconds < 120:
            return "stale"
        else:
            return "expired"

