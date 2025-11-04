#!/usr/bin/env python3
"""Run the Upbit trading bot in live mode."""

from __future__ import annotations

import argparse
import json
import logging

from upbit_bot.config import load_settings
from upbit_bot.core import UpbitClient
from upbit_bot.services import ExecutionEngine
from upbit_bot.services.risk import PositionSizer, RiskConfig, RiskManager
from upbit_bot.strategies import get_strategy
from upbit_bot.utils import ConsoleNotifier, SlackNotifier, TelegramNotifier, configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Upbit automated trading bot.")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation cycle.")
    parser.add_argument("--env-file", help="Path to .env file with credentials.")
    parser.add_argument("--candle-unit", type=int, default=1, help="Candle unit in minutes.")
    parser.add_argument("--poll-interval", type=int, default=30, help="Wait time between cycles.")
    parser.add_argument("--short-window", type=int, default=14, help="Short moving average window.")
    parser.add_argument("--long-window", type=int, default=37, help="Long moving average window.")
    parser.add_argument("--atr-threshold", type=float, default=0.0, help="ATR filter threshold.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute real orders instead of dry-run.",
    )
    parser.add_argument(
        "--order-amount",
        type=float,
        default=None,
        help="Order sizing amount (KRW for buys, asset units for sells). Required when --live.",
    )
    parser.add_argument("--components", help="JSON list of component strategies (composite mode).")
    parser.add_argument(
        "--max-daily-loss",
        type=float,
        default=None,
        help="Override max daily loss pct.",
    )
    parser.add_argument(
        "--max-position-pct",
        type=float,
        default=None,
        help="Override max position size pct.",
    )
    parser.add_argument(
        "--max-open-positions",
        type=int,
        default=None,
        help="Override max simultaneous positions.",
    )
    parser.add_argument(
        "--min-balance",
        type=float,
        default=None,
        help="Minimum KRW balance required to trade.",
    )
    parser.add_argument(
        "--min-order-amount",
        type=float,
        default=5000.0,
        help="Minimum KRW amount per order.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging()
    settings = load_settings(env_path=args.env_file)

    components_str = args.components or settings.strategy_components
    components = json.loads(components_str) if components_str else None

    strategy_kwargs = {}
    if settings.strategy != "composite":
        strategy_kwargs.update(
            {
                "short_window": args.short_window,
                "long_window": args.long_window,
                "atr_threshold": args.atr_threshold,
            }
        )
    if components and settings.strategy != "composite":
        logging.getLogger(__name__).warning(
            "Components provided but strategy is not 'composite'. Ignoring components.",
        )
        components = None

    if components:
        strategy_kwargs["components"] = components

    strategy = get_strategy(settings.strategy, **strategy_kwargs)
    client = UpbitClient(settings.access_key, settings.secret_key)
    risk_config = RiskConfig(
        max_daily_loss_pct=args.max_daily_loss or settings.max_daily_loss_pct,
        max_position_pct=args.max_position_pct or settings.max_position_pct,
        max_open_positions=args.max_open_positions or settings.max_open_positions,
        min_balance_krw=args.min_balance or settings.min_balance_krw,
    )

    def fetch_balance() -> float:
        try:
            accounts = client.get_accounts()
            for account in accounts:
                if account.get("currency") == "KRW":
                    return float(account.get("balance", 0.0))
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).error("Failed to fetch balance: %s", exc)
        return 0.0

    risk_manager = RiskManager(balance_fetcher=fetch_balance, config=risk_config)
    position_sizer = PositionSizer(balance_fetcher=fetch_balance, config=risk_config)

    notifiers = [ConsoleNotifier()]
    if settings.slack_webhook_url:
        notifiers.append(SlackNotifier(settings.slack_webhook_url))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers.append(TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id))

    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market=settings.market,
        candle_unit=args.candle_unit,
        poll_interval=args.poll_interval,
        dry_run=not args.live,
        order_amount=args.order_amount,
        risk_manager=risk_manager,
        position_sizer=position_sizer,
        notifiers=notifiers,
        min_order_amount=args.min_order_amount,
    )

    if args.once:
        engine.run_once()
    else:
        engine.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
