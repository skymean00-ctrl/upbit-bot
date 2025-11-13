"""FastAPI application exposing a simple dashboard for the trading bot."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from upbit_bot.config import Settings, load_settings
from upbit_bot.core import UpbitClient
from upbit_bot.data.performance_tracker import PerformanceTracker
from upbit_bot.data.trade_history import TradeHistoryStore
from upbit_bot.services import ExecutionEngine, PositionSizer, RiskConfig, RiskManager
from upbit_bot.strategies import get_strategy
from upbit_bot.utils import ConsoleNotifier, SlackNotifier, TelegramNotifier

from .controller import TradingController, TradingState

LOGGER = logging.getLogger(__name__)

# 사용 가능한 전략 목록 및 설명
STRATEGY_INFO = {
    "ma_crossover": {
        "name": "이동평균선 교차",
        "description": "단기 이동평균선이 장기 이동평균선을 상향 돌파하면 매수, 하향 돌파하면 매도하는 추세 추종 전략",
        "risk": "중간",
        "best_for": "추세가 명확한 시장",
    },
    "rsi_trend_filter": {
        "name": "RSI 트렌드 필터",
        "description": "RSI(상대강도지수)와 트렌드 필터를 결합한 전략. RSI가 과매수/과매도 구간에서 반전 신호를 포착",
        "risk": "낮음",
        "best_for": "변동성이 큰 시장",
    },
    "volatility_breakout": {
        "name": "변동성 돌파",
        "description": "전일 고가-저가 범위를 기준으로 변동성이 커질 때 돌파하면 매수하는 모멘텀 전략",
        "risk": "높음",
        "best_for": "강한 추세 시장",
    },
    "mixed_bb_rsi_ma": {
        "name": "볼린저밴드 + RSI + 이동평균",
        "description": "볼린저밴드, RSI, 이동평균을 조합한 다중 지표 전략으로 신호의 신뢰성을 높임",
        "risk": "중간",
        "best_for": "다양한 시장 상황",
    },
    "macd_crossover": {
        "name": "MACD 교차",
        "description": "MACD 선이 시그널 선을 교차할 때 매매 신호 발생. 골든크로스/데드크로스 활용",
        "risk": "중간",
        "best_for": "중장기 추세 시장",
    },
    "bb_squeeze": {
        "name": "볼린저밴드 스퀴즈",
        "description": "볼린저밴드가 수축(스퀴즈) 후 확장될 때 큰 움직임을 예상하고 진입하는 전략",
        "risk": "높음",
        "best_for": "변동성 증가 전 짧은 기간",
    },
    "support_resistance": {
        "name": "지지/저항선 돌파",
        "description": "주요 지지선 또는 저항선을 돌파할 때 추세 전환으로 보고 진입하는 전략",
        "risk": "중간",
        "best_for": "명확한 지지/저항이 있는 시장",
    },
    "volume_profile": {
        "name": "거래량 프로파일",
        "description": "거래량이 집중된 가격대(POC)를 기준으로 매매 결정. 거래량 급증 시 진입",
        "risk": "중간",
        "best_for": "거래량 분석이 중요한 시장",
    },
    "ai_market_analyzer": {
        "name": "🤖 AI 시장 분석",
        "description": "로컬 Ollama AI가 실시간 시장 데이터(이동평균, 변동성, 거래량)를 분석하여 신뢰도 기반 매매 신호 생성. 신경망 기반 인지로 동적 시장 판단",
        "risk": "낮음",
        "best_for": "모든 시장 상황",
    },
}

AVAILABLE_STRATEGIES = list(STRATEGY_INFO.keys())

# 사용 가능한 마켓 목록
AVAILABLE_MARKETS = [
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
]


def _build_strategy(
    settings: Settings,
    short_window: int | None = None,
    long_window: int | None = None,
) -> Any:
    components = None
    if settings.strategy_components:
        try:
            components = json.loads(settings.strategy_components)
        except json.JSONDecodeError as exc:  # noqa: BLE001
            LOGGER.warning("Failed to parse strategy components JSON: %s", exc)

    strategy_name = settings.strategy.name
    kwargs: dict[str, Any] = dict(settings.strategy.config or {})
    if components and strategy_name == "composite":
        kwargs["components"] = components
    else:
        if short_window is not None:
            kwargs["short_window"] = short_window
        if long_window is not None:
            kwargs["long_window"] = long_window
    return get_strategy(strategy_name, **kwargs)


def _build_notifiers(settings: Settings) -> list[Any]:
    notifiers: list[Any] = [ConsoleNotifier()]
    if settings.slack_webhook_url:
        notifiers.append(SlackNotifier(settings.slack_webhook_url))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers.append(TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id))
    return notifiers


def _build_balance_fetcher(client: UpbitClient) -> Any:
    def fetch_balance() -> float:
        try:
            accounts = client.get_accounts()
            for account in accounts:
                if account.get("currency") == "KRW":
                    return float(account.get("balance", 0.0))
        except Exception:  # noqa: BLE001
            return 0.0
        return 0.0

    return fetch_balance


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    client = UpbitClient(settings.access_key, settings.secret_key)
    strategy = _build_strategy(settings)
    risk_config = RiskConfig(
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_position_pct=settings.max_position_pct,
        max_open_positions=settings.max_open_positions,
        min_balance_krw=settings.min_balance_krw,
    )
    fetch_balance = _build_balance_fetcher(client)
    risk_manager = RiskManager(balance_fetcher=fetch_balance, config=risk_config)
    position_sizer = PositionSizer(balance_fetcher=fetch_balance, config=risk_config)

    app = FastAPI(title="Upbit Trading Bot Dashboard")
    
    trade_history_store = TradeHistoryStore()
    performance_tracker = PerformanceTracker()
    
    # AI 전략일 때는 1분 주기로 분석, 다른 전략은 5분 주기
    candle_unit = 1 if settings.strategy.name == "ai_market_analyzer" else 5
    poll_interval = 60 if settings.strategy.name == "ai_market_analyzer" else 300

    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market=settings.market,
        candle_unit=candle_unit,
        poll_interval=poll_interval,
        dry_run=True,
        risk_manager=risk_manager,
        position_sizer=position_sizer,
        notifiers=_build_notifiers(settings),
        trade_history_store=trade_history_store,
        order_amount_pct=settings.order_amount_pct,
    )
    controller = TradingController(engine=engine, client=client)
    app.state.controller = controller
    app.state.settings = settings
    app.state.trade_history_store = trade_history_store
    app.state.performance_tracker = performance_tracker

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:  # noqa: D401
        state = controller.get_state()
        account = controller.get_account_overview()
        html = _render_dashboard(state, account, STRATEGY_INFO, settings)
        return HTMLResponse(content=html)

    @app.post("/start")
    async def start_trading(mode: str = Form("dry")) -> RedirectResponse:
        controller.engine.dry_run = mode != "live"
        controller.start()
        return RedirectResponse(url="/", status_code=303)

    @app.post("/stop")
    async def stop_trading() -> RedirectResponse:
        controller.stop()
        return RedirectResponse(url="/", status_code=303)

    @app.get("/status")
    async def status() -> JSONResponse:
        return JSONResponse(controller.get_state().as_dict())

    @app.get("/balance")
    async def balance() -> JSONResponse:
        return JSONResponse(controller.get_account_overview())

    @app.get("/trades")
    async def get_trades(limit: int = 50) -> JSONResponse:
        """Get recent trades."""
        trade_history_store: TradeHistoryStore = app.state.trade_history_store
        trades = trade_history_store.get_recent_trades(limit=limit)
        return JSONResponse({"trades": trades})

    @app.get("/statistics")
    async def get_statistics(market: str | None = None) -> JSONResponse:
        """Get trading statistics."""
        trade_history_store: TradeHistoryStore = app.state.trade_history_store
        stats = trade_history_store.get_statistics(market=market)
        return JSONResponse(stats)

    @app.get("/performance")
    async def get_performance(strategy: str | None = None, market: str | None = None, days: int = 0) -> JSONResponse:
        """Get performance analytics."""
        performance_tracker: PerformanceTracker = app.state.performance_tracker
        stats = performance_tracker.get_statistics(strategy=strategy, market=market, days=days)
        daily_stats = performance_tracker.get_daily_stats(strategy=strategy)
        return JSONResponse({
            "summary": stats,
            "daily": daily_stats,
        })

    @app.post("/record-trade")
    async def record_trade(
        strategy: str = Form(...),
        market: str = Form(...),
        entry_price: float = Form(...),
        exit_price: float = Form(...),
        quantity: float = Form(...),
        duration_minutes: int = Form(0),
    ) -> JSONResponse:
        """Record a completed trade."""
        performance_tracker: PerformanceTracker = app.state.performance_tracker
        try:
            trade_id = performance_tracker.record_trade(
                strategy=strategy,
                market=market,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                trade_duration_minutes=duration_minutes,
            )
            return JSONResponse({
                "success": True,
                "trade_id": trade_id,
            })
        except Exception as e:  # noqa: BLE001
            return JSONResponse({
                "success": False,
                "error": str(e),
            }, status_code=400)

    @app.get("/positions")
    async def get_positions(market: str | None = None) -> JSONResponse:
        """Get open positions."""
        trade_history_store: TradeHistoryStore = app.state.trade_history_store
        positions = trade_history_store.get_open_positions(market=market)
        return JSONResponse({"positions": positions})

    @app.get("/strategies")
    async def get_strategies() -> JSONResponse:
        """사용 가능한 전략 목록 반환"""
        return JSONResponse({"strategies": AVAILABLE_STRATEGIES})

    @app.get("/markets")
    async def get_markets() -> JSONResponse:
        """사용 가능한 마켓 목록 반환"""
        return JSONResponse({"markets": AVAILABLE_MARKETS})

    @app.post("/update-settings")
    async def update_settings(
        strategy: Optional[str] = Form(None),
        market: Optional[str] = Form(None),
        order_amount_pct: Optional[float] = Form(None),
    ) -> JSONResponse:
        """설정 업데이트"""
        try:
            updates: dict[str, Any] = {}
            
            if strategy and strategy in AVAILABLE_STRATEGIES:
                # 전략 업데이트
                new_strategy = get_strategy(strategy, **settings.strategy.config or {})
                controller.engine.strategy = new_strategy
                
                # AI 전략일 때는 1분 주기, 다른 전략은 5분 주기
                if strategy == "ai_market_analyzer":
                    controller.engine.candle_unit = 1
                    controller.engine.poll_interval = 60
                else:
                    controller.engine.candle_unit = 5
                    controller.engine.poll_interval = 300
                
                updates["strategy"] = strategy
                LOGGER.info(f"Strategy updated to: {strategy}")
            
            if market and market in AVAILABLE_MARKETS:
                # 마켓 업데이트
                controller.engine.market = market
                updates["market"] = market
                LOGGER.info(f"Market updated to: {market}")
            
            if order_amount_pct is not None and 0.1 <= order_amount_pct <= 100:
                # 주문 금액 퍼센트 업데이트
                settings.order_amount_pct = order_amount_pct
                updates["order_amount_pct"] = order_amount_pct
                LOGGER.info(f"Order amount percentage updated to: {order_amount_pct}%")
            
            return JSONResponse({
                "success": True,
                "message": "Settings updated successfully",
                "updates": updates,
            })
        except Exception as e:  # noqa: BLE001
            LOGGER.error(f"Failed to update settings: {e}")
            return JSONResponse(
                {"success": False, "error": str(e)},
                status_code=400,
            )

    return app


def _render_dashboard(
    state: TradingState,
    account: dict[str, Any],
    strategy_info: dict[str, dict[str, str]],
    settings: Settings,
) -> str:
    running_status = "running" if state.running else "stopped"
    running_color = "green" if state.running else "red"
    dry_run_color = "blue" if state.dry_run else "orange"
    
    last_order_json = json.dumps(state.last_order, ensure_ascii=False, indent=2) if state.last_order else None
    
    accounts_data = account.get("accounts", [])
    krw_balance = account.get("krw_balance", 0.0)
    account_error = account.get("error")
    
    # 거래 가능한 코인만 필터링 및 현재 시세 기반 계산
    total_crypto_value = 0.0
    tradable_accounts = []
    
    for entry in accounts_data:
        currency = entry.get("currency", "")
        if currency == "KRW":
            continue
        
        balance = float(entry.get("balance", 0.0))
        if balance <= 0:
            continue
        
        # LUNC, APENFT, LUNA2 등 거래 불가능한 코인 필터링
        if currency in ["LUNC", "APENFT", "LUNA2", "DOGE", "SHIB"]:
            LOGGER.debug(f"Filtered out {currency} (non-tradable)")
            continue
        
        # 업비트에서 거래 가능한 마켓인지 확인
        market = f"KRW-{currency}"
        current_price = None
        
        try:
            ticker = client.get_ticker(market)
            if ticker:
                current_price = float(ticker.get("trade_price", 0.0))
                LOGGER.debug(f"Got ticker for {currency}: {current_price}")
        except Exception as e:
            # API 호출 실패 시 평균 매수가 사용
            LOGGER.warning(f"Failed to get ticker for {market}: {type(e).__name__}")
            current_price = None
        
        # 현재 시세가 없으면 평균 매수가 사용
        if current_price is None or current_price == 0:
            avg_price = float(entry.get("avg_buy_price", 0.0))
            if avg_price > 0:
                current_price = avg_price
                LOGGER.debug(f"Using avg_buy_price for {currency}: {avg_price}")
            else:
                # 평균 매수가도 없으면 표시 안함
                LOGGER.warning(f"No price available for {currency}, skipping")
                continue
        
        # 코인 정보 추가
        crypto_value = balance * current_price
        total_crypto_value += crypto_value
        entry_with_value = {**entry, "current_price": current_price, "crypto_value": crypto_value}
        tradable_accounts.append(entry_with_value)
        LOGGER.info(f"Added {currency}: balance={balance}, price={current_price}, value={crypto_value}")
    
    # 원본 accounts_data는 거래가능한 것만 사용
    accounts_data = tradable_accounts
    total_balance = krw_balance + total_crypto_value

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Upbit Trading Bot Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
            theme: {{
                extend: {{
                    colors: {{
                        primary: {{ DEFAULT: '#3b82f6', dark: '#2563eb' }},
                    }}
                }}
            }}
        }}
    </script>
    <style>
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
        .status-indicator {{
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }}
        .status-indicator.running {{
            background-color: #10b981;
            animation: pulse 2s infinite;
        }}
        .status-indicator.stopped {{
            background-color: #ef4444;
        }}
        .card {{
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1);
        }}
    </style>
</head>
<body class="bg-gray-50 dark:bg-gray-900 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-7xl">
        <!-- Header -->
        <div class="mb-8">
            <div class="flex items-center justify-between mb-4">
                <h1 class="text-4xl font-bold text-gray-900 dark:text-white">Upbit Trading Bot</h1>
                <div class="flex items-center space-x-4">
                    <div class="flex items-center px-4 py-2 rounded-lg bg-white dark:bg-gray-800 shadow">
                        <span class="status-indicator {running_status}"></span>
                        <span class="text-sm font-semibold text-gray-700 dark:text-gray-300">
                            {state.running and "RUNNING" or "STOPPED"}
                        </span>
                    </div>
                    <div class="px-4 py-2 rounded-lg {'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200' if state.dry_run else 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200'}">
                        <span class="text-sm font-semibold">{state.dry_run and "DRY-RUN" or "LIVE"}</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Balance Cards -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <div class="flex items-center justify-between">
                    <div>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">KRW Balance</p>
                        <p class="text-3xl font-bold text-gray-900 dark:text-white">{krw_balance:,.0f}</p>
                        <p class="text-sm text-gray-500 dark:text-gray-500 mt-1">KRW</p>
                    </div>
                    <div class="w-12 h-12 bg-green-100 dark:bg-green-900 rounded-full flex items-center justify-center">
                        <svg class="w-6 h-6 text-green-600 dark:text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                    </div>
                </div>
            </div>
            
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <div class="flex items-center justify-between">
                    <div>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">Crypto Value</p>
                        <p class="text-3xl font-bold text-gray-900 dark:text-white">{total_crypto_value:,.0f}</p>
                        <p class="text-sm text-gray-500 dark:text-gray-500 mt-1">KRW</p>
                    </div>
                    <div class="w-12 h-12 bg-purple-100 dark:bg-purple-900 rounded-full flex items-center justify-center">
                        <svg class="w-6 h-6 text-purple-600 dark:text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"></path>
                        </svg>
                    </div>
                </div>
            </div>
            
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <div class="flex items-center justify-between">
                    <div>
                        <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">Total Balance</p>
                        <p class="text-3xl font-bold text-gray-900 dark:text-white">{total_balance:,.0f}</p>
                        <p class="text-sm text-gray-500 dark:text-gray-500 mt-1">KRW</p>
                    </div>
                    <div class="w-12 h-12 bg-blue-100 dark:bg-blue-900 rounded-full flex items-center justify-center">
                        <svg class="w-6 h-6 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path>
                        </svg>
                    </div>
                </div>
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <!-- Settings Card -->
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <h2 class="text-xl font-bold text-gray-900 dark:text-white mb-4">Settings</h2>
                <form id="settings-form" method="post" action="/update-settings" class="space-y-4">
                    <div>
                        <label for="strategy-select" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            Strategy
                        </label>
                        <select 
                            id="strategy-select" 
                            name="strategy" 
                            class="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            onchange="updateStrategyDescription(this.value)"
                        >
                            {''.join([f'''
                            <option value="{strategy_key}" {'selected' if state.strategy == strategy_key else ''}>
                                {strategy_info.get(strategy_key, {}).get('name', strategy_key)}
                            </option>
                            ''' for strategy_key in AVAILABLE_STRATEGIES])}
                        </select>
                        <div id="strategy-description" class="mt-2 p-3 bg-gray-50 dark:bg-gray-900 rounded-lg">
                            <p class="text-xs text-gray-600 dark:text-gray-400 mb-1">
                                <strong>{strategy_info.get(state.strategy, {}).get('name', '알 수 없음')}</strong>
                            </p>
                            <p class="text-xs text-gray-500 dark:text-gray-500">
                                {strategy_info.get(state.strategy, {}).get('description', '설명 없음')}
                            </p>
                            <div class="mt-2 flex gap-2">
                                <span class="text-xs px-2 py-1 bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200 rounded">
                                    리스크: {strategy_info.get(state.strategy, {}).get('risk', 'N/A')}
                                </span>
                                <span class="text-xs px-2 py-1 bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 rounded">
                                    적합: {strategy_info.get(state.strategy, {}).get('best_for', 'N/A')}
                                </span>
                            </div>
                        </div>
                    </div>
                    <div>
                        <label for="market-select" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            Market
                        </label>
                        <select 
                            id="market-select" 
                            name="market" 
                            class="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        >
                            <option value="KRW-BTC" {'selected' if state.market == 'KRW-BTC' else ''}>KRW-BTC</option>
                            <option value="KRW-ETH" {'selected' if state.market == 'KRW-ETH' else ''}>KRW-ETH</option>
                            <option value="KRW-XRP" {'selected' if state.market == 'KRW-XRP' else ''}>KRW-XRP</option>
                            <option value="KRW-ADA" {'selected' if state.market == 'KRW-ADA' else ''}>KRW-ADA</option>
                            <option value="KRW-DOT" {'selected' if state.market == 'KRW-DOT' else ''}>KRW-DOT</option>
                            <option value="KRW-LINK" {'selected' if state.market == 'KRW-LINK' else ''}>KRW-LINK</option>
                            <option value="KRW-LTC" {'selected' if state.market == 'KRW-LTC' else ''}>KRW-LTC</option>
                            <option value="KRW-BCH" {'selected' if state.market == 'KRW-BCH' else ''}>KRW-BCH</option>
                            <option value="KRW-EOS" {'selected' if state.market == 'KRW-EOS' else ''}>KRW-EOS</option>
                            <option value="KRW-TRX" {'selected' if state.market == 'KRW-TRX' else ''}>KRW-TRX</option>
                        </select>
                    </div>
                    <div>
                        <label for="order-pct-input" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            💰 1건당 매수 퍼센트 (%)
                        </label>
                        <input 
                            type="number" 
                            id="order-pct-input" 
                            name="order_amount_pct" 
                            value="{settings.order_amount_pct}"
                            min="0.1" 
                            max="100"
                            step="0.1"
                            class="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            required
                        />
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">보유 원화의 %를 1건당 매수 금액으로 사용 (기본값: 3%)</p>
                    </div>
                    <button 
                        type="submit" 
                        class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-lg transition duration-200 shadow-md hover:shadow-lg"
                    >
                        Update Settings
                    </button>
                </form>
            </div>
            
            <!-- Status Card -->
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <h2 class="text-xl font-bold text-gray-900 dark:text-white mb-4">Status</h2>
                <div class="space-y-3">
                    <div class="flex justify-between items-center py-2 border-b border-gray-200 dark:border-gray-700">
                        <span class="text-gray-600 dark:text-gray-400">Current Market</span>
                        <span class="font-semibold text-gray-900 dark:text-white">{state.market}</span>
                    </div>
                    <div class="flex justify-between items-center py-2 border-b border-gray-200 dark:border-gray-700">
                        <span class="text-gray-600 dark:text-gray-400">Current Strategy</span>
                        <span class="font-semibold text-gray-900 dark:text-white">{state.strategy}</span>
                    </div>
                    <div class="flex justify-between items-center py-2 border-b border-gray-200 dark:border-gray-700">
                        <span class="text-gray-600 dark:text-gray-400">💰 Order Size</span>
                        <span class="font-semibold text-gray-900 dark:text-white">{settings.order_amount_pct}%</span>
                    </div>
                    <div class="flex justify-between items-center py-2 border-b border-gray-200 dark:border-gray-700">
                        <span class="text-gray-600 dark:text-gray-400">Last Signal</span>
                        <span class="font-semibold text-gray-900 dark:text-white">{state.last_signal or "N/A"}</span>
                    </div>
                    <div class="flex justify-between items-center py-2 border-b border-gray-200 dark:border-gray-700">
                        <span class="text-gray-600 dark:text-gray-400">Last Run</span>
                        <span class="font-semibold text-gray-900 dark:text-white text-sm">{state.last_run_at or "N/A"}</span>
                    </div>
                    {f'<div class="flex justify-between items-center py-2"><span class="text-red-600 dark:text-red-400">Last Error</span><span class="font-semibold text-red-600 dark:text-red-400 text-sm">{state.last_error}</span></div>' if state.last_error else ''}
                </div>
            </div>

            <!-- Controls Card -->
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <h2 class="text-xl font-bold text-gray-900 dark:text-white mb-4">🎮 서버 제어</h2>
                
                <!-- 서버 상태 표시 -->
                <div class="mb-6 p-4 rounded-lg bg-gradient-to-r from-blue-50 to-blue-100 dark:from-blue-900/20 dark:to-blue-800/20 border border-blue-200 dark:border-blue-800">
                    <div class="flex items-center justify-between">
    <div>
                            <p class="text-sm text-gray-600 dark:text-gray-400 mb-1">서버 상태</p>
                            <div class="flex items-center gap-2">
                                <div class="w-3 h-3 rounded-full bg-green-500 animate-pulse" id="server-status-dot"></div>
                                <span class="text-lg font-bold text-gray-900 dark:text-white" id="server-status-text">Running</span>
                            </div>
                        </div>
                        <div class="text-right">
                            <p class="text-xs text-gray-500 dark:text-gray-400 mb-1">거래 모드</p>
                            <span class="inline-block px-3 py-1 rounded-full text-sm font-semibold" id="trading-mode-badge">Dry-run</span>
                        </div>
                    </div>
    </div>
                
                <div class="space-y-4">
                    <form method="post" action="/start" class="space-y-3">
                        <div>
                            <label for="mode" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">📊 거래 모드 선택</label>
                            <select id="mode" name="mode" class="w-full px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                                <option value="dry" {'selected' if state.dry_run else ''}>🟢 Dry-run (시뮬레이션 - 실제 거래 없음)</option>
                                <option value="live" {'selected' if not state.dry_run else ''}>🔴 Live (실제 거래 - 주의!)</option>
                </select>
                        </div>
                        <button type="submit" class="w-full bg-green-600 hover:bg-green-700 active:bg-green-800 text-white font-bold py-3 px-6 rounded-lg transition duration-200 shadow-md hover:shadow-lg flex items-center justify-center gap-2">
                            <span>▶️</span>
                            <span>서버 시작</span>
                        </button>
            </form>
            <form method="post" action="/stop">
                        <button type="submit" class="w-full bg-red-600 hover:bg-red-700 active:bg-red-800 text-white font-bold py-3 px-6 rounded-lg transition duration-200 shadow-md hover:shadow-lg flex items-center justify-center gap-2">
                            <span>⏹️</span>
                            <span>서버 중지</span>
                        </button>
            </form>
                    
                    <!-- 추가 정보 -->
                    <div class="grid grid-cols-2 gap-2 pt-4 border-t border-gray-200 dark:border-gray-700 text-xs">
                        <div>
                            <p class="text-gray-600 dark:text-gray-400">마지막 실행</p>
                            <p class="font-semibold text-gray-900 dark:text-white" id="last-run-time">-</p>
                        </div>
                        <div>
                            <p class="text-gray-600 dark:text-gray-400">마지막 신호</p>
                            <p class="font-semibold text-gray-900 dark:text-white" id="last-signal-badge">HOLD</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Account & Orders -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <!-- Account Snapshot -->
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <h2 class="text-xl font-bold text-gray-900 dark:text-white mb-4">Account Snapshot</h2>
                {f'''
                <div class="mb-4 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
                    <div class="flex items-start">
                        <svg class="w-5 h-5 text-red-600 dark:text-red-400 mt-0.5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        <div>
                            <p class="text-sm font-semibold text-red-600 dark:text-red-400 mb-1">인증 오류</p>
                            <p class="text-xs text-red-600 dark:text-red-400">
                                {'API 키가 유효하지 않습니다. .env 파일의 UPBIT_ACCESS_KEY와 UPBIT_SECRET_KEY를 확인해주세요.' if '401' in str(account_error) or 'invalid_access_key' in str(account_error) else str(account_error)}
                            </p>
                        </div>
                    </div>
                </div>
                ''' if account_error else ''}
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                <thead>
                            <tr class="border-b border-gray-200 dark:border-gray-700">
                                <th class="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Currency</th>
                                <th class="text-right py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Balance</th>
                                <th class="text-right py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Current Price</th>
                                <th class="text-right py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Valuation (KRW)</th>
                            </tr>
                </thead>
                <tbody>
                            {''.join([f'''
                            <tr class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700">
                                <td class="py-3 px-4 font-medium text-gray-900 dark:text-white">{entry.get('currency', '?')}</td>
                                <td class="py-3 px-4 text-right text-gray-900 dark:text-white">{float(entry.get('balance', 0)):,.8f}</td>
                                <td class="py-3 px-4 text-right text-gray-600 dark:text-gray-400">{f"{float(entry.get('current_price', 0)):,.0f}" if entry.get('current_price') and float(entry.get('current_price', 0)) > 0 else '-'}</td>
                                <td class="py-3 px-4 text-right font-medium text-green-600 dark:text-green-400">{f"{float(entry.get('crypto_value', 0)):,.0f}" if entry.get('crypto_value') else '-'}</td>
                            </tr>''' for entry in accounts_data]) if accounts_data else '<tr><td colspan="4" class="py-4 px-4 text-center text-gray-500 dark:text-gray-400">보유한 거래 가능한 코인이 없습니다</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Latest Order -->
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <h2 class="text-xl font-bold text-gray-900 dark:text-white mb-4">Latest Order</h2>
                {f'''
                <div class="bg-gray-50 dark:bg-gray-900 rounded-lg p-4 overflow-x-auto">
                    <pre class="text-xs text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{last_order_json}</pre>
                </div>
                ''' if last_order_json else '<div class="text-center py-8 text-gray-500 dark:text-gray-400">No orders yet</div>'}
            </div>
        </div>

        <!-- Trade History & Statistics -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <!-- Trade History -->
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <h2 class="text-xl font-bold text-gray-900 dark:text-white mb-4">거래 내역</h2>
                <div id="trade-history" class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead>
                            <tr class="border-b border-gray-200 dark:border-gray-700">
                                <th class="text-left py-2 px-2 font-semibold text-gray-700 dark:text-gray-300 text-xs">시간</th>
                                <th class="text-left py-2 px-2 font-semibold text-gray-700 dark:text-gray-300 text-xs">전략</th>
                                <th class="text-left py-2 px-2 font-semibold text-gray-700 dark:text-gray-300 text-xs">신호</th>
                                <th class="text-right py-2 px-2 font-semibold text-gray-700 dark:text-gray-300 text-xs">가격</th>
                                <th class="text-right py-2 px-2 font-semibold text-gray-700 dark:text-gray-300 text-xs">수량</th>
                            </tr>
                        </thead>
                        <tbody id="trade-history-body">
                            <tr><td colspan="5" class="py-4 text-center text-gray-500 dark:text-gray-400 text-sm">로딩 중...</td></tr>
                </tbody>
            </table>
                </div>
            </div>

            <!-- Statistics -->
            <div class="card bg-white dark:bg-gray-800 rounded-xl shadow-lg p-6">
                <h2 class="text-xl font-bold text-gray-900 dark:text-white mb-4">📊 성과 분석</h2>
                <div id="statistics" class="space-y-3">
                    <!-- 기본 통계 -->
                    <div class="grid grid-cols-2 gap-4 mb-4">
                        <div class="bg-gray-50 dark:bg-gray-700 rounded p-3">
                            <p class="text-xs text-gray-600 dark:text-gray-400">총 거래</p>
                            <p class="text-2xl font-bold text-gray-900 dark:text-white" id="stat-total-trades">0</p>
                        </div>
                        <div class="bg-gray-50 dark:bg-gray-700 rounded p-3">
                            <p class="text-xs text-gray-600 dark:text-gray-400">승률</p>
                            <p class="text-2xl font-bold text-green-600 dark:text-green-400" id="stat-win-rate">0%</p>
                        </div>
                        <div class="bg-gray-50 dark:bg-gray-700 rounded p-3">
                            <p class="text-xs text-gray-600 dark:text-gray-400">총 수익/손실</p>
                            <p class="text-2xl font-bold text-gray-900 dark:text-white" id="stat-total-pnl">0 KRW</p>
                        </div>
                        <div class="bg-gray-50 dark:bg-gray-700 rounded p-3">
                            <p class="text-xs text-gray-600 dark:text-gray-400">평균 수익률</p>
                            <p class="text-2xl font-bold text-gray-900 dark:text-white" id="stat-avg-profit-pct">0%</p>
                        </div>
                    </div>
                    
                    <!-- 상세 분석 -->
                    <div class="border-t border-gray-200 dark:border-gray-700 pt-4">
                        <p class="text-sm font-semibold text-gray-900 dark:text-white mb-3">📈 상세 지표</p>
                        <div class="grid grid-cols-2 gap-2 text-xs">
                            <div>
                                <span class="text-gray-600 dark:text-gray-400">승리 거래</span>
                                <p class="font-bold text-green-600 dark:text-green-400" id="stat-winning-trades">0</p>
                            </div>
                            <div>
                                <span class="text-gray-600 dark:text-gray-400">손실 거래</span>
                                <p class="font-bold text-red-600 dark:text-red-400" id="stat-losing-trades">0</p>
                            </div>
                            <div>
                                <span class="text-gray-600 dark:text-gray-400">평균 수익</span>
                                <p class="font-bold text-green-600 dark:text-green-400" id="stat-avg-win">0 KRW</p>
                            </div>
                            <div>
                                <span class="text-gray-600 dark:text-gray-400">평균 손실</span>
                                <p class="font-bold text-red-600 dark:text-red-400" id="stat-avg-loss">0 KRW</p>
                            </div>
                            <div>
                                <span class="text-gray-600 dark:text-gray-400">수익 팩터</span>
                                <p class="font-bold text-gray-900 dark:text-white" id="stat-profit-factor">0.00</p>
                            </div>
                            <div>
                                <span class="text-gray-600 dark:text-gray-400">최대낙폭 (MDD)</span>
                                <p class="font-bold text-red-600 dark:text-red-400" id="stat-max-dd">0 KRW</p>
                            </div>
                            <div class="col-span-2">
                                <span class="text-gray-600 dark:text-gray-400">Sharpe Ratio</span>
                                <p class="font-bold text-gray-900 dark:text-white" id="stat-sharpe">0.00</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Auto-refresh indicator -->
        <div class="mt-6 text-center text-sm text-gray-500 dark:text-gray-400">
            <p>자동 새로고침: <span id="refresh-counter">15</span>초</p>
        </div>
    </div>

    <script>
        const STRATEGY_INFO = {json.dumps({k: v for k, v in strategy_info.items()}, ensure_ascii=False)};
        
        // 전략 설명 업데이트
        function updateStrategyDescription(strategyKey) {{
            const info = STRATEGY_INFO[strategyKey];
            if (info) {{
                const descDiv = document.getElementById('strategy-description');
                descDiv.innerHTML = `
                    <p class="text-xs text-gray-600 dark:text-gray-400 mb-1">
                        <strong>${{info.name}}</strong>
                    </p>
                    <p class="text-xs text-gray-500 dark:text-gray-500">
                        ${{info.description}}
                    </p>
                    <div class="mt-2 flex gap-2">
                        <span class="text-xs px-2 py-1 bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200 rounded">
                            리스크: ${{info.risk}}
                        </span>
                        <span class="text-xs px-2 py-1 bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 rounded">
                            적합: ${{info.best_for}}
                        </span>
                    </div>
                `;
            }}
        }}

        // 거래 내역 로드
        async function loadTradeHistory() {{
            try {{
                const response = await fetch('/trades?limit=10');
                const data = await response.json();
                const tbody = document.getElementById('trade-history-body');
                
                if (data.trades && data.trades.length > 0) {{
                    tbody.innerHTML = data.trades.map(trade => {{
                        const date = new Date(trade.timestamp);
                        const timeStr = date.toLocaleString('ko-KR', {{ hour: '2-digit', minute: '2-digit' }});
                        const strategyName = STRATEGY_INFO[trade.strategy]?.name || trade.strategy;
                        const sideColor = trade.side === 'buy' ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400';
                        return `
                            <tr class="border-b border-gray-100 dark:border-gray-700">
                                <td class="py-2 px-2 text-xs text-gray-600 dark:text-gray-400">${{timeStr}}</td>
                                <td class="py-2 px-2 text-xs text-gray-900 dark:text-white">${{strategyName}}</td>
                                <td class="py-2 px-2 text-xs ${{sideColor}}">${{trade.side === 'buy' ? '매수' : '매도'}}</td>
                                <td class="py-2 px-2 text-xs text-right text-gray-900 dark:text-white">${{trade.price ? trade.price.toLocaleString() : '-'}}</td>
                                <td class="py-2 px-2 text-xs text-right text-gray-600 dark:text-gray-400">${{trade.volume ? trade.volume.toFixed(4) : '-'}}</td>
                            </tr>
                        `;
                    }}).join('');
                }} else {{
                    tbody.innerHTML = '<tr><td colspan="5" class="py-4 text-center text-gray-500 dark:text-gray-400 text-sm">거래 내역이 없습니다.</td></tr>';
                }}
            }} catch (error) {{
                console.error('Failed to load trade history:', error);
            }}
        }}

        // 통계 로드
        async function loadStatistics() {{
            try {{
                const response = await fetch('/statistics');
                const stats = await response.json();
                
                document.getElementById('stat-total-trades').textContent = stats.total_trades || 0;
                document.getElementById('stat-closed-positions').textContent = stats.closed_positions || 0;
                document.getElementById('stat-win-rate').textContent = (stats.win_rate || 0).toFixed(1) + '%';
                document.getElementById('stat-total-pnl').textContent = (stats.total_pnl || 0).toLocaleString() + ' KRW';
                document.getElementById('stat-avg-pnl').textContent = (stats.avg_pnl_pct || 0).toFixed(2) + '%';
            }} catch (error) {{
                console.error('Failed to load statistics:', error);
            }}
        }}

        // 설정 업데이트 폼 처리
        const settingsForm = document.getElementById('settings-form');
        if (settingsForm) {{
            settingsForm.addEventListener('submit', async (e) => {{
                e.preventDefault();
                const formData = new FormData(settingsForm);
                
                try {{
                    const response = await fetch('/update-settings', {{
                        method: 'POST',
                        body: formData,
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        // 성공 메시지 표시
                        const messageDiv = document.createElement('div');
                        messageDiv.className = 'mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg';
                        messageDiv.innerHTML = `
                            <p class="text-sm text-green-600 dark:text-green-400">설정이 성공적으로 업데이트되었습니다.</p>
                        `;
                        settingsForm.insertBefore(messageDiv, settingsForm.firstChild);
                        
                        // 3초 후 메시지 제거 및 페이지 새로고침
                        setTimeout(() => {{
                            messageDiv.remove();
                            window.location.reload();
                        }}, 2000);
                    }} else {{
                        // 오류 메시지 표시
                        alert('설정 업데이트 실패: ' + (result.error || '알 수 없는 오류'));
                    }}
                }} catch (error) {{
                    console.error('Failed to update settings:', error);
                    alert('설정 업데이트 중 오류가 발생했습니다.');
                }}
            }});
        }}

        // 초기 로드
        loadTradeHistory();
        loadStatistics();
        
        // 주기적 업데이트 (30초마다)
        setInterval(() => {{
            loadTradeHistory();
            loadStatistics();
        }}, 30000);

        // Auto-refresh 기능
        let refreshCounter = 15;
        const counterElement = document.getElementById('refresh-counter');
        
        setInterval(() => {{
            refreshCounter--;
            if (counterElement) {{
                counterElement.textContent = refreshCounter;
            }}
            if (refreshCounter <= 0) {{
                refreshCounter = 15;
                // 페이지 새로고침
                window.location.reload();
            }}
        }}, 1000);

        // 실시간 업데이트 (5초마다)
        setInterval(() => {{
            fetch('/status')
                .then(response => response.json())
                .then(data => {{
                    // 서버 상태 업데이트
                    const statusDot = document.getElementById('server-status-dot');
                    const statusText = document.getElementById('server-status-text');
                    const modeBadge = document.getElementById('trading-mode-badge');
                    const lastRunTime = document.getElementById('last-run-time');
                    const lastSignalBadge = document.getElementById('last-signal-badge');
                    
                    if (statusDot && statusText) {{
                        if (data.running) {{
                            statusDot.classList.add('bg-green-500', 'animate-pulse');
                            statusDot.classList.remove('bg-red-500');
                            statusText.textContent = '🟢 Running';
                            statusText.classList.add('text-green-600', 'dark:text-green-400');
                            statusText.classList.remove('text-red-600', 'dark:text-red-400');
                        }} else {{
                            statusDot.classList.remove('bg-green-500', 'animate-pulse');
                            statusDot.classList.add('bg-red-500');
                            statusText.textContent = '🔴 Stopped';
                            statusText.classList.remove('text-green-600', 'dark:text-green-400');
                            statusText.classList.add('text-red-600', 'dark:text-red-400');
                        }}
                    }}
                    
                    // 거래 모드 업데이트
                    if (modeBadge) {{
                        if (data.dry_run) {{
                            modeBadge.textContent = '🟢 Dry-run (시뮬레이션)';
                            modeBadge.classList.add('bg-blue-100', 'dark:bg-blue-900/30', 'text-blue-800', 'dark:text-blue-300');
                            modeBadge.classList.remove('bg-red-100', 'dark:bg-red-900/30', 'text-red-800', 'dark:text-red-300');
                        }} else {{
                            modeBadge.textContent = '🔴 Live (실제 거래)';
                            modeBadge.classList.remove('bg-blue-100', 'dark:bg-blue-900/30', 'text-blue-800', 'dark:text-blue-300');
                            modeBadge.classList.add('bg-red-100', 'dark:bg-red-900/30', 'text-red-800', 'dark:text-red-300');
                        }}
                    }}
                    
                    // 마지막 실행 시간 업데이트
                    if (lastRunTime && data.last_run_at) {{
                        const runTime = new Date(data.last_run_at);
                        const now = new Date();
                        const diff = Math.round((now - runTime) / 1000);
                        if (diff < 60) {{
                            lastRunTime.textContent = diff + '초 전';
                        }} else if (diff < 3600) {{
                            lastRunTime.textContent = Math.round(diff / 60) + '분 전';
                        }} else {{
                            lastRunTime.textContent = runTime.toLocaleTimeString('ko-KR', {{hour: '2-digit', minute: '2-digit'}});
                        }}
                    }}
                    
                    // 마지막 신호 업데이트
                    if (lastSignalBadge && data.last_signal) {{
                        const signal = data.last_signal.toUpperCase();
                        if (signal === 'BUY') {{
                            lastSignalBadge.textContent = '🟢 BUY';
                            lastSignalBadge.className = 'font-semibold text-green-600 dark:text-green-400';
                        }} else if (signal === 'SELL') {{
                            lastSignalBadge.textContent = '🔴 SELL';
                            lastSignalBadge.className = 'font-semibold text-red-600 dark:text-red-400';
                        }} else {{
                            lastSignalBadge.textContent = '⚪ HOLD';
                            lastSignalBadge.className = 'font-semibold text-gray-600 dark:text-gray-400';
                        }}
                    }}
                }})
                .catch(err => console.error('Failed to fetch status:', err));
        }}, 3000);  // 3초마다 상태 업데이트
    </script>
</body>
</html>"""
    return html
