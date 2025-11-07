#!/usr/bin/env python3
"""Run the Upbit trading bot in live mode."""

from __future__ import annotations

import argparse
import json
import logging
import time

from upbit_bot.config import load_settings
from upbit_bot.core import UpbitClient
from upbit_bot.services import ExecutionEngine
from upbit_bot.services.risk import PositionSizer, RiskConfig, RiskManager
from upbit_bot.strategies import CombinedStrategy, MovingAverageCrossoverStrategy, MixedBBRSIMAStrategy, RSITrendFilterStrategy, VolatilityBreakoutStrategy
from upbit_bot.utils import ConsoleNoti

logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser(description="Run the Upbit trading bot.")
    parser.add_argument("--verbose", "-v", action="count", default=0, help="Increase verbosity (e.g., -v, -vv)")
    return parser.parse_args()

def setup_logging(verbose_level: int):
    level = logging.INFO
    if verbose_level == 1:
        level = logging.DEBUG
    elif verbose_level >= 2:
        level = logging.DEBUG # Or more detailed if needed

    logging.basicConfig(level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

def main():
    args = parse_args()
    setup_logging(args.verbose)

    logger.info("Upbit 봇 시작...")

    settings = load_settings()

    # === 전략 로딩 부분 시작 ===
    sub_strategies_configs = settings.get("combined_strategies", [])
    if not sub_strategies_configs:
        # 기존 단일 전략 로직 (호환성을 위해 유지)
        strategy_name = settings.strategy.name
        strategy_params = settings.strategy.config
        
        if strategy_name == "ma_crossover":
            strategy = MovingAverageCrossoverStrategy(**strategy_params)
        elif strategy_name == "mixed_bb_rsi_ma":
            strategy = MixedBBRSIMAStrategy(**strategy_params)
        elif strategy_name == "rsi_trend_filter":
            strategy = RSITrendFilterStrategy(**strategy_params)
        elif strategy_name == "volatility_breakout":
            strategy = VolatilityBreakoutStrategy(**strategy_params)
        else:
            logger.error(f"알 수 없는 단일 전략: {strategy_name}")
            return
        logger.info(f"단일 전략 '{strategy.name}' 사용.")
    else:
        sub_strategies = []
        for strat_config in sub_strategies_configs:
            strategy_name = strat_config.get("name")
            strategy_params = strat_config.get("config", {})
            
            if strategy_name == "ma_crossover":
                sub_strategies.append(MovingAverageCrossoverStrategy(**strategy_params))
            elif strategy_name == "mixed_bb_rsi_ma":
                sub_strategies.append(MixedBBRSIMAStrategy(**strategy_params))
            elif strategy_name == "rsi_trend_filter":
                sub_strategies.append(RSITrendFilterStrategy(**strategy_params))
            elif strategy_name == "volatility_breakout":
                sub_strategies.append(VolatilityBreakoutStrategy(**strategy_params))
            else:
                logger.warning(f"알 수 없는 하위 전략: {strategy_name}. 이 전략은 무시됩니다.")
        
        if not sub_strategies:
            logger.error("CombinedStrategy에 추가할 유효한 하위 전략이 없습니다. 봇을 종료합니다.")
            return
        
        strategy = CombinedStrategy(sub_strategies=sub_strategies)
        logger.info(f"복합 전략 '{strategy.name}' 사용. 하위 전략: {[s.name for s in sub_strategies]}")
    # === 전략 로딩 부분 끝 ===

    client = UpbitClient(settings.upbit_api_key, settings.upbit_secret_key)
    risk_config = RiskConfig(**settings.risk)
    risk_manager = RiskManager(risk_config, PositionSizer(client))
    
    # ExecutionEngine에 ConsoleNoti를 전달하여 알림을 받을 수 있도록 합니다.
    notifier = ConsoleNoti()
    engine = ExecutionEngine(client, strategy, risk_manager, notifier)

    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("봇이 사용자 요청으로 종료됩니다.")
    except Exception as e:
        logger.exception(f"봇 실행 중 예상치 못한 오류 발생: {e}")
    finally:
        logger.info("Upbit 봇 종료.")

if __name__ == "__main__":
    main()
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
from upbit_bot.strategies import get_strategy, CombinedStrategy, MovingAverageCrossoverStrategy, MixedBBRSIMAStrategy, RSITrendFilterStrategy, VolatilityBreakoutStrategy
from upbit_bot.utils import ConsoleNoti


def main():
    args = parse_args()
    setup_logging(args.verbose)

    logger.info("Upbit 봇 시작...")

    settings = load_settings()
    # === 전략 로딩 부분 시작 ===
    sub_strategies_configs = settings.get("combined_strategies", [])
    if not sub_strategies_configs:
        # 기존 단일 전략 로직 (호환성을 위해 유지)
        # get_strategy 함수는 이제 사용되지 않으므로, 각 전략 클래스를 직접 호출하도록 변경
        strategy_name = settings.strategy.name
        strategy_params = settings.strategy.config
        
        if strategy_name == "ma_crossover":
            strategy = MovingAverageCrossoverStrategy(**strategy_params)
        elif strategy_name == "mixed_bb_rsi_ma":
            strategy = MixedBBRSIMAStrategy(**strategy_params)
        elif strategy_name == "rsi_trend_filter":
            strategy = RSITrendFilterStrategy(**strategy_params)
        elif strategy_name == "volatility_breakout":
            strategy = VolatilityBreakoutStrategy(**strategy_params)
        else:
            logger.error(f"알 수 없는 단일 전략: {strategy_name}")
            return
        logger.info(f"단일 전략 '{strategy.name}' 사용.")
    else:
        sub_strategies = []
        for strat_config in sub_strategies_configs:
            strategy_name = strat_config.get("name")
            strategy_params = strat_config.get("config", {})
            
            if strategy_name == "ma_crossover":
                sub_strategies.append(MovingAverageCrossoverStrategy(**strategy_params))
            elif strategy_name == "mixed_bb_rsi_ma":
                sub_strategies.append(MixedBBRSIMAStrategy(**strategy_params))
            elif strategy_name == "rsi_trend_filter":
                sub_strategies.append(RSITrendFilterStrategy(**strategy_params))
            elif strategy_name == "volatility_breakout":
                sub_strategies.append(VolatilityBreakoutStrategy(**strategy_params))
            else:
                logger.warning(f"알 수 없는 하위 전략: {strategy_name}. 이 전략은 무시됩니다.")
        
        if not sub_strategies:
            logger.error("CombinedStrategy에 추가할 유효한 하위 전략이 없습니다. 봇을 종료합니다.")
            return
        
        strategy = CombinedStrategy(sub_strategies=sub_strategies)
        logger.info(f"복합 전략 '{strategy.name}' 사용. 하위 전략: {[s.name for s in sub_strategies]}")
    # === 전략 로딩 부분 끝 ===

    # 나머지 main 함수 로직 (기존 코드를 여기에 삽입해야 함)
    # 예시: client = UpbitClient(settings.upbit_api_key, settings.upbit_secret_key)
    #       risk_manager = RiskManager(RiskConfig(**settings.risk), PositionSizer(client))
    #       engine = ExecutionEngine(client, strategy, risk_manager, ConsoleNoti())
    #       engine.run()fier, SlackNotifier, TelegramNotifier, configure_logging


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
