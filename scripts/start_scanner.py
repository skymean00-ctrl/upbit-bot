"""스캐너 서비스 런처 스크립트."""

import logging
import os
import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/scanner.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

from upbit_bot.services.scanner_service import ContinuousScannerService

LOGGER = logging.getLogger(__name__)


def main() -> None:
    """메인 함수."""
    try:
        scanner_url = os.getenv("OLLAMA_SCANNER_URL", "http://localhost:11434")
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        LOGGER.info(f"스캐너 URL: {scanner_url}")
        LOGGER.info(f"Redis URL: {redis_url}")

        service = ContinuousScannerService(ollama_url=scanner_url, redis_url=redis_url)

        try:
            service.run()
        except KeyboardInterrupt:
            LOGGER.info("\n스캐너 종료 중...")
            service.stop()

    except Exception as e:
        LOGGER.error(f"스캐너 시작 실패: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

