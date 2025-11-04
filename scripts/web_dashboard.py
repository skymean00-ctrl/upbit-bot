#!/usr/bin/env python3
"""Run the FastAPI web dashboard for the Upbit trading bot."""

from __future__ import annotations

import argparse

import uvicorn

from upbit_bot.config import load_settings
from upbit_bot.web import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upbit bot web dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Host address to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (development only).",
    )
    parser.add_argument("--env-file", help="Optional .env file path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings(env_path=args.env_file) if args.env_file else None
    app = create_app(settings=settings)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
