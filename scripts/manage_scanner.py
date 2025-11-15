"""스캐너 서비스 관리 도구."""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

from upbit_bot.services.scanner_service import ContinuousScannerService

LOGGER = logging.getLogger(__name__)


def start_scanner() -> None:
    """스캐너 시작."""
    try:
        scanner_url = os.getenv("OLLAMA_SCANNER_URL", "http://localhost:11434")
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        LOGGER.info(f"스캐너 시작 (Ollama: {scanner_url}, Redis: {redis_url})")

        service = ContinuousScannerService(ollama_url=scanner_url, redis_url=redis_url)

        try:
            service.run()
        except KeyboardInterrupt:
            LOGGER.info("\n스캐너 종료 중...")
            service.stop()

    except Exception as e:
        LOGGER.error(f"스캐너 시작 실패: {e}", exc_info=True)
        sys.exit(1)


def stop_scanner() -> None:
    """스캐너 중지."""
    import signal

    # PID 파일 확인 (간단한 구현)
    LOGGER.info("스캐너 프로세스 찾는 중...")
    os.system("pkill -f 'scripts.start_scanner'")
    LOGGER.info("스캐너 중지 신호 전송")


def scanner_status() -> None:
    """스캐너 상태 확인."""
    import requests

    try:
        # Redis에서 직접 확인
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        try:
            from upbit_bot.database.redis_store import RedisScanStore

            store = RedisScanStore(redis_url)
            results = store.get_scan_results(max_age_seconds=600)

            if not results:
                print("❌ 상태: 데이터 없음 (최근 10분 이내 스캔 결과 없음)")
                return

            latest = max(results, key=lambda x: x.get("timestamp", ""))
            latest_timestamp_str = latest.get("timestamp", "")

            if latest_timestamp_str:
                from datetime import UTC, datetime

                latest_timestamp = datetime.fromisoformat(
                    latest_timestamp_str.replace("Z", "+00:00")
                )
                age = (datetime.now(UTC) - latest_timestamp).total_seconds()

                status = "정상" if age < 300 else "지연됨"
                print(f"✅ 상태: {status}")
                print(f"📊 스캔된 코인: {len(results)}개")
                print(f"⏰ 마지막 스캔: {int(age)}초 전")
                print(f"📅 타임스탬프: {latest_timestamp_str}")

                if age > 300:
                    print(f"⚠️  경고: 마지막 스캔이 {int(age/60)}분 전입니다")

        except ImportError:
            print("❌ Redis 모듈을 찾을 수 없습니다")

    except Exception as e:
        LOGGER.error(f"상태 확인 실패: {e}", exc_info=True)


def test_scan(coins: int = 5) -> None:
    """테스트 스캔 실행."""
    try:
        scanner_url = os.getenv("OLLAMA_SCANNER_URL", "http://localhost:11434")
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        LOGGER.info(f"테스트 스캔 시작 ({coins}개 코인)")

        service = ContinuousScannerService(ollama_url=scanner_url, redis_url=redis_url)

        # 임시로 top_n 설정
        os.environ["SCANNER_TOP_N_COINS"] = str(coins)

        # 한 번만 스캔
        start = time.time()
        service.scan_cycle()
        duration = time.time() - start

        LOGGER.info(f"테스트 스캔 완료 (소요: {duration:.1f}초)")

    except Exception as e:
        LOGGER.error(f"테스트 스캔 실패: {e}", exc_info=True)
        sys.exit(1)


def main() -> None:
    """메인 함수."""
    parser = argparse.ArgumentParser(description="스캐너 서비스 관리 도구")
    subparsers = parser.add_subparsers(dest="command", help="명령어")

    subparsers.add_parser("start", help="스캐너 시작")
    subparsers.add_parser("stop", help="스캐너 중지")
    subparsers.add_parser("status", help="스캐너 상태 확인")

    test_parser = subparsers.add_parser("test", help="테스트 스캔 실행")
    test_parser.add_argument(
        "--coins", type=int, default=5, help="스캔할 코인 수 (기본값: 5)"
    )

    args = parser.parse_args()

    if args.command == "start":
        start_scanner()
    elif args.command == "stop":
        stop_scanner()
    elif args.command == "status":
        scanner_status()
    elif args.command == "test":
        test_scan(coins=args.coins)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

