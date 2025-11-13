#!/bin/bash
# Upbit Bot Web Server 시작 스크립트
# Tailscale을 통한 외부 접속을 위해 0.0.0.0으로 바인딩

cd "$(dirname "$0")/.." || exit 1

# 가상환경 활성화
source venv/bin/activate

# 기본 포트는 8080, 필요시 --port 옵션으로 변경 가능
# --host 0.0.0.0은 Tailscale을 통한 외부 접속을 위해 필요
python scripts/web_dashboard.py --host 0.0.0.0 --port "${PORT:-8080}"

