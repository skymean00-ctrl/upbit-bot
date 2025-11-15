"""연속 스캔 서비스 - 서버에서 24/7 실행되는 독립 스캐너."""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any

from upbit_bot.config.settings import Settings
from upbit_bot.core import UpbitClient

from .adaptive_scheduler import AdaptiveScanScheduler
from .coin_scanner import CoinScanner

try:
    from upbit_bot.database.redis_store import RedisScanStore
except ImportError:
    RedisScanStore = None  # type: ignore[assignment, misc]

LOGGER = logging.getLogger(__name__)


class ContinuousScannerService:
    """서버에서 24/7 실행되는 연속 스캔 서비스."""

    def __init__(self, ollama_url: str, redis_url: str) -> None:
        """
        연속 스캔 서비스 초기화.

        Args:
            ollama_url: Ollama 서버 URL (예: http://localhost:11434)
            redis_url: Redis 서버 URL (예: redis://localhost:6379/0)
        """
        if RedisScanStore is None:
            raise ImportError("redis 모듈이 설치되지 않았습니다. pip install redis를 실행하세요.")

        settings = Settings()
        self.scanner = CoinScanner(ollama_url=ollama_url, model="qwen2.5:1.5b")
        self.store = RedisScanStore(redis_url)

        initial_interval = int(os.getenv("SCANNER_INTERVAL_SECONDS", "180"))
        self.scheduler = AdaptiveScanScheduler(initial_interval=initial_interval)

        # UpbitClient 초기화
        self.client = UpbitClient(
            access_key=settings.access_key,
            secret_key=settings.secret_key,
        )

        self.running = False
        self.scan_count = 0
        self.start_time = time.time()

        LOGGER.info(
            f"연속 스캔 서비스 초기화 완료 (Ollama: {ollama_url}, Redis: {redis_url})"
        )

    def get_top_markets_by_volume(self, top_n: int = 50) -> list[str]:
        """
        24시간 거래량 상위 N개 마켓 조회.

        Args:
            top_n: 상위 N개 (기본값: 50)

        Returns:
            마켓 이름 리스트 (예: ["KRW-BTC", "KRW-ETH", ...])
        """
        try:
            # UpbitClient의 get_top_volume_markets 메서드 활용
            markets = self.client.get_top_volume_markets(limit=top_n)
            LOGGER.info(f"거래량 상위 {len(markets)}개 마켓 조회 완료")
            return markets
        except Exception as e:
            LOGGER.error(f"거래량 상위 마켓 조회 실패: {e}")
            # 기본 코인 목록 반환
            return [
                "KRW-BTC",
                "KRW-ETH",
                "KRW-XRP",
                "KRW-ADA",
                "KRW-DOT",
                "KRW-LINK",
                "KRW-LTC",
                "KRW-BCH",
                "KRW-EOS",
                "KRW-TRX",
            ][:top_n]

    def scan_cycle(self) -> None:
        """1회 스캔 사이클 실행."""
        start = time.time()
        current_interval = self.scheduler.get_current_interval()

        LOGGER.info(f"=" * 60)
        LOGGER.info(f"스캔 사이클 시작 (간격: {current_interval}초)")

        try:
            # 1. 거래량 상위 50개 조회
            top_n = int(os.getenv("SCANNER_TOP_N_COINS", "50"))
            markets = self.get_top_markets_by_volume(top_n=top_n)
            LOGGER.info(f"스캔 대상: {len(markets)}개 코인")

            # 2. 티커 정보 가져오기 (거래량, 가격)
            tickers_map: dict[str, dict[str, Any]] = {}
            try:
                tickers = self.client.get_tickers(markets)
                for ticker in tickers:
                    market = ticker.get("market")
                    if market:
                        tickers_map[market] = ticker
            except Exception as e:
                LOGGER.warning(f"티커 정보 조회 실패: {e}")

            # 3. 캔들 데이터 가져오기
            markets_data: dict[str, list[Any]] = {}
            for market in markets:
                try:
                    # 1분봉 60개 가져오기
                    candles = self.client.get_candles(market, unit=1, count=60)
                    # Candle 객체로 변환
                    from upbit_bot.strategies import Candle

                    candle_objects = []
                    for c in candles:
                        candle_objects.append(
                            Candle(
                                timestamp=c["timestamp"],
                                open=float(c["opening_price"]),
                                high=float(c["high_price"]),
                                low=float(c["low_price"]),
                                close=float(c["trade_price"]),
                                volume=float(c["candle_acc_trade_volume"]),
                            )
                        )
                    markets_data[market] = candle_objects

                except Exception as e:
                    LOGGER.warning(f"{market} 캔들 데이터 조회 실패: {e}")

            LOGGER.info(f"캔들 데이터 조회 완료: {len(markets_data)}개 코인")

            # 4. 병렬 스캔 (max_workers=10)
            max_workers = int(os.getenv("SCANNER_MAX_WORKERS", "10"))
            scan_results = self.scanner.scan_markets(markets_data, max_workers=max_workers)

            LOGGER.info(f"스캔 완료: {len(scan_results)}개 코인 분석됨")

            # 5. Redis 저장
            saved_count = 0
            for market, result in scan_results.items():
                try:
                    # 추가 메타데이터 (거래량, 가격)
                    if market in tickers_map:
                        ticker = tickers_map[market]
                        result["volume_24h"] = float(
                            ticker.get("acc_trade_volume_24h", 0)
                        )
                        result["price"] = float(ticker.get("trade_price", 0))

                    self.store.save_scan_result(market, result)
                    saved_count += 1

                except Exception as e:
                    LOGGER.error(f"{market} 저장 실패: {e}")

            # 6. 메트릭 기록
            duration = time.time() - start
            self.scheduler.record_scan_duration(duration)
            self.scheduler.update_interval()
            self.scan_count += 1

            LOGGER.info(
                f"스캔 사이클 완료: {saved_count}/{len(scan_results)}개 저장, "
                f"소요 {duration:.1f}초, 다음 간격 {self.scheduler.get_current_interval()}초"
            )
            LOGGER.info(f"=" * 60)

            # 7. 자동 재시작 체크 (1000회 또는 24시간)
            if self.should_restart():
                LOGGER.info("자동 재시작 조건 충족")
                self.graceful_restart()

        except Exception as e:
            duration = time.time() - start
            LOGGER.error(f"스캔 사이클 오류 (소요 {duration:.1f}초): {e}", exc_info=True)

    def should_restart(self) -> bool:
        """
        재시작 필요 여부 확인.

        Returns:
            True: 1000회 스캔 또는 24시간 경과 시
        """
        return self.scan_count >= 1000 or (time.time() - self.start_time) > 86400

    def graceful_restart(self) -> None:
        """우아한 재시작 (프로세스 재실행)."""
        LOGGER.info("우아한 재시작 시작...")
        self.stop()
        # 프로세스 재실행
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def run(self) -> None:
        """메인 루프 실행."""
        self.running = True
        LOGGER.info("연속 스캔 서비스 시작")

        try:
            while self.running:
                try:
                    self.scan_cycle()

                    # 다음 스캔까지 대기
                    interval = self.scheduler.get_current_interval()
                    LOGGER.debug(f"다음 스캔까지 {interval}초 대기...")

                    for _ in range(interval):
                        if not self.running:
                            break
                        time.sleep(1)

                except KeyboardInterrupt:
                    LOGGER.info("사용자 중단 신호 수신")
                    break
                except Exception as e:
                    LOGGER.error(f"메인 루프 오류: {e}", exc_info=True)
                    time.sleep(60)  # 오류 시 1분 대기 후 재시도

        finally:
            LOGGER.info("연속 스캔 서비스 종료")
            self.running = False

    def stop(self) -> None:
        """서비스 중지."""
        LOGGER.info("서비스 중지 신호 전송")
        self.running = False

