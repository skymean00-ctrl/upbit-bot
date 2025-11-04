#!/usr/bin/env python3
"""Simple connectivity check using the Upbit account endpoint."""

from __future__ import annotations

import json
import sys

from upbit_bot.config import load_settings
from upbit_bot.core import UpbitAPIError, UpbitClient
from upbit_bot.utils.logging import configure_logging


def main() -> int:
    configure_logging()
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load settings: {exc}", file=sys.stderr)
        return 1

    client = UpbitClient(settings.access_key, settings.secret_key)
    try:
        accounts = client.get_accounts()
    except UpbitAPIError as exc:
        print(f"Upbit API error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(accounts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
