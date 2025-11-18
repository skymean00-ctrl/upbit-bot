"""FastAPI application exposing a simple dashboard for the trading bot."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from threading import Thread
from typing import Any, AsyncGenerator, Optional

import requests
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from upbit_bot.config import Settings, load_settings
from upbit_bot.core import UpbitClient
from upbit_bot.data.performance_tracker import PerformanceTracker
from upbit_bot.data.trade_history import TradeHistoryStore
from upbit_bot.services import ExecutionEngine, PositionSizer, RiskConfig, RiskManager
from upbit_bot.services.ollama_client import OllamaClient, OllamaError
from upbit_bot.strategies import Candle, get_strategy
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
    "ai_market_analyzer_high_risk": {
        "name": "🚀 AI 시장 분석 - 고위험",
        "description": "AI 시장 분석을 베이스로 한 고위험 고수익 전략. 낮은 신뢰도 임계값(0.4)으로 더 많은 매매 신호 생성, 빠른 진입/퇴출로 단기 수익 추구. 공격적 매매 원칙 적용",
        "risk": "높음",
        "best_for": "변동성이 높고 공격적 매매를 원하는 경우",
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
    
    # 기본 전략을 AI 시장 분석으로 설정
    if settings.strategy.name != "ai_market_analyzer":
        from upbit_bot.config.settings import StrategyConfig
        settings.strategy = StrategyConfig(name="ai_market_analyzer", config={})

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
    is_ai_strategy = settings.strategy.name in ("ai_market_analyzer", "ai_market_analyzer_high_risk")
    candle_unit = 1 if is_ai_strategy else 5
    poll_interval = 60 if is_ai_strategy else 300

    # 거래 모드 기본값을 live (dry_run=False)로 설정
    # 환경변수 DRY_RUN이 True면 dry-run 모드, 그 외에는 live 모드
    import os
    default_dry_run = os.getenv("DRY_RUN", "false").lower() == "true"

    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market=settings.market,
        candle_unit=candle_unit,
        poll_interval=poll_interval,
        dry_run=default_dry_run,  # 기본값: live 모드 (False)
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
    
    # AI 전략 백그라운드 분석 태스크 (서버 시작 없이도 주기적으로 분석)
    def background_ai_analysis() -> None:
        """백그라운드에서 주기적으로 AI 분석 실행 (서버 시작 여부와 무관)"""
        import time
        while True:
            try:
                # AI 전략인 경우에만 실행
                current_settings = load_settings()
                if current_settings.strategy.name in ("ai_market_analyzer", "ai_market_analyzer_high_risk"):
                    # engine 참조를 지역 변수로 가져오기 (스레드 안전)
                    try:
                        # app.state에 안전하게 접근
                        controller = app.state.controller
                        engine = controller.engine
                        
                        if engine and engine.strategy.name in ("ai_market_analyzer", "ai_market_analyzer_high_risk"):
                            try:
                                # 여러 코인 분석
                                selected_market, signal, candles = engine._analyze_multiple_markets()
                                LOGGER.info(f"Background AI analysis: {selected_market} -> {signal.value}")
                                
                                # 분석 결과 저장
                                if hasattr(engine.strategy, 'last_analysis') and engine.strategy.last_analysis:
                                    engine.last_ai_analysis = engine.strategy.last_analysis.copy()
                                    engine.last_ai_analysis['selected_market'] = selected_market
                                    engine.last_ai_analysis['timestamp'] = datetime.now(UTC).isoformat()
                                    
                                    # signal을 문자열로 변환
                                    signal_obj = engine.last_ai_analysis.get('signal')
                                    if signal_obj is not None:
                                        if hasattr(signal_obj, 'value'):
                                            engine.last_ai_analysis['signal'] = signal_obj.value
                                        elif hasattr(signal_obj, 'name'):
                                            engine.last_ai_analysis['signal'] = signal_obj.name
                                        else:
                                            engine.last_ai_analysis['signal'] = str(signal_obj)
                            except Exception as e:
                                LOGGER.warning(f"Background AI analysis failed: {e}", exc_info=True)
                    except AttributeError:
                        # app.state가 아직 준비되지 않았을 수 있음
                        LOGGER.debug("App state not ready yet for AI analysis")
                # 60초마다 실행 (AI 전략 주기와 동일)
                time.sleep(60)
            except Exception as e:
                LOGGER.error(f"Background AI analysis error: {e}", exc_info=True)
                time.sleep(60)
    
    # 백그라운드 태스크 시작 (서버 시작과 무관하게)
    # 함수 내부에서 app 객체를 참조하므로, 함수 정의 후에 시작
    # 주의: 전역 변수 대신 app.state에 저장하여 스레드 관리
    def start_background_ai_analysis():
        # 이미 시작되었는지 확인
        if not hasattr(app.state, '_ai_analysis_thread'):
            ai_analysis_thread = Thread(target=background_ai_analysis, daemon=True)
            ai_analysis_thread.start()
            app.state._ai_analysis_thread = ai_analysis_thread
            LOGGER.info("Background AI analysis task started")
        elif not app.state._ai_analysis_thread.is_alive():
            # 스레드가 죽었으면 재시작
            ai_analysis_thread = Thread(target=background_ai_analysis, daemon=True)
            ai_analysis_thread.start()
            app.state._ai_analysis_thread = ai_analysis_thread
            LOGGER.info("Background AI analysis task restarted")
    
    # 앱 시작 시 백그라운드 태스크 시작
    start_background_ai_analysis()

    # CSP 헤더 미들웨어 추가
    class CSPMiddleware(BaseHTTPMiddleware):
        """Content Security Policy 헤더를 모든 응답에 추가"""
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            # CSP 헤더 추가 (unsafe-eval 허용 - Tailwind CDN 및 동적 코드 실행 필요)
            csp_policy = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
                "https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
                "connect-src 'self' ws: wss: http: https:; "
                "img-src 'self' data: https:; "
                "font-src 'self' data: https:; "
                "frame-src 'none'; "
                "object-src 'none';"
            )
            response.headers["Content-Security-Policy"] = csp_policy
            return response

    app.add_middleware(CSPMiddleware)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:  # noqa: D401
        state = controller.get_state()
        account = controller.get_account_overview()
        html = _render_dashboard(state, account, STRATEGY_INFO, settings)
        return HTMLResponse(content=html)

    @app.post("/start")
    async def start_trading(mode: str = Form("dry")) -> JSONResponse:
        """서버 시작"""
        try:
            controller.engine.dry_run = mode != "live"
            controller.start()
            LOGGER.info(f"서버 시작됨 (mode={mode}, dry_run={controller.engine.dry_run})")
            return JSONResponse({
                "success": True,
                "message": "서버가 시작되었습니다.",
                "mode": mode,
                "dry_run": controller.engine.dry_run,
                "running": controller.engine.is_running(),
            })
        except Exception as e:
            LOGGER.error(f"서버 시작 실패: {e}")
            return JSONResponse({
                "success": False,
                "error": str(e),
            }, status_code=500)

    @app.post("/stop")
    async def stop_trading() -> JSONResponse:
        """서버 중지"""
        try:
            controller.stop()
            LOGGER.info("서버 중지됨")
            return JSONResponse({
                "success": True,
                "message": "서버가 중지되었습니다.",
                "running": controller.engine.is_running(),
            })
        except Exception as e:
            LOGGER.error(f"서버 중지 실패: {e}")
            return JSONResponse({
                "success": False,
                "error": str(e),
            }, status_code=500)

    @app.post("/force-exit")
    async def force_exit() -> JSONResponse:
        """강제 탈출: 모든 거래 가능한 코인을 시장가로 매도."""
        try:
            result = controller.engine.force_exit_all()
            return JSONResponse({
                "success": True,
                "result": result,
            })
        except Exception as e:  # noqa: BLE001
            LOGGER.error(f"Force exit error: {e}")
            return JSONResponse({
                "success": False,
                "error": str(e),
            }, status_code=500)

    @app.get("/status")
    async def status() -> JSONResponse:
        return JSONResponse(controller.get_state().as_dict())

    @app.get("/balance")
    async def balance() -> JSONResponse:
        return JSONResponse(controller.get_account_overview())

    @app.get("/api/holdings")
    async def get_holdings() -> JSONResponse:
        """보유 코인 목록 조회 API - 실시간 시세 페이지용"""
        try:
            account_overview = controller.get_account_overview()
            accounts = account_overview.get("accounts", [])
            
            # KRW 제외하고, 잔액이 있는 코인만 필터링
            coins = []
            non_tradable = {"LUNC", "APENFT", "LUNA2", "DOGE", "SHIB"}
            
            for account in accounts:
                currency = account.get("currency", "")
                balance = float(account.get("balance", 0.0))
                
                # KRW 제외, 잔액 없는 코인 제외, 거래 불가능한 코인 제외
                if currency != "KRW" and balance > 0 and currency not in non_tradable:
                    coins.append(currency)
            
            return JSONResponse({"coins": coins})
        except Exception as e:  # noqa: BLE001
            LOGGER.error(f"Failed to get holdings: {e}")
            return JSONResponse({"coins": [], "error": str(e)}, status_code=500)

    @app.get("/api/stream")
    async def stream_updates() -> StreamingResponse:
        """Server-Sent Events stream for real-time updates."""
        async def generate() -> AsyncGenerator[str, None]:
            # 마지막 거래 내역 동기화 시간 추적 (app.state에 저장하여 공유)
            SYNC_INTERVAL = 300  # 5분 (초)
            
            while True:
                try:
                    # 주기적 거래 내역 동기화 (5분마다)
                    current_time = datetime.now(UTC)
                    last_sync_time = getattr(app.state, '_last_sync_time', None)
                    should_sync = False
                    
                    if last_sync_time is None:
                        should_sync = True
                        app.state._last_sync_time = current_time
                    else:
                        time_diff = (current_time - last_sync_time).total_seconds()
                        if time_diff >= SYNC_INTERVAL:
                            should_sync = True
                            app.state._last_sync_time = current_time
                    
                    if should_sync:
                        # 백그라운드에서 동기화 실행 (중복 실행 방지)
                        sync_lock = getattr(app.state, '_sync_lock', None)
                        if sync_lock is None:
                            import threading
                            app.state._sync_lock = threading.Lock()
                            sync_lock = app.state._sync_lock
                        
                        if not sync_lock.locked():
                            def sync_trades_background():
                                with sync_lock:
                                    try:
                                        # app.state에서 직접 가져오기 (클로저 스코프 문제 해결)
                                        controller = app.state.controller
                                        trade_history_store: TradeHistoryStore = app.state.trade_history_store
                                        result = trade_history_store.sync_external_trades(
                                            client=controller.engine.client,
                                            days=7,
                                        )
                                        if result.get("success"):
                                            synced = result.get("synced", 0)
                                            if synced > 0:
                                                LOGGER.info(f"자동 거래 내역 동기화 완료: {synced}개 동기화")
                                    except Exception as e:
                                        LOGGER.warning(f"자동 거래 내역 동기화 실패: {e}")
                            
                            sync_thread = Thread(target=sync_trades_background, daemon=True)
                            sync_thread.start()
                    
                    # Get current account overview
                    controller = app.state.controller
                    account = controller.get_account_overview()
                    state = controller.get_state().as_dict()
                    
                    # 계정 데이터에 암호화폐 총 가치 계산 추가
                    accounts_data = account.get("accounts", [])
                    krw_balance = account.get("krw_balance", 0.0)
                    total_crypto_value = 0.0
                    
                    # 거래 가능한 코인만 계산
                    try:
                        for entry in accounts_data:
                            currency = entry.get("currency", "")
                            if currency == "KRW":
                                continue
                            balance = float(entry.get("balance", 0.0))
                            if balance <= 0:
                                continue
                            if currency in ["LUNC", "APENFT", "LUNA2", "DOGE", "SHIB"]:
                                continue
                            
                            market = f"KRW-{currency}"
                            try:
                                ticker = controller.engine.client.get_ticker(market)
                                if ticker:
                                    current_price = float(ticker.get("trade_price", 0.0))
                                    total_crypto_value += balance * current_price
                            except Exception:
                                avg_price = float(entry.get("avg_buy_price", 0.0))
                                if avg_price > 0:
                                    total_crypto_value += balance * avg_price
                    except Exception:
                        pass
                    
                    account["total_crypto_value"] = total_crypto_value
                    account["total_balance"] = krw_balance + total_crypto_value
                    
                    # Ollama 상태는 항상 가져오기 (서버 시작/중지와 상관없이)
                    ollama_status_data = controller.get_ollama_status()
                    
                    # AI 전략이면 항상 AI 분석 결과 가져오기 (SSE 스트림에서 직접 실행)
                    ai_analysis = None
                    ai_strategies = ["ai_market_analyzer", "ai_market_analyzer_high_risk"]
                    if state.get("strategy") in ai_strategies:
                        # 먼저 기존 분석 결과 확인
                        ai_analysis = controller.get_ai_analysis()
                        
                        # 분석 결과가 없거나 오래된 경우 (60초 이상 경과) 즉시 분석 실행
                        should_analyze = False
                        analysis_in_progress = False
                        
                        if not ai_analysis:
                            should_analyze = True
                        elif ai_analysis.get("timestamp"):
                            try:
                                last_analysis_time = datetime.fromisoformat(ai_analysis["timestamp"].replace("Z", "+00:00"))
                                time_diff = (datetime.now(UTC) - last_analysis_time).total_seconds()
                                if time_diff > 60:  # 60초 이상 경과하면 재분석
                                    should_analyze = True
                            except Exception:
                                should_analyze = True
                        else:
                            should_analyze = True
                        
                        # 분석이 필요한 경우 즉시 실행 (최대 1개 코인만 빠르게 분석)
                        if should_analyze:
                            # 분석 실행 플래그 확인 (중복 실행 방지)
                            engine = controller.engine
                            analysis_lock = getattr(engine, '_analysis_lock', None)
                            if analysis_lock is None:
                                import threading
                                engine._analysis_lock = threading.Lock()
                                analysis_lock = engine._analysis_lock
                            
                            # 락이 없으면 (분석 중이 아니면) 실행
                            if not analysis_lock.locked():
                                analysis_in_progress = True  # 분석 시작 플래그
                                engine._analysis_in_progress = True
                                
                                def run_ai_analysis_async():
                                    with analysis_lock:
                                        try:
                                            if engine and engine.strategy.name in ("ai_market_analyzer", "ai_market_analyzer_high_risk"):
                                                LOGGER.info("SSE stream: Executing AI analysis for multiple markets")
                                                try:
                                                    # 여러 코인 분석 실행 (기존 메서드 사용)
                                                    if hasattr(engine, '_analyze_multiple_markets'):
                                                        selected_market, signal, candles = engine._analyze_multiple_markets()
                                                        
                                                        # 분석 결과 저장
                                                        if hasattr(engine.strategy, 'last_analysis') and engine.strategy.last_analysis:
                                                            engine.last_ai_analysis = engine.strategy.last_analysis.copy()
                                                            engine.last_ai_analysis['selected_market'] = selected_market or engine.market
                                                            engine.last_ai_analysis['timestamp'] = datetime.now(UTC).isoformat()
                                                            
                                                            # signal을 문자열로 변환
                                                            signal_obj = engine.last_ai_analysis.get('signal')
                                                            if signal_obj is not None:
                                                                if hasattr(signal_obj, 'value'):
                                                                    engine.last_ai_analysis['signal'] = signal_obj.value
                                                                elif hasattr(signal_obj, 'name'):
                                                                    engine.last_ai_analysis['signal'] = signal_obj.name
                                                                else:
                                                                    engine.last_ai_analysis['signal'] = str(signal_obj)
                                                            
                                                            signal_value = signal.value if hasattr(signal, 'value') else str(signal)
                                                            LOGGER.info(f"SSE stream: AI analysis completed - {selected_market or engine.market} -> {signal_value} (confidence: {engine.last_ai_analysis.get('confidence', 0):.2%})")
                                                        else:
                                                            LOGGER.warning("SSE stream: AI analysis executed but no result available")
                                                    else:
                                                        # _analyze_multiple_markets가 없으면 현재 market만 분석 (fallback)
                                                        current_market = engine.market
                                                        raw = engine.client.get_candles(current_market, unit=engine.candle_unit, count=20)
                                                        if raw:
                                                            from upbit_bot.strategies import Candle
                                                            candles_list = [
                                                                Candle(
                                                                    timestamp=int(item["timestamp"]),
                                                                    open=float(item["opening_price"]),
                                                                    high=float(item["high_price"]),
                                                                    low=float(item["low_price"]),
                                                                    close=float(item["trade_price"]),
                                                                    volume=float(item["candle_acc_trade_volume"]),
                                                                )
                                                                for item in reversed(raw)
                                                            ]
                                                            
                                                            if len(candles_list) >= 5:
                                                                # AI 분석 실행
                                                                signal = engine.strategy.on_candles(candles_list)
                                                                
                                                                # 분석 결과 저장
                                                                if hasattr(engine.strategy, 'last_analysis') and engine.strategy.last_analysis:
                                                                    engine.last_ai_analysis = engine.strategy.last_analysis.copy()
                                                                    engine.last_ai_analysis['selected_market'] = current_market
                                                                    engine.last_ai_analysis['timestamp'] = datetime.now(UTC).isoformat()
                                                                    
                                                                    # signal을 문자열로 변환
                                                                    signal_obj = engine.last_ai_analysis.get('signal')
                                                                    if signal_obj is not None:
                                                                        if hasattr(signal_obj, 'value'):
                                                                            engine.last_ai_analysis['signal'] = signal_obj.value
                                                                        elif hasattr(signal_obj, 'name'):
                                                                            engine.last_ai_analysis['signal'] = signal_obj.name
                                                                        else:
                                                                            engine.last_ai_analysis['signal'] = str(signal_obj)
                                                                    
                                                                    LOGGER.info(f"SSE stream: AI analysis completed - {current_market} -> {signal.value if hasattr(signal, 'value') else str(signal)} (confidence: {engine.last_ai_analysis.get('confidence', 0):.2%})")
                                                                else:
                                                                    LOGGER.warning("SSE stream: AI analysis executed but no result available")
                                                except Exception as e:
                                                    LOGGER.error(f"SSE stream: Multi-market analysis failed: {e}", exc_info=True)
                                        except Exception as e:
                                            LOGGER.error(f"SSE stream: AI analysis failed: {e}", exc_info=True)
                                        finally:
                                            # 분석 완료 플래그 제거
                                            engine._analysis_in_progress = False
                                
                                # 백그라운드 스레드에서 실행
                                analysis_thread = Thread(target=run_ai_analysis_async, daemon=True)
                                analysis_thread.start()
                                LOGGER.info("AI analysis thread started - analyzing multiple markets")
                            else:
                                # 이미 분석 중이면 플래그 확인
                                analysis_in_progress = getattr(engine, '_analysis_in_progress', False)
                        
                        # 분석 결과가 여전히 없거나 분석 중이면 상태 정보 제공
                        if not ai_analysis or analysis_in_progress:
                            # Ollama 연결 확인 (더 상세한 검사) - 먼저 확인하여 분석 상태를 결정
                            ollama_status = "disconnected"
                            ollama_error = None
                            try:
                                # 서버 로컬 Ollama 사용 (환경 변수 또는 기본값)
                                import os
                                from upbit_bot.services.ollama_client import OLLAMA_BASE_URL
                                ollama_url = os.getenv("OLLAMA_SCANNER_URL") or os.getenv("OLLAMA_BASE_URL") or OLLAMA_BASE_URL
                                
                                test_response = requests.get(f"{ollama_url}/api/tags", timeout=3)
                                if test_response.status_code == 200:
                                    models = test_response.json().get("models", [])
                                    model_names = [m.get("name", "") for m in models]
                                    # 현재는 1.5b 단일 모델 구조를 사용하므로, 태그 조회만 성공하면 연결된 것으로 간주
                                    ollama_status = "connected"
                                    LOGGER.info(f"Ollama 연결 확인: {len(models)}개 모델 사용 가능 (모델 목록: {', '.join(model_names[:3])}...)")
                                else:
                                    ollama_status = "error"
                                    ollama_error = f"HTTP {test_response.status_code}"
                                    LOGGER.warning(f"Ollama 응답 오류: {ollama_error}")
                            except requests.exceptions.Timeout:
                                ollama_status = "timeout"
                                ollama_error = "연결 시간 초과 (3초) - 서버 Ollama 서버 응답 없음"
                                LOGGER.warning(f"Ollama 연결 시간 초과 - 서버 Ollama 서버가 응답하지 않음")
                            except requests.exceptions.ConnectionError as e:
                                ollama_status = "disconnected"
                                ollama_error = f"연결 오류: {str(e)[:100]}"
                                LOGGER.error(f"Ollama 연결 실패: {e}")
                            except Exception as e:
                                ollama_status = "error"
                                ollama_error = f"예기치 않은 오류: {str(e)[:100]}"
                                LOGGER.error(f"Ollama 확인 중 오류: {e}", exc_info=True)
                            
                            # Ollama 연결 실패 시 분석 플래그 초기화
                            if ollama_status in ["disconnected", "timeout", "error", "model_missing"]:
                                # 분석 진행 중이었더라도 Ollama가 응답하지 않으면 플래그 초기화
                                if analysis_in_progress:
                                    LOGGER.warning(f"Ollama 서버 응답 없음 - 분석 플래그 초기화 (status: {ollama_status})")
                                    analysis_in_progress = False
                                    engine = controller.engine
                                    if hasattr(engine, '_analysis_in_progress'):
                                        engine._analysis_in_progress = False
                                status = "ollama_disconnected"
                            elif analysis_in_progress:
                                # Ollama가 연결되어 있고 분석 중이면 "analyzing" 상태
                                status = "analyzing"
                            elif ollama_status == "connected":
                                # Ollama가 연결되어 있으면 분석을 시작해야 하므로 "analyzing"으로 표시
                                # (실제로는 분석이 곧 시작되거나 진행 중일 수 있음)
                                status = "analyzing"
                            else:
                                status = "ollama_disconnected"
                            
                            # 분석 중일 때는 selected_market을 "N/A"로 설정 (BTC 등 기본값 표시 방지)
                            default_market = "N/A" if status == "analyzing" else state.get("market", "N/A")
                            
                            ai_analysis = {
                                "selected_market": default_market,
                                "signal": state.get("last_signal", "HOLD"),
                                "confidence": 0.0,
                                "market_data": {},
                                "timestamp": datetime.now(UTC).isoformat(),
                                "status": status,
                                "ollama_status": ollama_status,
                                "ollama_error": ollama_error
                            }
                    
                    # 통계 데이터 가져오기 (오늘/누적 각각)
                    statistics_data = None
                    try:
                        trade_history_store: TradeHistoryStore = app.state.trade_history_store
                        today_stats = trade_history_store.get_statistics(today_only=True)
                        cumulative_stats = trade_history_store.get_statistics(today_only=False)
                        statistics_data = {
                            "today": today_stats,
                            "cumulative": cumulative_stats,
                        }
                    except Exception as e:
                        LOGGER.warning(f"Failed to get statistics: {e}")
                        empty_stats = {
                            "total_trades": 0,
                            "closed_positions": 0,
                            "winning_trades": 0,
                            "losing_trades": 0,
                            "win_rate": 0.0,
                            "total_pnl": 0.0,
                            "avg_pnl_pct": 0.0,
                            "avg_win": 0.0,
                            "avg_loss": 0.0,
                            "profit_factor": 0.0,
                        }
                        statistics_data = {
                            "today": empty_stats,
                            "cumulative": empty_stats,
                        }
                    
                    # 거래 내역 가져오기 (최근 20개)
                    recent_trades = None
                    try:
                        trade_history_store: TradeHistoryStore = app.state.trade_history_store
                        recent_trades = trade_history_store.get_recent_trades(limit=20)
                    except Exception as e:
                        LOGGER.warning(f"Failed to get recent trades: {e}")
                        recent_trades = []
                    
                    # 포트폴리오 정보 가져오기 (보유 중인 코인 목록)
                    portfolio_data = None
                    try:
                        portfolio_data = controller.engine.get_portfolio_status()
                    except Exception as e:
                        LOGGER.warning(f"Failed to get portfolio status: {e}")
                        portfolio_data = {
                            "total_positions": 0,
                            "open_positions": [],
                            "worst_position": None,
                        }
                    
                    data = {
                        "timestamp": int(__import__("time").time() * 1000),
                        "balance": account,
                        "state": state,
                        "ai_analysis": ai_analysis,  # AI 전략이면 항상 포함
                        "ollama_status": ollama_status_data,  # Ollama 상태는 항상 포함 (서버 시작/중지와 상관없이)
                        "statistics": statistics_data,  # 통계 데이터 포함
                        "recent_trades": recent_trades,  # 최근 거래 내역 포함
                        "portfolio": portfolio_data,  # 포트폴리오 정보 포함 (보유 중인 코인 목록)
                    }
                    
                    # Send SSE formatted data
                    yield f"data: {json.dumps(data)}\n\n"
                    
                    # Update every 3 seconds for responsive UI
                    await asyncio.sleep(3)
                except Exception as e:
                    LOGGER.error(f"Stream error: {e}")
                    await asyncio.sleep(3)
        
        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/trades")
    async def get_trades(limit: int = 50) -> JSONResponse:
        """Get recent trades."""
        trade_history_store: TradeHistoryStore = app.state.trade_history_store
        trades = trade_history_store.get_recent_trades(limit=limit)
        return JSONResponse({"trades": trades})

    @app.get("/statistics")
    async def get_statistics(market: str | None = None, today_only: bool = False) -> JSONResponse:
        """Get trading statistics."""
        trade_history_store: TradeHistoryStore = app.state.trade_history_store
        stats = trade_history_store.get_statistics(market=market, today_only=today_only)
        return JSONResponse(stats)

    @app.delete("/statistics")
    async def clear_statistics(today_only: bool = False) -> JSONResponse:
        """Clear trading statistics."""
        trade_history_store: TradeHistoryStore = app.state.trade_history_store
        try:
            result = trade_history_store.clear_statistics(today_only=today_only)
            return JSONResponse({"success": True, "message": result})
        except Exception as e:
            LOGGER.error(f"Failed to clear statistics: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

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

    @app.get("/chart/{market}")
    async def get_chart_data(market: str, candles: int = 100) -> JSONResponse:
        """Get candle data for chart."""
        try:
            # 시장 이름 정규화 (예: BTC -> KRW-BTC)
            if not market.startswith("KRW-"):
                market = f"KRW-{market}"
            
            # 100개 봉 조회
            candle_data = controller.engine.client.get_candles(market, unit=5, count=candles)
            
            if not candle_data:
                return JSONResponse({"error": f"No data for {market}"}, status_code=404)
            
            # 차트용으로 변환
            chart_data = []
            for c in candle_data:
                # Candle 객체 또는 dict 형식 지원
                if isinstance(c, dict):
                    chart_data.append({
                        "time": c.get("candle_date_time_utc", c.get("timestamp", "")),
                        "open": float(c.get("opening_price", 0)),
                        "high": float(c.get("high_price", 0)),
                        "low": float(c.get("low_price", 0)),
                        "close": float(c.get("trade_price", 0)),
                        "volume": float(c.get("candle_acc_trade_volume", 0)),
                    })
                else:
                    # Candle 객체
                    chart_data.append({
                        "time": c.timestamp,
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume),
                    })
            
            return JSONResponse({"data": chart_data, "market": market})
        except Exception as e:  # noqa: BLE001
            error_msg = str(e)
            # 404 에러 (코인 없음) 처리
            if "404" in error_msg or "Code not found" in error_msg or "market not found" in error_msg.lower():
                LOGGER.debug(f"Chart data not found for {market}: {e}")
                return JSONResponse({"error": f"코인 '{market}' 데이터를 찾을 수 없습니다", "code": "NOT_FOUND"}, status_code=404)
            # 기타 에러는 500으로 반환하되 상세 정보 로깅
            LOGGER.error(f"Failed to get chart data for {market}: {e}", exc_info=True)
            return JSONResponse({"error": str(e)}, status_code=500)

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

    @app.post("/api/sync-trades")
    async def sync_trades() -> JSONResponse:
        """사용자가 직접 거래한 내역을 동기화."""
        try:
            trade_history_store: TradeHistoryStore = app.state.trade_history_store
            result = trade_history_store.sync_external_trades(
                client=controller.engine.client,
                days=7,
            )
            
            if result.get("success"):
                return JSONResponse({
                    "success": True,
                    "message": f"거래 내역 동기화 완료: {result.get('synced', 0)}개 동기화, {result.get('skipped', 0)}개 스킵",
                    "synced": result.get("synced", 0),
                    "skipped": result.get("skipped", 0),
                    "errors": result.get("errors", []),
                })
            else:
                return JSONResponse(
                    {"success": False, "error": result.get("error", "동기화 실패")},
                    status_code=400,
                )
        except Exception as e:  # noqa: BLE001
            LOGGER.error(f"거래 내역 동기화 실패: {e}")
            return JSONResponse(
                {"success": False, "error": str(e)},
                status_code=500,
            )

    @app.post("/update-settings")
    async def update_settings(
        strategy: Optional[str] = Form(None),
        market: Optional[str] = Form(None),
        order_amount_pct: Optional[float] = Form(None),
        mode: Optional[str] = Form(None),
    ) -> JSONResponse:
        """설정 업데이트"""
        try:
            updates: dict[str, Any] = {}
            
            # 거래 모드 업데이트 (dry-run/live)
            if mode is not None:
                if mode in ("dry", "live"):
                    new_dry_run = mode != "live"
                    controller.engine.dry_run = new_dry_run
                    updates["mode"] = mode
                    updates["dry_run"] = new_dry_run  # 명시적으로 값 저장
                    LOGGER.info(f"Trading mode updated to: {mode} (dry_run={new_dry_run})")
            
            if strategy and strategy in AVAILABLE_STRATEGIES:
                # 전략 업데이트
                new_strategy = get_strategy(strategy, **settings.strategy.config or {})
                controller.engine.strategy = new_strategy
                
                # AI 전략일 때는 1분 주기, 다른 전략은 5분 주기
                if strategy in ("ai_market_analyzer", "ai_market_analyzer_high_risk"):
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

    @app.get("/api/scan-results")
    async def get_scan_results(
        limit: int = Query(50, ge=1, le=100),
        max_age_minutes: int = Query(5, ge=1, le=60),
        min_score: float = Query(0.0, ge=0.0, le=1.0),
    ) -> JSONResponse:
        """스캔 결과 조회"""
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            
            try:
                from upbit_bot.database.redis_store import RedisScanStore
                
                store = RedisScanStore(redis_url)
                
                max_age_seconds = max_age_minutes * 60
                results = store.get_scan_results(max_age_seconds=max_age_seconds)
                
                # 필터링
                filtered = [r for r in results if float(r.get('score', 0)) >= min_score]
                
                return JSONResponse({
                    "timestamp": datetime.now(UTC).isoformat(),
                    "count": len(filtered[:limit]),
                    "max_age_minutes": max_age_minutes,
                    "results": filtered[:limit]
                })
            except ImportError:
                LOGGER.error("Redis 스토어를 사용할 수 없습니다. redis 모듈이 설치되어 있는지 확인하세요.")
                return JSONResponse({
                    "error": "Redis 스토어를 사용할 수 없습니다",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "count": 0,
                    "results": []
                }, status_code=503)
        except Exception as e:
            LOGGER.error(f"스캔 결과 조회 실패: {e}", exc_info=True)
            return JSONResponse({
                "error": str(e),
                "timestamp": datetime.now(UTC).isoformat(),
                "count": 0,
                "results": []
            }, status_code=500)

    @app.get("/api/scanner/health")
    async def scanner_health() -> JSONResponse:
        """스캐너 헬스체크"""
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            
            try:
                from upbit_bot.database.redis_store import RedisScanStore
                
                store = RedisScanStore(redis_url)
                
                # 최근 10분 이내 스캔 결과 조회
                results = store.get_scan_results(max_age_seconds=600)
                
                if not results:
                    return JSONResponse({
                        "status": "no_data",
                        "message": "최근 10분 이내 스캔 결과 없음"
                    })
                
                # 고유 마켓(코인) 수 계산
                unique_markets: set[str] = set()
                for r in results:
                    market = r.get("market")
                    if isinstance(market, str) and market:
                        unique_markets.add(market)
                total_coins_scanned = len(unique_markets)
                
                latest = max(results, key=lambda x: x.get('timestamp', ''))
                latest_timestamp_str = latest.get('timestamp', '')
                
                if latest_timestamp_str:
                    latest_timestamp = datetime.fromisoformat(latest_timestamp_str.replace("Z", "+00:00"))
                    age = (datetime.now(UTC) - latest_timestamp).total_seconds()
                    
                    return JSONResponse({
                        "status": "healthy" if age < 300 else "stale",
                        "last_scan_age_seconds": age,
                        # 최근 10분 이내 스캔된 고유 코인 수
                        "total_coins_scanned": total_coins_scanned,
                        # 참고용: 원시 결과 개수
                        "raw_entries": len(results),
                        "latest_timestamp": latest_timestamp_str
                    })
                else:
                    return JSONResponse({
                        "status": "unknown",
                        "message": "타임스탬프 정보 없음"
                    })
            except ImportError:
                return JSONResponse({
                    "status": "error",
                    "message": "Redis 스토어를 사용할 수 없습니다"
                })
        except Exception as e:
            LOGGER.error(f"스캐너 헬스체크 실패: {e}", exc_info=True)
            return JSONResponse({
                "status": "error",
                "message": str(e)
            })

    @app.post("/api/ai/query")
    async def ai_query(request: Request) -> JSONResponse:
        """코인 관련 Q&A 엔드포인트."""
        try:
            payload = await request.json()
            question = (payload.get("question") or "").strip()
            if not question:
                return JSONResponse(
                    {"error": "question is required"},
                    status_code=400,
                )

            # 코인/마켓 추출 (간단 규칙)
            import re

            market_pattern = re.compile(r"\b(KRW-[A-Z0-9]{2,10})\b")
            markets = market_pattern.findall(question)
            market = markets[0] if markets else None

            # trade history / decisions / scan 결과 조회
            trade_store: TradeHistoryStore = app.state.trade_history_store

            # 최근 거래
            recent_trades = []
            try:
                if market:
                    recent_trades = trade_store.get_trades_by_market(market, limit=20)
                else:
                    recent_trades = trade_store.get_recent_trades(limit=20)
            except Exception as e:
                LOGGER.warning(f"AI Q&A: trade history 조회 실패: {e}")

            # 최근 AI 결정/스캔 결과 (직접 SQL 사용)
            decisions: list[dict[str, Any]] = []
            scans: list[dict[str, Any]] = []
            try:
                conn = trade_store._conn  # 내부 커넥션 재사용
                cur = conn.execute(
                    """
                    SELECT * FROM ai_decisions
                    ORDER BY decided_at DESC
                    LIMIT 20
                    """
                )
                decisions = [dict(row) for row in cur.fetchall()]

                cur = conn.execute(
                    """
                    SELECT * FROM coin_scan_results
                    ORDER BY scanned_at DESC
                    LIMIT 100
                    """
                )
                scans = [dict(row) for row in cur.fetchall()]
            except Exception as e:
                LOGGER.warning(f"AI Q&A: ai_decisions/coin_scan_results 조회 실패: {e}")

            # LLM 컨텍스트 구성
            context = {
                "question": question,
                "market": market,
                "recent_trades": recent_trades,
                "ai_decisions": decisions,
                "coin_scans": scans,
            }

            # Ollama 클라이언트 (경량 모델 사용)
            # Q&A 용도이므로 결정자용 기본 모델(1.5B)을 그대로 재사용
            from upbit_bot.services.ollama_client import OLLAMA_DECISION_MODEL

            ollama_client = OllamaClient(model=OLLAMA_DECISION_MODEL)
            prompt = (
                "당신은 이 업비트 자동매매 봇의 기록을 설명해주는 어시스턴트입니다.\n"
                "아래 JSON 데이터만 근거로, 사용자의 코인 관련 질문에 한국어로 답하세요.\n"
                "모르는 정보는 \"기록 상 알 수 없습니다\"라고 답하고, 추측하거나 지어내지 마세요.\n\n"
                "[질문]\n"
                f"{question}\n\n"
                "[트레이딩/AI 기록 데이터]\n"
                f"{json.dumps(context, ensure_ascii=False)[:6000]}\n\n"
                "위 데이터를 기반으로 간결하지만 충분한 설명을 해주세요."
            )

            try:
                answer_text = ollama_client.generate(prompt, temperature=0.2)
            except OllamaError as e:
                LOGGER.error(f"AI Q&A Ollama 오류: {e}")
                return JSONResponse(
                    {"error": "Ollama 호출 실패", "details": str(e)},
                    status_code=500,
                )

            return JSONResponse(
                {
                    "answer": answer_text.strip(),
                    "market": market,
                }
            )
        except Exception as e:  # noqa: BLE001
            LOGGER.error(f"AI Q&A 처리 실패: {e}", exc_info=True)
            return JSONResponse(
                {"error": str(e)},
                status_code=500,
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
            # _render_dashboard 함수에서 controller 접근 (app.state 사용)
            # 순환 참조 방지를 위해 조건부 import 및 None 체크
            try:
                import sys
                app_module = sys.modules.get('upbit_bot.web.app')
                if app_module and hasattr(app_module, 'create_app'):
                    # app 인스턴스는 create_app에서 생성되므로 직접 접근 불가
                    # 대신 account 데이터의 avg_buy_price 사용
                    current_price = None
                else:
                    current_price = None
            except Exception:
                current_price = None
                
            # API 호출 없이 평균 매수가 사용 (더 안정적)
            # 필요시 나중에 별도 API 호출 추가 가능
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
        
        # 구매 금액 계산
        avg_buy_price = float(entry.get("avg_buy_price", 0.0))
        purchase_amount = balance * avg_buy_price
        
        entry_with_value = {
            **entry, 
            "current_price": current_price, 
            "crypto_value": crypto_value,
            "purchase_amount": purchase_amount,
            "avg_buy_price": avg_buy_price
        }
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
    <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; connect-src 'self' ws: wss:; img-src 'self' data: https:; font-src 'self' data:;">
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
        @keyframes shimmer {{
            0% {{ background-position: -1000px 0; }}
            100% {{ background-position: 1000px 0; }}
        }}
        .status-indicator {{
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
            box-shadow: 0 0 8px currentColor;
        }}
        .status-indicator.running {{
            background-color: #10b981;
            animation: pulse 2s infinite;
        }}
        .status-indicator.stopped {{
            background-color: #ef4444;
        }}
        .card {{
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            border: 1px solid rgba(229, 231, 235, 0.5);
        }}
        .dark .card {{
            border-color: rgba(55, 65, 81, 0.5);
        }}
        .card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 20px 40px -12px rgba(0, 0, 0, 0.15);
        }}
        .balance-card {{
            background: linear-gradient(135deg, #ffffff 0%, #f9fafb 100%);
            border: 1px solid rgba(229, 231, 235, 0.8);
        }}
        .dark .balance-card {{
            background: linear-gradient(135deg, #1f2937 0%, #111827 100%);
            border-color: rgba(55, 65, 81, 0.8);
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
            transition: all 0.2s;
        }}
        .btn-primary:hover {{
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
            transform: translateY(-1px);
            box-shadow: 0 10px 20px rgba(59, 130, 246, 0.3);
        }}
        .btn-success {{
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
        }}
        .btn-success:hover {{
            background: linear-gradient(135deg, #059669 0%, #047857 100%);
            box-shadow: 0 10px 20px rgba(16, 185, 129, 0.3);
        }}
        .btn-danger {{
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
        }}
        .btn-danger:hover {{
            background: linear-gradient(135deg, #dc2626 0%, #b91c1c 100%);
            box-shadow: 0 10px 20px rgba(239, 68, 68, 0.3);
        }}
        .table-row {{
            transition: all 0.2s;
        }}
        .table-row:hover {{
            background-color: rgba(59, 130, 246, 0.05);
        }}
        .dark .table-row:hover {{
            background-color: rgba(59, 130, 246, 0.1);
        }}
        .stat-card {{
            background: linear-gradient(135deg, rgba(249, 250, 251, 0.8) 0%, rgba(243, 244, 246, 0.8) 100%);
            border: 1px solid rgba(229, 231, 235, 0.6);
        }}
        .dark .stat-card {{
            background: linear-gradient(135deg, rgba(31, 41, 55, 0.8) 0%, rgba(17, 24, 39, 0.8) 100%);
            border-color: rgba(55, 65, 81, 0.6);
        }}
    </style>
</head>
<body class="bg-gradient-to-br from-gray-50 via-white to-gray-50 dark:from-gray-900 dark:via-gray-800 dark:to-gray-900 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-7xl">
        <!-- Header -->
        <div class="mb-10">
            <div class="flex items-center justify-between mb-6">
    <div>
                    <h1 class="text-5xl font-extrabold bg-gradient-to-r from-blue-600 via-purple-600 to-blue-800 dark:from-blue-400 dark:via-purple-400 dark:to-blue-600 bg-clip-text text-transparent mb-2">
                        Upbit Trading Bot
                    </h1>
                    <p class="text-gray-600 dark:text-gray-400 text-sm">AI 기반 자동 매매 시스템</p>
    </div>
                    </div>
                    </div>

        <!-- Server Control & Account (상단으로 이동) -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <!-- Controls Card -->
            <div class="card bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-7">
                <h2 class="text-2xl font-extrabold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                    <span class="text-3xl">🎮</span>
                    <span>서버 제어</span>
                </h2>
                
                <!-- 서버 상태 표시 -->
                <div class="mb-6 p-5 rounded-xl bg-gradient-to-br from-blue-50 via-blue-100 to-indigo-50 dark:from-blue-900/30 dark:via-blue-800/20 dark:to-indigo-900/30 border-2 border-blue-200 dark:border-blue-800 shadow-md">
                    <div class="flex items-center justify-between">
                        <div>
                            <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2 uppercase tracking-wide">서버 상태</p>
                            <div class="flex items-center gap-3">
                                <div class="w-4 h-4 rounded-full bg-green-500 animate-pulse shadow-lg shadow-green-500/50" id="server-status-dot"></div>
                                <span class="text-xl font-extrabold text-gray-900 dark:text-white" id="server-status-text">🟢 동작 중</span>
                            </div>
                        </div>
                        <div class="text-right">
                            <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2 uppercase tracking-wide">거래 모드</p>
                            <span class="inline-block px-4 py-1.5 rounded-xl text-sm font-bold shadow-md {'bg-gradient-to-r from-blue-500 to-blue-600 text-white' if state.dry_run else 'bg-gradient-to-r from-orange-500 to-red-600 text-white'}" id="trading-mode-badge">{state.dry_run and '모의 모드' or '실전 모드'}</span>
                        </div>
                    </div>
                </div>
                
                <div class="space-y-4">
                    <form method="post" action="/start" class="space-y-3">
                        <div>
                            <label for="mode" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">📊 거래 모드 선택</label>
                            <div class="grid grid-cols-2 gap-2">
                                <button type="button" id="mode-dry" class="w-full px-4 py-2 border-2 rounded-lg font-semibold transition-all {'border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300' if state.dry_run else 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:border-blue-400'}">
                                    🟢 모의 모드
                                </button>
                                <button type="button" id="mode-live" class="w-full px-4 py-2 border-2 rounded-lg font-semibold transition-all {'border-red-500 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300' if not state.dry_run else 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:border-red-400'}">
                                    🔴 실전 모드
                                </button>
                            </div>
                            <input type="hidden" id="mode" name="mode" value="{'dry' if state.dry_run else 'live'}">
                        </div>
                        <button type="submit" class="btn-success w-full text-white font-bold py-3.5 px-6 rounded-xl transition-all duration-200 shadow-lg hover:shadow-xl flex items-center justify-center gap-2">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path>
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            <span>서버 시작</span>
                        </button>
                    </form>
                    <form method="post" action="/stop">
                        <button type="submit" class="btn-danger w-full text-white font-bold py-3.5 px-6 rounded-xl transition-all duration-200 shadow-lg hover:shadow-xl flex items-center justify-center gap-2">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 10h6v4H9z"></path>
                            </svg>
                            <span>서버 중지</span>
                        </button>
                    </form>
                    
                    <!-- 강제 탈출 버튼 -->
                    <button id="force-exit-btn" class="w-full bg-gradient-to-r from-orange-500 to-red-600 hover:from-orange-600 hover:to-red-700 active:from-orange-700 active:to-red-800 text-white font-bold py-3.5 px-6 rounded-xl transition-all duration-200 shadow-lg hover:shadow-xl flex items-center justify-center gap-2">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path>
                        </svg>
                        <span>강제 탈출 (모든 코인 매도)</span>
                    </button>
                    
                    <!-- 거래 내역 동기화 버튼 -->
                    <button id="sync-trades-btn" class="w-full bg-gradient-to-r from-blue-500 to-indigo-600 hover:from-blue-600 hover:to-indigo-700 active:from-blue-700 active:to-indigo-800 text-white font-bold py-3.5 px-6 rounded-xl transition-all duration-200 shadow-lg hover:shadow-xl flex items-center justify-center gap-2">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                        </svg>
                        <span>거래 내역 동기화</span>
                    </button>
                    
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
            
            <!-- Account Snapshot -->
            <div class="card bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-7">
                <h2 class="text-2xl font-extrabold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                    <span class="text-3xl">💼</span>
                    <span>자산 현황</span>
                </h2>
                {f'''
                <div class="mb-4 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
                    <div class="flex items-start">
                        <svg class="w-5 h-5 text-red-600 dark:text-red-400 mt-0.5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        <div>
                            <p class="text-sm font-semibold text-red-600 dark:text-red-400 mb-1">
                                {('인증 오류' if '401' in str(account_error) or 'invalid_access_key' in str(account_error) else '계정 조회 오류')}
                            </p>
                            <p class="text-xs text-red-600 dark:text-red-400">
                                {'API 키가 유효하지 않습니다. .env 파일의 UPBIT_ACCESS_KEY와 UPBIT_SECRET_KEY를 확인해주세요.' if '401' in str(account_error) or 'invalid_access_key' in str(account_error) else '업비트 API 응답 지연 또는 네트워크 오류입니다. 잠시 후 다시 시도해주세요.'}
                                <br/>
                                <span class="text-[10px] opacity-80">{str(account_error)}</span>
                            </p>
                        </div>
                    </div>
                </div>
                ''' if account_error else ''}
                <div class="overflow-x-auto">
                    <table id="account-snapshot" class="w-full text-sm">
                        <thead>
                            <tr class="border-b-2 border-gray-300 dark:border-gray-600 bg-gradient-to-r from-gray-50 to-gray-100 dark:from-gray-700 dark:to-gray-800">
                                <th class="text-left py-4 px-4 font-bold text-gray-800 dark:text-gray-200">코인</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">보유량</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">구매금액 (원)</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">현재가치 (원)</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">수익/손실 (원)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {''.join([f'''
                            <tr class="table-row border-b border-gray-100 dark:border-gray-700 transition-all duration-200">
                                <td class="py-3 px-4 font-medium text-gray-900 dark:text-white">{entry.get('currency', '?')}</td>
                                <td class="py-3 px-4 text-right text-gray-900 dark:text-white">{float(entry.get('balance', 0)):,.8f}</td>
                                <td class="py-3 px-4 text-right font-medium text-blue-600 dark:text-blue-400">{f"{float(entry.get('purchase_amount', 0)):,.0f}" if entry.get('purchase_amount') else '-'}</td>
                                <td class="py-3 px-4 text-right font-medium text-green-600 dark:text-green-400">{f"{float(entry.get('crypto_value', 0)):,.0f}" if entry.get('crypto_value') else '-'}</td>
                                <td class="py-3 px-4 text-right font-medium {('text-green-600 dark:text-green-400' if float(entry.get('crypto_value', 0)) - float(entry.get('purchase_amount', 0)) >= 0 else 'text-red-600 dark:text-red-400')}">{f"{float(entry.get('crypto_value', 0)) - float(entry.get('purchase_amount', 0)):,.0f}" if entry.get('crypto_value') and entry.get('purchase_amount') else '-'}</td>
                            </tr>''' for entry in accounts_data]) if accounts_data else '<tr><td colspan="5" class="py-4 px-4 text-center text-gray-500 dark:text-gray-400">거래 가능한 코인이 없습니다</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Ollama Connection Status Alert -->
        <div id="ollama-alert" class="hidden mb-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
            <div class="flex items-start">
                <svg class="w-5 h-5 text-red-600 dark:text-red-400 mt-0.5 mr-3" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>
                </svg>
    <div>
                    <h3 class="text-sm font-semibold text-red-800 dark:text-red-200 mb-1">⚠️ Ollama 연결 끊김</h3>
                    <p class="text-sm text-red-700 dark:text-red-300">AI 시장 분석 서비스를 사용할 수 없습니다. 서버의 Ollama 서버 상태를 확인해주세요.</p>
    </div>
            </div>
        </div>

        <!-- AI Analysis Console Window (Always Visible - Scrollable) -->
        <div class="mb-8 bg-gradient-to-br from-gray-900 via-gray-900 to-gray-950 dark:from-gray-950 dark:via-gray-900 dark:to-black rounded-2xl shadow-2xl border border-gray-700 dark:border-gray-800 overflow-hidden">
            <div class="bg-gradient-to-r from-gray-800 to-gray-900 dark:from-gray-900 dark:to-gray-800 px-5 py-4 border-b border-gray-700 dark:border-gray-800 flex items-center justify-between">
                <div class="flex items-center gap-4">
                    <h3 class="text-base font-bold text-green-400 flex items-center gap-3">
                        <span class="text-2xl animate-pulse">🤖</span>
                        <span>AI 분석 콘솔 (2차 선정 10개)</span>
                    </h3>
                    <!-- Ollama 연결 상태 표시 -->
                    <div id="ollama-status-badge" class="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-gray-700/50 text-gray-400 border border-gray-600">
                        <span id="ollama-status-icon" class="w-2 h-2 rounded-full bg-gray-500 animate-pulse"></span>
                        <span id="ollama-status-text">Ollama 확인 중...</span>
                    </div>
                </div>
                <button id="console-clear-btn" class="px-3 py-1.5 text-xs font-semibold bg-gray-700 hover:bg-gray-600 active:bg-gray-500 text-gray-300 rounded-lg transition-all duration-200 shadow-md hover:shadow-lg">
                    Clear
                </button>
            </div>
            <div id="ai-console-content" class="overflow-y-auto p-5 font-mono text-sm text-green-400 bg-gray-900 dark:bg-black" style="height: 24em; line-height: 1.5em; max-height: 24em;">
                <div id="ai-console-waiting" class="text-gray-500 flex items-center gap-2">
                    <span class="animate-spin">🔄</span>
                    <span>AI 분석 대기 중... (1차 스캔: 30-60초, 2차 분석: 20-40초, 최종 선정: 10-30초)</span>
                </div>
            </div>
        </div>

        <!-- 매매 예정 콘솔 (최종 5개) -->
        <div class="mb-8 bg-gradient-to-br from-blue-900 via-blue-900 to-indigo-950 dark:from-indigo-950 dark:via-blue-900 dark:to-black rounded-2xl shadow-2xl border border-blue-700 dark:border-blue-800 overflow-hidden">
            <div class="bg-gradient-to-r from-blue-800 to-indigo-900 dark:from-indigo-900 dark:to-blue-800 px-5 py-4 border-b border-blue-700 dark:border-blue-800 flex items-center justify-between">
                <div class="flex items-center gap-4">
                    <h3 class="text-base font-bold text-blue-300 flex items-center gap-3">
                        <span class="text-2xl">🎯</span>
                        <span>매매 예정 (최종 선정 5개)</span>
                    </h3>
                    <div id="trading-pending-badge" class="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-700/50 text-blue-300 border border-blue-600">
                        <span id="trading-pending-count">0</span>
                        <span>개 예정</span>
                    </div>
                </div>
            </div>
            <div id="trading-pending-content" class="overflow-y-auto p-5 font-mono text-sm text-blue-300 bg-blue-900/30 dark:bg-black" style="height: 20em; line-height: 1.5em; max-height: 20em;">
                <div id="trading-pending-waiting" class="text-gray-500 flex items-center gap-2">
                    <span class="animate-spin">🔄</span>
                    <span>매매 예정 목록 대기 중... (최종 5개 선정 완료 후 표시, 예상 소요: 60-130초)</span>
                </div>
            </div>
        </div>

        <!-- Balance Cards -->
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
            <div class="balance-card card rounded-2xl shadow-xl p-7 relative overflow-hidden">
                <div class="absolute top-0 right-0 w-32 h-32 bg-gradient-to-br from-green-400/20 to-green-600/10 rounded-full -mr-16 -mt-16"></div>
                <div class="flex items-center justify-between relative z-10">
                    <div>
                        <p class="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">KRW 잔액</p>
                        <p class="text-4xl font-extrabold text-gray-900 dark:text-white mb-1" id="balance-krw">{krw_balance:,.0f}</p>
                        <p class="text-xs text-gray-500 dark:text-gray-500 font-medium">원화 보유</p>
                    </div>
                    <div class="w-16 h-16 bg-gradient-to-br from-green-400 to-green-600 rounded-2xl flex items-center justify-center shadow-lg">
                        <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                    </div>
                </div>
            </div>
            
            <div class="balance-card card rounded-2xl shadow-xl p-7 relative overflow-hidden">
                <div class="absolute top-0 right-0 w-32 h-32 bg-gradient-to-br from-purple-400/20 to-purple-600/10 rounded-full -mr-16 -mt-16"></div>
                <div class="flex items-center justify-between relative z-10">
                    <div>
                        <p class="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">암호화폐 가치</p>
                        <p class="text-4xl font-extrabold text-gray-900 dark:text-white mb-1" id="balance-crypto">{total_crypto_value:,.0f}</p>
                        <p class="text-xs text-gray-500 dark:text-gray-500 font-medium">코인 보유</p>
                    </div>
                    <div class="w-16 h-16 bg-gradient-to-br from-purple-400 to-purple-600 rounded-2xl flex items-center justify-center shadow-lg">
                        <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"></path>
                        </svg>
                    </div>
                </div>
            </div>
            
            <div class="balance-card card rounded-2xl shadow-xl p-7 relative overflow-hidden">
                <div class="absolute top-0 right-0 w-32 h-32 bg-gradient-to-br from-blue-400/20 to-blue-600/10 rounded-full -mr-16 -mt-16"></div>
                <div class="flex items-center justify-between relative z-10">
                    <div>
                        <p class="text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">총 자산</p>
                        <p class="text-4xl font-extrabold text-gray-900 dark:text-white mb-1" id="balance-total">{total_balance:,.0f}</p>
                        <p class="text-xs text-gray-500 dark:text-gray-500 font-medium">전체 합계</p>
                    </div>
                    <div class="w-16 h-16 bg-gradient-to-br from-blue-400 to-blue-600 rounded-2xl flex items-center justify-center shadow-lg">
                        <svg class="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path>
                        </svg>
                    </div>
                </div>
            </div>
        </div>

        <!-- Statistics & Trade History (중요 정보 - 상단 배치) -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <!-- Performance Analysis - Split into Today and Cumulative -->
            <div class="card bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-7">
                <h2 class="text-2xl font-extrabold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                    <span class="text-3xl">📊</span>
                    <span>성과 분석</span>
                </h2>
                
                <!-- 오늘 기준 성과 -->
                <div class="mb-6">
                    <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-3 flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <span class="text-xl">📅</span>
                            <span>오늘 기준 성과</span>
                        </div>
                        <button
                            id="clear-statistics-today-btn"
                            class="px-3 py-1.5 text-xs font-semibold bg-red-500 hover:bg-red-600 active:bg-red-700 text-white rounded-lg transition-all duration-200 shadow-md hover:shadow-lg"
                            title="오늘 성과 초기화"
                        >
                            Clear
                        </button>
                    </h3>
                    <div id="statistics-today" class="space-y-2" style="height: 9em; overflow-y-auto;">
                        <div class="grid grid-cols-2 gap-2 mb-2">
                        <div class="stat-card rounded-xl p-3 shadow-sm">
                            <p class="text-xs text-gray-600 dark:text-gray-400">총 거래</p>
                                <p class="text-lg font-bold text-gray-900 dark:text-white" id="stat-today-total-trades">0</p>
                        </div>
                        <div class="stat-card rounded-xl p-3 shadow-sm">
                            <p class="text-xs text-gray-600 dark:text-gray-400">승률</p>
                                <p class="text-lg font-bold text-green-600 dark:text-green-400" id="stat-today-win-rate">0%</p>
                        </div>
                        <div class="stat-card rounded-xl p-3 shadow-sm">
                            <p class="text-xs text-gray-600 dark:text-gray-400">총 수익/손실</p>
                                <p class="text-sm font-bold text-gray-900 dark:text-white" id="stat-today-total-pnl">0 KRW</p>
                        </div>
                        <div class="stat-card rounded-xl p-3 shadow-sm">
                            <p class="text-xs text-gray-600 dark:text-gray-400">평균 수익률</p>
                                <p class="text-lg font-bold text-gray-900 dark:text-white" id="stat-today-avg-profit-pct">0%</p>
                        </div>
                    </div>
                            </div>
                            </div>
                
                <!-- 누적 성과 -->
                <div class="border-t border-gray-200 dark:border-gray-700 pt-4">
                    <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-3 flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <span class="text-xl">📈</span>
                            <span>누적 성과</span>
                        </div>
                        <button
                            id="clear-statistics-cumulative-btn"
                            class="px-3 py-1.5 text-xs font-semibold bg-red-500 hover:bg-red-600 active:bg-red-700 text-white rounded-lg transition-all duration-200 shadow-md hover:shadow-lg"
                            title="누적 성과 초기화"
                        >
                            Clear
                        </button>
                    </h3>
                    <div id="statistics-cumulative" class="space-y-2" style="height: 9em; overflow-y-auto;">
                        <div class="grid grid-cols-2 gap-2 mb-2">
                            <div class="stat-card rounded-xl p-3 shadow-sm">
                                <p class="text-xs text-gray-600 dark:text-gray-400">총 거래</p>
                                <p class="text-lg font-bold text-gray-900 dark:text-white" id="stat-cumulative-total-trades">0</p>
                            </div>
                            <div class="stat-card rounded-xl p-3 shadow-sm">
                                <p class="text-xs text-gray-600 dark:text-gray-400">승률</p>
                                <p class="text-lg font-bold text-green-600 dark:text-green-400" id="stat-cumulative-win-rate">0%</p>
                            </div>
                            <div class="stat-card rounded-xl p-3 shadow-sm">
                                <p class="text-xs text-gray-600 dark:text-gray-400">총 수익/손실</p>
                                <p class="text-sm font-bold text-gray-900 dark:text-white" id="stat-cumulative-total-pnl">0 KRW</p>
                            </div>
                            <div class="stat-card rounded-xl p-3 shadow-sm">
                                <p class="text-xs text-gray-600 dark:text-gray-400">평균 수익률</p>
                                <p class="text-lg font-bold text-gray-900 dark:text-white" id="stat-cumulative-avg-profit-pct">0%</p>
                            </div>
                        </div>
                    </div>

            <!-- AI Q&A Search Bar -->
            <div class="mt-4">
                <div class="bg-white dark:bg-gray-800 shadow-lg rounded-2xl border border-gray-200 dark:border-gray-700 px-4 py-3 flex items-center gap-3">
                    <span class="text-xl">🔍</span>
                    <input
                        id="ai-query-input"
                        type="text"
                        class="flex-1 bg-transparent border-none focus:outline-none text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500"
                        placeholder="코인 질문을 입력하세요 (예: 왜 KRW-BTC를 그때 그 가격에 샀어?, 지금 공격적으로 들어갈 코인은?)"
                    />
                    <button
                        id="ai-query-button"
                        class="px-3 py-1.5 text-xs font-semibold rounded-xl bg-gradient-to-r from-blue-500 to-blue-600 text-white shadow-md hover:shadow-lg transition-all"
                        type="button"
                    >
                        질문하기
                    </button>
                </div>
                <div id="ai-query-result" class="mt-3 text-sm text-gray-800 dark:text-gray-100 whitespace-pre-line hidden"></div>
                </div>
            </div>

            <!-- Trade History -->
            <div class="card bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-7">
                <h2 class="text-2xl font-extrabold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                    <span class="text-3xl">📋</span>
                    <span>거래 내역</span>
                </h2>
                <div id="trade-history" class="overflow-x-auto overflow-y-auto" style="height: 20em;">
                    <table class="w-full text-sm">
                        <thead>
                            <tr class="border-b-2 border-gray-300 dark:border-gray-600 bg-gradient-to-r from-gray-50 to-gray-100 dark:from-gray-700 dark:to-gray-800">
                                <th class="text-left py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">시간</th>
                                <th class="text-left py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">코인</th>
                                <th class="text-left py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">전략</th>
                                <th class="text-center py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">신호</th>
                                <th class="text-right py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">가격</th>
                                <th class="text-right py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">수량</th>
                                <th class="text-right py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">총액</th>
                                <th class="text-right py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">수익/손실</th>
                                <th class="text-right py-3 px-4 font-bold text-gray-800 dark:text-gray-200 whitespace-nowrap">수익률 (%)</th>
                            </tr>
                        </thead>
                        <tbody id="trade-history-body">
                            <tr><td colspan="9" class="py-4 text-center text-gray-500 dark:text-gray-400 text-sm">로딩 중...</td></tr>
                </tbody>
            </table>
                </div>
            </div>
        </div>

        <!-- Server Control & Account -->
        <!-- (헤더 바로 아래로 이동된 섹션 - 중복 방지를 위해 이 위치에서는 제거됨) -->

        <!-- Settings & Status (드롭다운 - 맨 아래, 통합 카드) -->
        <div class="grid grid-cols-1 gap-6 mb-8">
            <!-- Settings + Status Card -->
            <div class="card bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-7">
                <h2 class="text-2xl font-extrabold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                        <span class="text-3xl">⚙️</span>
                    <span>설정 & 상태</span>
                    </h2>
                <form id="settings-form" method="post" action="/update-settings" class="space-y-6">
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
                            <option value="{strategy_key}" {'selected' if (state.strategy or 'ai_market_analyzer') == strategy_key else ''}>
                                {strategy_info.get(strategy_key, {}).get('name', strategy_key)}
                            </option>
                            ''' for strategy_key in AVAILABLE_STRATEGIES if strategy_key.startswith('ai_market_analyzer')])}
                </select>
                        <div id="strategy-description" class="mt-2 p-3 bg-gray-50 dark:bg-gray-900 rounded-lg">
                            <p class="text-xs text-gray-600 dark:text-gray-400 mb-1">
                                <strong>{strategy_info.get(state.strategy or 'ai_market_analyzer', {}).get('name', 'AI 시장 분석')}</strong>
                            </p>
                            <p class="text-xs text-gray-500 dark:text-gray-500">
                                {strategy_info.get(state.strategy or 'ai_market_analyzer', {}).get('description', '설명 없음')}
                            </p>
                            <div class="mt-2 flex gap-2">
                                <span class="text-xs px-2 py-1 bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200 rounded">
                                    리스크: {strategy_info.get(state.strategy or 'ai_market_analyzer', {}).get('risk', 'N/A')}
                                </span>
                                <span class="text-xs px-2 py-1 bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 rounded">
                                    적합: {strategy_info.get(state.strategy or 'ai_market_analyzer', {}).get('best_for', 'N/A')}
                                </span>
                            </div>
                        </div>
                    </div>
                    <!-- Market 표시 제거: 5개 코인을 모두 모니터링하므로 단일 market 표시 불필요 -->
                    <input type="hidden" name="market" value="{state.market or 'KRW-BTC'}">
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
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
                            💡 보유 원화의 %를 1건당 매수 금액으로 계산<br/>
                            • 매수: 계산값 &lt; 6,000원이면 6,000원으로 매수<br/>
                            • 매도: 신호 발생 시 무조건 매도 (포지션 &lt; 5,000원이면 추가 매수 후 즉시 판매) (기본값: 3%)
                        </p>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4 border-t border-gray-200 dark:border-gray-700 text-sm">
                <div class="space-y-3">
                    <!-- Current Market 표시 제거: 5개 코인을 모두 모니터링하므로 단일 market 표시 불필요 -->
                    <div class="flex justify-between items-center py-2 border-b border-gray-200 dark:border-gray-700">
                        <span class="text-gray-600 dark:text-gray-400">Current Strategy</span>
                        <span class="font-semibold text-gray-900 dark:text-white">{state.strategy}</span>
                    </div>
                    <div class="flex justify-between items-center py-2 border-b border-gray-200 dark:border-gray-700">
                        <span class="text-gray-600 dark:text-gray-400">💰 Order Size</span>
                        <span class="font-semibold text-gray-900 dark:text-white">{settings.order_amount_pct}%</span>
                    </div>
                        </div>
                        <div class="space-y-3">
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
                    <div class="mt-6">
                        <button 
                            type="submit" 
                            class="btn-primary w-full text-white font-bold py-3 px-6 rounded-xl transition-all duration-200 shadow-lg hover:shadow-xl"
                        >
                            설정 저장
                        </button>
                    </div>
                </form>
            </div>
        </div>

    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <script>
        const STRATEGY_INFO = {json.dumps({k: v for k, v in strategy_info.items()}, ensure_ascii=False)};
        // manual 전략 설명 추가 (사용자가 업비트에서 직접 거래한 내역)
        STRATEGY_INFO['manual'] = {{
            name: '수동 거래',
            description: '사용자가 업비트에서 직접 거래한 내역 (동기화된 거래)',
            risk: '사용자 결정',
            best_for: '수동 거래'
        }};
        let currentChartInstance = null;
        let eventSource = null;

        // AI Q&A 검색창 핸들러
        async function sendAiQuery() {{
            const input = document.getElementById('ai-query-input');
            const resultEl = document.getElementById('ai-query-result');
            if (!input || !resultEl) return;

            const question = input.value.trim();
            if (!question) {{
                resultEl.textContent = '질문을 입력해주세요.';
                resultEl.classList.remove('hidden');
                return;
            }}

            resultEl.textContent = 'AI가 기록을 분석하는 중입니다...';
            resultEl.classList.remove('hidden');

            try {{
                const resp = await fetch('/api/ai/query', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({{ "question": question }}),
                }});

                const data = await resp.json();
                if (!resp.ok) {{
                    resultEl.textContent = data.error || '요청 처리 중 오류가 발생했습니다.';
                    return;
                }}

                resultEl.textContent = data.answer || '응답을 생성하지 못했습니다.';
            }} catch (err) {{
                console.error('AI Q&A 요청 실패:', err);
                resultEl.textContent = '서버와 통신 중 오류가 발생했습니다.';
            }}
        }}

        function initAiQuery() {{
            const input = document.getElementById('ai-query-input');
            const button = document.getElementById('ai-query-button');
            if (button) {{
                button.addEventListener('click', sendAiQuery);
            }}
            if (input) {{
                input.addEventListener('keydown', function(e) {{
                    if (e.key === 'Enter') {{
                        e.preventDefault();
                        sendAiQuery();
                    }}
                }});
            }}
        }}

        document.addEventListener('DOMContentLoaded', initAiQuery);
        
        // SSE 스트림 연결
        function connectEventStream() {{
            if (eventSource) return; // 이미 연결됨
            
            eventSource = new EventSource('/api/stream');
            
            eventSource.onopen = () => {{
                console.log('✅ 실시간 스트림 연결됨');
            }};
            
            eventSource.onmessage = (event) => {{
                try {{
                    const data = JSON.parse(event.data);
                    updateUIWithStreamData(data);
                }} catch (err) {{
                    console.error('Stream data parse error:', err);
                }}
            }};
            
            eventSource.onerror = () => {{
                console.error('❌ 스트림 연결 에러, 5초 후 재연결...');
                if (eventSource) {{
                    eventSource.close();
                    eventSource = null;
                }}
                setTimeout(connectEventStream, 5000);
            }};
        }}
        
        // 스트림 데이터로 UI 업데이트
        function updateUIWithStreamData(data) {{
            try {{
                // Ollama 연결 상태 업데이트 (항상 표시 - 서버 시작/중지와 상관없이)
                const statusBadge = document.getElementById('ollama-status-badge');
                const statusIcon = document.getElementById('ollama-status-icon');
                const statusText = document.getElementById('ollama-status-text');
                
                if (statusBadge && statusIcon && statusText) {{
                    // AI 전략인지 확인 (AI 전략이 아니면 배지 숨기기)
                    const aiStrategies = ['ai_market_analyzer', 'ai_market_analyzer_high_risk'];
                    const isAIStrategy = data.state && aiStrategies.includes(data.state.strategy);
                    
                    if (isAIStrategy) {{
                        statusBadge.style.display = 'flex';
                        
                        // Ollama 상태 명시적 확인 (null/undefined 체크)
                        if (data.ollama_status && typeof data.ollama_status === 'object') {{
                            const connected = data.ollama_status.connected === true;
                            const error = data.ollama_status.error || null;
                            const scannerAvailable = data.ollama_status.scanner_model_available === true;
                            const decisionAvailable = data.ollama_status.decision_model_available === true;
                            const modelAvailable = data.ollama_status.model_available === true;
                            
                            if (connected && modelAvailable) {{
                                // 연결됨 + 두 모델 모두 사용 가능 (모델 이름은 표시하지 않음)
                                statusBadge.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-green-900/30 text-green-400 border border-green-600/50';
                                statusIcon.className = 'w-2 h-2 rounded-full bg-green-400 animate-pulse';
                                statusText.textContent = '✅ Ollama 연결됨';
                            }} else if (connected && (scannerAvailable || decisionAvailable)) {{
                                // 연결됨 + 일부 모델만 사용 가능 (모델 이름 없이 요약만 표시)
                                statusBadge.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-yellow-900/30 text-yellow-400 border border-yellow-600/50';
                                statusIcon.className = 'w-2 h-2 rounded-full bg-yellow-400 animate-pulse';
                                statusText.textContent = '⚠️ Ollama 연결됨 (일부 모델 없음)';
                            }} else if (connected && !scannerAvailable && !decisionAvailable) {{
                                // 연결됨 + 모델 없음 (모델 이름 미표시)
                                statusBadge.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-yellow-900/30 text-yellow-400 border border-yellow-600/50';
                                statusIcon.className = 'w-2 h-2 rounded-full bg-yellow-400 animate-pulse';
                                statusText.textContent = '⚠️ Ollama 연결됨 (사용 가능한 모델 없음)';
                            }} else {{
                                // 연결 안됨
                                statusBadge.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-red-900/30 text-red-400 border border-red-600/50';
                                statusIcon.className = 'w-2 h-2 rounded-full bg-red-400';
                                const errorMsg = error ? ': ' + error : '';
                                statusText.textContent = '❌ Ollama 연결 실패' + errorMsg;
                            }}
                        }} else {{
                            // Ollama 상태 정보가 없거나 잘못된 형식이면 확인 중 상태 유지
                            statusBadge.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-gray-700/50 text-gray-400 border border-gray-600';
                            statusIcon.className = 'w-2 h-2 rounded-full bg-gray-500 animate-pulse';
                            statusText.textContent = 'Ollama 확인 중...';
                        }}
                    }} else {{
                        // AI 전략이 아니면 Ollama 상태 배지 숨기기
                        statusBadge.style.display = 'none';
                    }}
                }}
                
                // 잔액 업데이트 (실시간)
                if (data.balance) {{
                    // KRW 잔액
                    const krwEl = document.getElementById('balance-krw');
                    if (krwEl) {{
                        const krw = data.balance.krw_balance ?? 0;
                        krwEl.textContent = krw.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                    }}
                    
                    // Crypto Value
                    const cryptoEl = document.getElementById('balance-crypto');
                    if (cryptoEl) {{
                        const crypto = data.balance.total_crypto_value ?? 0;
                        cryptoEl.textContent = crypto.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                    }}
                    
                    // Total Balance
                    const totalEl = document.getElementById('balance-total');
                    if (totalEl) {{
                        const total = data.balance.total_balance ?? ((data.balance.krw_balance ?? 0) + (data.balance.total_crypto_value ?? 0));
                        totalEl.textContent = total.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                    }}
                    
                    // 자산 현황 테이블 업데이트 (accounts 데이터가 있으면)
                    if (data.balance.accounts && Array.isArray(data.balance.accounts)) {{
                        // 비동기로 업데이트 (성능 최적화)
                        setTimeout(() => updateAccountsTable(data.balance.accounts), 100);
                    }}
                }}
                
                    // 통계 데이터 실시간 업데이트
                    if (data.statistics) {{
                        updateStatistics(data.statistics);
                    }}
                    
                    // 거래 내역 실시간 업데이트
                    if (data.recent_trades && Array.isArray(data.recent_trades)) {{
                        updateTradeHistory(data.recent_trades);
                    }}
                
                // 서버 상태 업데이트 (페이지 상단 + 서버 제어 창 동기화)
                if (data.state) {{
                    // running 값 명시적 확인 (undefined/null 처리)
                    const isRunning = data.state.running === true;
                    
                    // 페이지 상단 서버 상태 업데이트
                    const statusDot = document.getElementById('server-status-dot');
                    const statusText = document.getElementById('server-status-text');
                    if (statusDot && statusText) {{
                        if (isRunning) {{
                            statusDot.classList.add('bg-green-500', 'animate-pulse');
                            statusDot.classList.remove('bg-red-500');
                            statusText.textContent = '🟢 동작 중';
                            statusText.classList.add('text-green-600', 'dark:text-green-400');
                            statusText.classList.remove('text-red-600', 'dark:text-red-400');
                        }} else {{
                            statusDot.classList.remove('bg-green-500', 'animate-pulse');
                            statusDot.classList.add('bg-red-500');
                            statusText.textContent = '🔴 중지됨';
                            statusText.classList.remove('text-green-600', 'dark:text-green-400');
                            statusText.classList.add('text-red-600', 'dark:text-red-400');
                        }}
                    }}
                    
                    // 거래 모드 업데이트 (서버 제어 창 + 페이지 상단) - 명시적 값 확인
                    // data.state.dry_run 값 명시적 확인 (false도 명시적으로 처리)
                    const isDryRun = data.state.dry_run === true || (data.state.dry_run !== false && data.state.dry_run !== undefined && data.state.dry_run !== null);
                    updateTradingModeBadge(isDryRun);
                    
                    // 마지막 실행 시간 업데이트
                    const lastRunEl = document.getElementById('last-run-time');
                    if (lastRunEl) {{
                        const lastRun = data.state.last_run;
                        if (lastRun) {{
                            try {{
                                const runTime = new Date(lastRun);
                                const now = new Date();
                                const diff = Math.round((now - runTime) / 1000);
                                if (diff < 60) {{
                                    lastRunEl.textContent = diff + '초 전';
                                }} else if (diff < 3600) {{
                                    lastRunEl.textContent = Math.round(diff / 60) + '분 전';
                                }} else {{
                                    lastRunEl.textContent = runTime.toLocaleTimeString('ko-KR', {{hour: '2-digit', minute: '2-digit'}});
                                }}
                            }} catch (e) {{
                                lastRunEl.textContent = lastRun;
                            }}
                        }} else {{
                            lastRunEl.textContent = '-';
                        }}
                    }}
                    
                    // 마지막 신호 업데이트
                    const lastSignalEl = document.getElementById('last-signal-badge');
                    if (lastSignalEl) {{
                        const signal = data.state.last_signal ?? 'HOLD';
                        lastSignalEl.textContent = signal;
                        // 신호에 따른 색상 변경
                        if (signal === 'BUY' || signal.toUpperCase() === 'BUY') {{
                            lastSignalEl.className = 'font-semibold text-green-600 dark:text-green-400';
                        }} else if (signal === 'SELL' || signal.toUpperCase() === 'SELL') {{
                            lastSignalEl.className = 'font-semibold text-red-600 dark:text-red-400';
                        }} else {{
                            lastSignalEl.className = 'font-semibold text-gray-600 dark:text-gray-400';
                        }}
                    }}
                }}
                
                // AI 분석 결과 표시 (AI 전략이면 항상 표시)
                const aiStrategies = ['ai_market_analyzer', 'ai_market_analyzer_high_risk'];
                if (data.state && aiStrategies.includes(data.state.strategy)) {{
                    // AI 전략이면 항상 분석 결과 표시 (결과가 없어도 상태 표시)
                if (data.ai_analysis) {{
                    const analysis = data.ai_analysis;
                    const selectedMarket = analysis.selected_market || 'N/A';
                    let signal = analysis.signal || 'HOLD';
                    
                    // signal 값 정규화 (StrategySignal enum -> string)
                    if (typeof signal === 'object' && signal.value) {{
                        signal = signal.value;
                    }} else if (typeof signal === 'string') {{
                        // 'StrategySignal.BUY' 형태에서 'BUY' 추출
                        signal = signal.replace('StrategySignal.', '').replace('StrategySignal', '').replace('.', '').trim();
                    }}
                    
                    const confidence = (analysis.confidence || 0) * 100;
                    const marketData = analysis.market_data || {{}};
                    const status = analysis.status;
                    const analysis_in_progress = status === 'analyzing' || status === 'waiting';
                    
                        const consoleEl = document.getElementById('ai-console-content');
                        if (consoleEl) {{
                            // 타임스탬프 생성
                            const timestamp = analysis.timestamp ? new Date(analysis.timestamp).toLocaleTimeString('ko-KR', {{hour: '2-digit', minute: '2-digit', second: '2-digit'}}) : new Date().toLocaleTimeString('ko-KR', {{hour: '2-digit', minute: '2-digit', second: '2-digit'}});
                            const coinName = selectedMarket.replace('KRW-', '') || 'N/A';
                            
                            // 분석 결과가 없는 경우 또는 실패한 경우
                            if (status === 'analyzing') {{
                                // 분석 중이면 대기 메시지 유지 또는 생성
                                let waitingEl = document.getElementById('ai-console-waiting');
                                if (!waitingEl) {{
                                    waitingEl = document.createElement('div');
                                    waitingEl.id = 'ai-console-waiting';
                                    waitingEl.className = 'text-gray-500 flex items-center gap-2';
                                    consoleEl.appendChild(waitingEl);
                                }}
                                // 분석 중일 때는 코인 이름 대신 "다중 코인" 표시 (BTC 등 기본값 방지)
                                const displayName = (selectedMarket === 'N/A' || coinName === 'N/A') 
                                    ? '다중 코인' 
                                    : coinName;
                                waitingEl.innerHTML = '<span class="animate-spin">🔄</span><span>[' + timestamp + '] ' + displayName + ' | AI 분석 실행 중... (잠시만 기다려주세요)</span>';
                            }} else {{
                                // 분석이 완료되면 대기 메시지 제거
                                const waitingEl = document.getElementById('ai-console-waiting');
                                if (waitingEl) {{
                                    waitingEl.remove();
                                }}
                                
                                if (status === 'ollama_disconnected' || analysis.ollama_status === 'disconnected' || 
                                    analysis.ollama_status === 'timeout' || analysis.ollama_status === 'error' ||
                                    analysis.ollama_status === 'model_missing') {{
                                    let errorMsg = '❌ Ollama 서버 연결 실패';
                                    if (analysis.ollama_error) {{
                                        errorMsg += ': ' + analysis.ollama_error;
                                    }}
                                    if (analysis.ollama_status === 'disconnected' || analysis.ollama_status === 'timeout') {{
                                        errorMsg += ' - 서버 Ollama 서버 상태를 확인해주세요.';
                                    }}
                                    const message = '[' + timestamp + '] ' + coinName + ' | ' + errorMsg;
                                    addAIConsoleMessage(message, 'red');
                                    
                                    // Ollama 알림 표시
                                    const alertEl = document.getElementById('ollama-alert');
                                    if (alertEl) {{
                                        alertEl.classList.remove('hidden');
                                    }}
                                }} else {{
                                    // Ollama 연결 정상이면 알림 숨김
                                    const alertEl = document.getElementById('ollama-alert');
                                    if (alertEl) {{
                                        alertEl.classList.add('hidden');
                                    }}
                                }}
                                
                                if (status === 'waiting') {{
                                    // 분석 대기 중이면 분석 실행 메시지 표시 (분석이 곧 시작됨)
                                    const waitingEl = document.getElementById('ai-console-waiting');
                                    if (!waitingEl) {{
                                        const newWaitingEl = document.createElement('div');
                                        newWaitingEl.id = 'ai-console-waiting';
                                        newWaitingEl.className = 'text-gray-500 flex items-center gap-2';
                                        consoleEl.appendChild(newWaitingEl);
                                    }}
                                    const waitingElToUpdate = document.getElementById('ai-console-waiting');
                                    if (waitingElToUpdate) {{
                                        waitingElToUpdate.innerHTML = '<span class="animate-spin">🔄</span><span>[' + timestamp + '] ' + coinName + ' | AI 분석 시작 중... (잠시만 기다려주세요)</span>';
                                    }}
                                }} else if (status === 'stopped') {{
                                    const lastRun = (data.state && data.state.last_run) ? data.state.last_run : '아직 없음';
                                    const message = '[' + timestamp + '] ' + coinName + ' | ⚠️ 서버 중지됨 (마지막 실행: ' + lastRun + ')';
                                    addAIConsoleMessage(message, 'gray');
                                }} else if (status === 'insufficient_data') {{
                                    const message = '[' + timestamp + '] ' + coinName + ' | ⚠️ 데이터 부족 (최소 5개 캔들 필요)';
                                    addAIConsoleMessage(message, 'yellow');
                                }} else if (status === 'calculation_failed') {{
                                    const message = '[' + timestamp + '] ' + coinName + ' | ⚠️ 기술적 지표 계산 실패';
                                    addAIConsoleMessage(message, 'yellow');
                                }}
                        
                        // 분석 진행 상태 확인 및 표시
                        if (status === 'analyzing' || analysis_in_progress) {{
                            // 분석 단계별 메시지 표시
                            const firstRoundCount = analysis.first_round_count || analysis.decision?.first_round_count || 0;
                            const secondRoundCount = analysis.second_round_count || analysis.decision?.second_round_count || 0;
                            
                            let analyzingMsg = '';
                            if (firstRoundCount === 0 && secondRoundCount === 0) {{
                                // 1차 분석 대기 중
                                analyzingMsg = '[' + timestamp + '] 🔄 1차 분석 대기 중... (거래량 상위 30개 스캔 중, 예상 소요: 30-60초)';
                            }} else if (firstRoundCount > 0 && secondRoundCount === 0) {{
                                // 2차 분석 대기 중 (AI 사용)
                                analyzingMsg = '[' + timestamp + '] 🔄 2차 Ollama 분석 중... (1차 ' + firstRoundCount + '개 완료, 30개 중 10개 선정 중, 예상 소요: 20-40초)';
                            }} else if (firstRoundCount > 0 && secondRoundCount > 0) {{
                                // 3차 분석 진행 중 (매매 시그널 분석)
                                analyzingMsg = '[' + timestamp + '] 🔄 3차 Ollama 매매 시그널 분석 중... (1차 ' + firstRoundCount + '개 → 2차 ' + secondRoundCount + '개 완료, 매매 예정 5개에 대한 시그널 분석, 예상 소요: 10-30초)';
                            }} else {{
                                // 기본 메시지
                                analyzingMsg = '[' + timestamp + '] 🔄 AI 분석 진행 중... (Ollama 서버 응답 대기 중)';
                            }}
                            addAIConsoleMessage(analyzingMsg, 'cyan');
                        }}
                        
                        // 2차 선정 10개 표시 (AI 분석 콘솔)
                        const secondRoundCandidates = analysis.second_round_candidates || analysis.decision?.second_round_candidates || [];
                        const coinAnalyses = analysis.coin_analyses || analysis.scanner_result || analysis.decision?.coin_analyses || {{}};
                        
                        // 분석 결과가 있는지 확인
                        const hasAnalysisData = (secondRoundCandidates && secondRoundCandidates.length > 0) || 
                                               (coinAnalyses && Object.keys(coinAnalyses).length > 0);
                        
                        if (secondRoundCandidates && secondRoundCandidates.length > 0) {{
                            // 2차 선정 10개를 AI 분석 콘솔에 표시
                            const firstRoundCount = analysis.first_round_count || analysis.decision?.first_round_count || 0;
                            const secondRoundCount = analysis.second_round_count || analysis.decision?.second_round_count || secondRoundCandidates.length;
                            
                            // 단계별 선정 정보 표시
                            const summaryMessage = '[' + timestamp + '] 📊 선정 과정: 1차 ' + firstRoundCount + '개 → 2차 ' + secondRoundCount + '개 (점수 및 거래량 기준)';
                            addAIConsoleMessage(summaryMessage, 'cyan');
                            
                            // 2차 선정 10개 표시
                            secondRoundCandidates.forEach((candidate, index) => {{
                                const market = candidate.market || '';
                                const coinName = market.replace('KRW-', '');
                                const baseScore = ((candidate.base_score || candidate.score || 0) * 100).toFixed(1);
                                const scoreEff = ((candidate.score_eff || candidate.score || 0) * 100).toFixed(1);
                                const reason = candidate.reason || '분석 중';
                                const trend = candidate.trend || 'unknown';
                                const risk = candidate.risk || 'medium';
                                const isSelected = market === selectedMarket;
                                
                                // 선택된 코인은 강조 표시
                                const prefix = isSelected ? '⭐ ' : '  ';
                                const rank = (index + 1) + '.';
                                const trendEmoji = trend === 'uptrend' ? '📈' : trend === 'downtrend' ? '📉' : '➡️';
                                const riskColor = risk === 'high' ? 'red' : risk === 'medium' ? 'yellow' : 'green';
                                const exposureInfo = candidate.exposure_pct ? ' | 노출: ' + candidate.exposure_pct.toFixed(1) + '%' : '';
                                const message = '[' + timestamp + '] ' + prefix + rank + ' ' + coinName + ' | 기본점수: ' + baseScore + '% | 효과점수: ' + scoreEff + '% | ' + trendEmoji + ' ' + trend + ' | 리스크: ' + risk + exposureInfo + ' | 이유: ' + reason;
                                addAIConsoleMessage(message, isSelected ? 'yellow' : riskColor);
                            }});
                        }} else if (coinAnalyses && Object.keys(coinAnalyses).length > 0) {{
                            // Fallback: 기존 coin_analyses 사용 (레거시 모드)
                            const sortedCoins = Object.entries(coinAnalyses)
                                .sort((a, b) => ((b[1].score || 0) - (a[1].score || 0)))
                                .slice(0, 10);
                            
                            sortedCoins.forEach(([market, data]) => {{
                                const coinName = market.replace('KRW-', '');
                                const score = ((data.score || 0) * 100).toFixed(1);
                                const reason = data.reason || '분석 중';
                                const trend = data.trend || 'unknown';
                                const risk = data.risk || 'medium';
                                const isSelected = market === selectedMarket;
                                
                                const prefix = isSelected ? '⭐ ' : '  ';
                                const trendEmoji = trend === 'uptrend' ? '📈' : trend === 'downtrend' ? '📉' : '➡️';
                                const riskColor = risk === 'high' ? 'red' : risk === 'medium' ? 'yellow' : 'green';
                                const message = '[' + timestamp + '] ' + prefix + coinName + ' | 점수: ' + score + '% | ' + trendEmoji + ' ' + trend + ' | 리스크: ' + risk + ' | 이유: ' + reason;
                                addAIConsoleMessage(message, isSelected ? 'yellow' : riskColor);
                            }});
                        }} else if (!hasAnalysisData && status !== 'analyzing' && !analysis_in_progress) {{
                            // 분석 결과가 없고 분석 중이 아닐 때만 메시지 표시
                            if (status === 'no_analysis' || status === 'ollama_disconnected') {{
                                const message = '[' + timestamp + '] ⚠️ AI 분석 결과 없음 - Ollama 서버 연결 확인 필요';
                                addAIConsoleMessage(message, 'yellow');
                            }} else {{
                                const message = '[' + timestamp + '] ⚠️ AI 분석 결과 없음 - 분석이 완료되지 않았거나 데이터가 없습니다';
                                addAIConsoleMessage(message, 'yellow');
                            }}
                        }}
                        
                        // 최종 선정 5개 표시 (매매 예정 콘솔) - 보유 중인 코인 고정 표시
                        const finalCandidates = analysis.final_candidates || analysis.decision?.final_candidates || [];
                        const pendingContentEl = document.getElementById('trading-pending-content');
                        const pendingWaitingEl = document.getElementById('trading-pending-waiting');
                        
                        // 대기 메시지 제거
                        if (pendingWaitingEl) {{
                            pendingWaitingEl.remove();
                        }}
                        
                        // 매매 예정 콘솔에 표시
                        if (pendingContentEl) {{
                            // 기존 내용 초기화 (최신만 유지)
                            pendingContentEl.innerHTML = '';
                            
                            // 현재 보유 중인 포지션 가져오기
                            const openPositions = data.portfolio?.open_positions || [];
                            const heldMarkets = new Set(openPositions.map(p => p.market));
                            
                            // 보유 중인 코인과 새로운 후보 통합
                            const fixedCandidates = [];  // 보유 중인 코인 (고정)
                            const dynamicCandidates = [];  // 새로운 후보 (동적)
                            
                            // final_candidates에서 보유 중인 코인 분리
                            finalCandidates.forEach(candidate => {{
                                const market = candidate.market || '';
                                if (heldMarkets.has(market)) {{
                                    fixedCandidates.push({{...candidate, isFixed: true, isHeld: true}});
                                }} else {{
                                    dynamicCandidates.push(candidate);
                                }}
                            }});
                            
                            // 보유 중이지만 final_candidates에 없는 코인 추가 (분석에서 제외되었지만 보유 중)
                            openPositions.forEach(pos => {{
                                const market = pos.market;
                                if (market && !fixedCandidates.find(c => c.market === market)) {{
                                    const positionValue = pos.current_value || 0;
                                    const entryPrice = pos.entry_price || 0;
                                    const currentPrice = pos.current_price || entryPrice;
                                    const pnl = pos.pnl || 0;
                                    const pnlPct = entryPrice > 0 ? ((currentPrice - entryPrice) / entryPrice * 100) : 0;
                                    
                                    fixedCandidates.push({{
                                        market: market,
                                        score: 0,
                                        score_eff: 0,
                                        base_score: 0,
                                        trend: 'unknown',
                                        risk: 'medium',
                                        reason: '보유 중 (매도 대기)',
                                        isFixed: true,
                                        isHeld: true,
                                        position_value: positionValue,
                                        pnl: pnl,
                                        pnl_pct: pnlPct
                                    }});
                                }}
                            }});
                            
                            // 최종 후보 리스트: 고정 코인 + 동적 코인 (최대 5개)
                            const allCandidates = [...fixedCandidates, ...dynamicCandidates].slice(0, 5);
                            const finalCount = allCandidates.length;
                            
                            // 0개일 때는 표시하지 않음
                            if (finalCount === 0) {{
                                return;
                            }}
                            
                            const fixedCount = fixedCandidates.length;
                            const dynamicCount = Math.min(dynamicCandidates.length, 5 - fixedCount);
                            
                            // 동적 모니터링 상태 가져오기 (타이밍 정보)
                            const monitoringStatus = data.monitoring_status || {{}};
                            const timings = monitoringStatus.timings || {{}};
                            const signals = monitoringStatus.signals || {{}};
                            
                            const headerMessage = '[' + timestamp + '] 🎯 매매 예정: ' + finalCount + '개 (보유 ' + fixedCount + '개 고정 🔒 + 신규 ' + dynamicCount + '개)';
                            addTradingPendingMessage(headerMessage, 'cyan');
                            
                            // 5개 코인을 각각 독립적으로 표시
                            allCandidates.forEach((candidate, i) => {{
                                const rank = (i + 1) + '.';
                                const market = candidate.market || '';
                                const coinName = market.replace('KRW-', '');
                                const baseScore = ((candidate.base_score || candidate.score || 0) * 100).toFixed(1);
                                const scoreEff = ((candidate.score_eff || candidate.score || 0) * 100).toFixed(1);
                                const trend = candidate.trend || 'unknown';
                                const risk = candidate.risk || 'medium';
                                const isSelected = market === selectedMarket;
                                const isFixed = candidate.isFixed || false;
                                const isHeld = candidate.isHeld || false;
                                
                                // AI 타이밍 정보 (동적 모니터링에서 가져오기)
                                const timingInfo = timings[market] || {{}};
                                const buyTiming = candidate.buy_timing || timingInfo.buy_timing || 'wait';
                                const buySignal = candidate.buy_signal || timingInfo.buy_signal || 'none';
                                const timingReason = candidate.timing_reason || timingInfo.timing_reason || '';
                                const entrySignal = signals[market];
                                
                                // 타이밍별 이모지 및 상태
                                let timingEmoji = '⏸️';
                                let timingText = '대기 중';
                                let timingColor = 'gray';
                                if (buyTiming === 'now') {{
                                    timingEmoji = '🟢';
                                    timingText = '즉시 매수';
                                    timingColor = 'green';
                                }} else if (buyTiming === 'watch') {{
                                    timingEmoji = '👀';
                                    timingText = '관찰 중';
                                    timingColor = 'yellow';
                                }} else if (buyTiming === 'wait') {{
                                    timingEmoji = '⏳';
                                    timingText = '대기 중';
                                    timingColor = 'gray';
                                }}
                                
                                // 신호 강도 표시
                                let signalStrength = '';
                                if (buySignal === 'strong') {{
                                    signalStrength = ' | 신호: 🔥 강함';
                                }} else if (buySignal === 'medium') {{
                                    signalStrength = ' | 신호: ⚡ 보통';
                                }} else if (buySignal === 'weak') {{
                                    signalStrength = ' | 신호: 💤 약함';
                                }}
                                
                                // entry_signal이 있으면 매매 진행 중
                                let actionStatus = '';
                                if (entrySignal) {{
                                    actionStatus = ' | 🚀 매매 진행 중';
                                    timingColor = 'green';
                                }}
                                
                                // 고정 표시 (보유 중)
                                const fixedIcon = isFixed ? '🔒 ' : '';
                                const fixedText = isFixed ? ' (보유 중)' : '';
                                
                                // 선택된 코인은 강조 표시
                                const prefix = isSelected ? '🔥 ' : (isFixed ? fixedIcon : '  ');
                                const trendEmoji = trend === 'uptrend' ? '📈' : trend === 'downtrend' ? '📉' : '➡️';
                                const riskColor = risk === 'high' ? 'red' : risk === 'medium' ? 'yellow' : 'green';
                                const exposureInfo = candidate.exposure_pct ? ' | 노출: ' + candidate.exposure_pct.toFixed(1) + '%' : '';
                                
                                // 보유 중인 코인의 수익률 정보
                                let pnlInfo = '';
                                if (isHeld && candidate.pnl_pct !== undefined) {{
                                    const pnlSign = candidate.pnl_pct >= 0 ? '+' : '';
                                    pnlInfo = ' | 수익률: ' + pnlSign + candidate.pnl_pct.toFixed(2) + '%';
                                }}
                                
                                let scoreInfo = '';
                                if (isHeld && candidate.score === 0) {{
                                    scoreInfo = ' | 점수: 분석 제외';
                                }} else {{
                                    scoreInfo = ' | 기본점수: ' + baseScore + '% | 효과점수: ' + scoreEff + '%';
                                }}
                                
                                // 각 코인별 구분선 및 정보
                                const separator = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ timingReason + ' | ' + trendEmoji + ' ' + trend + ' | 리스크: ' + risk + exposureInfo + pnlInfo;
                                addTradingPendingMessage(message, timingColor);
                                
                                // 타이밍 이유 표시
                                if (timingReason) {{
                                    const reasonMessage = '    └─ 이유: ' + timingReason;
                                    addTradingPendingMessage(reasonMessage, 'gray');
                                }}
                            }})
                            
                            // 최종 결정 요약 표시 (매매 예정 콘솔에만)
                            // final_candidates가 있을 때만 최종 결정 표시 (후보 부족 시 표시 안 함)
                            if (finalCandidates.length > 0 && selectedMarket && selectedMarket !== 'N/A' && signal && signal !== 'HOLD') {{
                                // 신호에 따른 이모지와 색상
                                let signalEmoji = '⚪';
                                let signalColor = 'gray';
                                if (signal === 'BUY' || signal.toUpperCase() === 'BUY') {{
                                    signalEmoji = '🟢';
                                    signalColor = 'green';
                                }} else if (signal === 'SELL' || signal.toUpperCase() === 'SELL') {{
                                    signalEmoji = '🔴';
                                    signalColor = 'red';
                                }}
                                
                                // 최종 결정 메시지
                                let decisionMessage;
                                if (marketData && Object.keys(marketData).length > 0 && marketData.current_price) {{
                                    const price = Math.floor(marketData.current_price || 0).toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                                    const vol = (marketData.volatility || 0).toFixed(2);
                                    const volRatio = (marketData.volume_ratio || 0).toFixed(2);
                                    decisionMessage = '[' + timestamp + '] ⭐ 최종 결정: ' + selectedMarket.replace('KRW-', '') + ' | ' + signalEmoji + ' ' + signal + ' (신뢰도: ' + confidence.toFixed(1) + '%) | 가격: ' + price + '원 | 변동성: ' + vol + '% | 거래량: ' + volRatio + 'x';
                                }} else {{
                                    decisionMessage = '[' + timestamp + '] ⭐ 최종 결정: ' + selectedMarket.replace('KRW-', '') + ' | ' + signalEmoji + ' ' + signal + ' (신뢰도: ' + confidence.toFixed(1) + '%)';
                                }}
                                addTradingPendingMessage(decisionMessage, signalColor);
                            }} else if (finalCandidates.length === 0 && selectedMarket && selectedMarket !== 'N/A' && signal && signal !== 'HOLD') {{
                                // final_candidates가 없으면 최종 결정을 표시하지 않음 (분석 중 또는 후보 부족)
                                const decisionMessage = '[' + timestamp + '] ⚠️ 최종 선정 대기 중... (후보 부족 또는 분석 진행 중)';
                                addTradingPendingMessage(decisionMessage, 'gray');
                            }} else if (signal === 'HOLD' && finalCandidates.length > 0) {{
                                const decisionMessage = '[' + timestamp + '] ⚪ 최종 결정: HOLD (신뢰도: ' + confidence.toFixed(1) + '%)';
                                addTradingPendingMessage(decisionMessage, 'gray');
                            }}
                            
                            // 카운트 업데이트
                            const countEl = document.getElementById('trading-pending-count');
                            if (countEl) {{
                                // 0개일 때는 표시하지 않음 (빈 문자열)
                                countEl.textContent = finalCandidates.length > 0 ? finalCandidates.length : '';
                            }}
                        }} else {{
                            // pendingContentEl이 없으면 대기 메시지 표시
                            if (pendingWaitingEl) {{
                                pendingWaitingEl.style.display = 'block';
                            }}
                            const countEl = document.getElementById('trading-pending-count');
                            if (countEl) {{
                                // 0개일 때는 표시하지 않음 (빈 문자열)
                                countEl.textContent = '';
                            }}
                        }}
                                    
                                    // Ollama 연결 정상이면 알림 숨김
                                    const alertEl = document.getElementById('ollama-alert');
                                    if (alertEl) {{
                                        alertEl.classList.add('hidden');
                                    }}
                                }}
                            }}
                        }}
                    }} else {{
                        // AI 전략이지만 ai_analysis가 없는 경우 (서버 시작 직후 또는 분석 실행 중)
                        const consoleEl = document.getElementById('ai-console-content');
                        if (consoleEl) {{
                            // 대기 메시지가 없으면 추가
                            let waitingEl = document.getElementById('ai-console-waiting');
                            if (!waitingEl) {{
                                waitingEl = document.createElement('div');
                                waitingEl.id = 'ai-console-waiting';
                                waitingEl.className = 'text-gray-500 flex items-center gap-2';
                                consoleEl.appendChild(waitingEl);
                            }}
                            
                            // Ollama 연결 상태 확인 (data.ai_analysis가 없을 수도 있음)
                            const ollamaStatus = (data.ai_analysis && data.ai_analysis.ollama_status) ? data.ai_analysis.ollama_status : 'unknown';
                            let statusText = 'AI 분석 초기화 중...';
                            
                            if (ollamaStatus === 'disconnected' || ollamaStatus === 'timeout') {{
                                statusText = 'Ollama 서버 연결 실패 - 분석 불가';
                            }} else if (ollamaStatus === 'model_missing') {{
                                statusText = '필요한 모델 없음 - 분석 불가';
                            }} else if (ollamaStatus === 'connected') {{
                                statusText = '분석 실행 중...';
                            }} else {{
                                statusText = 'AI 분석 초기화 중...';
                            }}
                            
                            const now = new Date().toLocaleTimeString('ko-KR', {{hour: '2-digit', minute: '2-digit', second: '2-digit'}});
                            waitingEl.innerHTML = '<span class="animate-spin">🔄</span><span>[' + now + '] ' + statusText + '</span>';
                        }}
                    }}
                }}
            catch (err) {{
                console.error('Stream update error:', err);
            }}
        }}
        
        // SSE 스트림 연결 중복 호출 제거 (이미 2221줄에서 호출됨)
        
        // 자산 현황 테이블 업데이트 함수
        async function updateAccountsTable(accounts) {{
            try {{
                const tbody = document.querySelector('#account-snapshot tbody') || document.querySelector('table tbody');
                if (!tbody) return;
                
                // 거래 가능한 코인만 필터링
                const tradableAccounts = accounts.filter(entry => {{
                    const currency = entry.currency || '';
                    if (currency === 'KRW') return false;
                    const balance = parseFloat(entry.balance || 0);
                    if (balance <= 0) return false;
                    if (['LUNC', 'APENFT', 'LUNA2', 'DOGE', 'SHIB'].includes(currency)) return false;
                    return true;
                }});
                
                // 각 코인 행 업데이트
                const rows = tbody.querySelectorAll('tr');
                const coinRowMap = {{}};
                // 유효하지 않은 코인 이름 저장 (404 에러 방지)
                const invalidCoins = new Set();
                
                rows.forEach(row => {{
                    const coinText = row.querySelector('td')?.textContent.trim().split(' ')[0];
                    // 유효성 검사: 영문/숫자로만 구성된 코인만 허용 (최소 2자, 최대 10자)
                    if (coinText && !coinText.includes('보유한') && /^[A-Z0-9]{{2,10}}$/.test(coinText)) {{
                        coinRowMap[coinText] = row;
                    }}
                }});
                
                // 각 코인 데이터 업데이트
                for (const entry of tradableAccounts) {{
                    const currency = entry.currency || '';
                    
                    // 유효성 검사: 영문/숫자로만 구성된 코인만 허용
                    if (!/^[A-Z0-9]{{2,10}}$/.test(currency)) {{
                        console.debug('Invalid coin name skipped: ' + currency);
                        continue;
                    }}
                    
                    // 이전에 404 에러를 받은 코인은 스킵
                    if (invalidCoins.has(currency)) {{
                        continue;
                    }}
                    
                    const market = `KRW-${{currency}}`;
                    
                    try {{
                        // 현재가 조회
                        const response = await fetch(`/chart/${{currency}}?candles=1`);
                        if (!response.ok) {{
                            // 404나 500 에러면 스킵 (로그만 남기고 계속 진행)
                            if (response.status === 404 || response.status === 500) {{
                                console.debug('Chart data not available for ' + currency + ': HTTP ' + response.status);
                                invalidCoins.add(currency); // 재시도 방지
                                continue;
                            }}
                            throw new Error('HTTP ' + response.status);
                        }}
                        const chartData = await response.json();
                        
                        // 에러 응답 체크
                        if (chartData.error) {{
                            console.debug(`Chart data error for ${{currency}}: ${{chartData.error}}`);
                            continue;
                        }}
                        
                        const balance = parseFloat(entry.balance || 0);
                        const avgBuyPrice = parseFloat(entry.avg_buy_price || 0);
                        let currentPrice = avgBuyPrice;
                        
                        if (chartData.data && chartData.data.length > 0) {{
                            currentPrice = chartData.data[chartData.data.length - 1].close;
                        }}
                        
                        const currentValue = balance * currentPrice;
                        const purchaseAmount = balance * avgBuyPrice;
                        
                        // 기존 행 찾기 또는 새 행 생성
                        let row = coinRowMap[currency];
                        if (!row) {{
                            // 새 행 생성 (필요 시)
                            continue;
                        }}
                        
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 5) {{
                            // 코인명 (이미 있음)
                            // 보유량
                            cells[1].textContent = balance.toFixed(8);
                            // 구매금액
                            cells[2].textContent = purchaseAmount.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                            // 현재가치
                            cells[3].textContent = currentValue.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                            // 수익/손실
                            const pnl = currentValue - purchaseAmount;
                            cells[4].textContent = pnl.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                            
                            // 수익/손실에 따른 색상 변경
                            if (pnl > 0) {{
                                cells[4].className = 'py-3 px-4 text-right font-medium text-green-600 dark:text-green-400';
                            }} else if (pnl < 0) {{
                                cells[4].className = 'py-3 px-4 text-right font-medium text-red-600 dark:text-red-400';
                            }} else {{
                                cells[4].className = 'py-3 px-4 text-right font-medium text-gray-600 dark:text-gray-400';
                            }}
                            
                            // 현재가치 색상
                            if (currentValue > purchaseAmount) {{
                                cells[3].className = 'py-3 px-4 text-right font-medium text-green-600 dark:text-green-400';
                            }} else if (currentValue < purchaseAmount) {{
                                cells[3].className = 'py-3 px-4 text-right font-medium text-red-600 dark:text-red-400';
                            }} else {{
                                cells[3].className = 'py-3 px-4 text-right font-medium text-gray-600 dark:text-gray-400';
                            }}
                        }}
                    }} catch (err) {{
                        console.debug('Failed to update ' + currency + ':', err);
                    }}
                }}
            }} catch (err) {{
                console.error('Failed to update accounts table:', err);
            }}
        }}
        
        // 실시간 현재가 업데이트 (기존 함수)
        let isUpdating = false;  // 업데이트 락
        async function updateAccountValues() {{
            // 이미 업데이트 중이면 스킵
            if (isUpdating) {{
                console.debug('Update already in progress, skipping...');
                return;
            }}
            
            isUpdating = true;
            try {{
                // 자산 현황 테이블 찾기 (account-snapshot 또는 첫 번째 테이블)
                const table = document.querySelector('#account-snapshot tbody') || document.querySelector('table tbody');
                if (!table) {{
                    console.debug('Table tbody not found');
                    return;
                }}
                
                const rows = table.querySelectorAll('tr');
                if (rows.length === 0) {{
                    console.debug('No rows found in table');
                    return;
                }}
                console.debug(`Found ${{rows.length}} rows to check`);
                
                for (const row of rows) {{
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 5) {{
                        console.debug(`Row skipped: only ${{cells.length}} cells (need 5)`);
                        continue;
                    }}
                    
                    // 코인명 추출
                    let coinText = cells[0].textContent.trim();
                    // 공백 제거
                    coinText = coinText.replace(/\\s+/g, '').trim();
                    if (!coinText || coinText === '보유한' || coinText === '거래') continue;
                    
                    // 유효성 검사: 영문/숫자로만 구성된 코인만 허용
                    if (!/^[A-Z0-9]{{2,10}}$/.test(coinText)) {{
                        console.debug('Invalid coin name skipped: ' + coinText);
                        continue;
                    }}
                    
                    try {{
                        // 현재가 조회
                        const response = await fetch(`/chart/${{coinText}}?candles=1`);
                        if (!response.ok) {{
                            // 404나 500 에러면 스킵 (로그만 남기고 계속 진행)
                            if (response.status === 404 || response.status === 500) {{
                                console.debug('Chart data not available for ' + coinText + ': HTTP ' + response.status);
                                continue;
                            }}
                            throw new Error('HTTP ' + response.status);
                        }}
                        const data = await response.json();
                        
                        // 에러 응답 체크
                        if (data.error || !data.data || data.data.length === 0) {{
                            console.debug(`Chart data error for ${{coinText}}: ${{data.error || 'No data'}}`);
                            continue;
                        }}
                        
                        // 보유량 파싱 (쉼표 제거 후 파싱)
                        const balanceText = cells[1].textContent.trim().replace(/,/g, '');
                        const balance = parseFloat(balanceText);
                        if (isNaN(balance) || balance <= 0) {{
                            console.debug(`Invalid balance for ${{coinText}}: ${{balanceText}}`);
                            continue;
                        }}
                        
                        // 현재가 추출
                        const currentPrice = data.data[data.data.length - 1].close;
                        if (!currentPrice || currentPrice <= 0) {{
                            console.debug(`Invalid price for ${{coinText}}: ${{currentPrice}}`);
                            continue;
                        }}
                        
                        const currentValue = balance * currentPrice;
                        
                        // 구매금액 파싱 (쉼표 제거 후 파싱)
                        const purchaseText = cells[2].textContent.trim().replace(/,/g, '');
                        const purchaseValue = parseFloat(purchaseText);
                        if (isNaN(purchaseValue) || purchaseValue <= 0) {{
                            console.debug(`Invalid purchase value for ${{coinText}}: ${{purchaseText}}`);
                            continue;
                        }}
                        
                        const pnl = currentValue - purchaseValue;
                        
                        console.debug(`Updating ${{coinText}}: balance=${{balance}}, price=${{currentPrice}}, currentValue=${{currentValue}}, purchaseValue=${{purchaseValue}}, pnl=${{pnl}}`);
                        
                        // 현재가치 업데이트
                        cells[3].textContent = currentValue.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                        
                        // 수익/손실 업데이트
                        cells[4].textContent = pnl.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                        
                        // 현재가치 색상
                        if (currentValue > purchaseValue) {{
                            cells[3].className = 'py-3 px-4 text-right font-medium text-green-600 dark:text-green-400';
                        }} else if (currentValue < purchaseValue) {{
                            cells[3].className = 'py-3 px-4 text-right font-medium text-red-600 dark:text-red-400';
                        }} else {{
                            cells[3].className = 'py-3 px-4 text-right font-medium text-gray-600 dark:text-gray-400';
                        }}
                        
                        // 수익/손실 색상
                        if (pnl > 0) {{
                            cells[4].className = 'py-3 px-4 text-right font-medium text-green-600 dark:text-green-400';
                        }} else if (pnl < 0) {{
                            cells[4].className = 'py-3 px-4 text-right font-medium text-red-600 dark:text-red-400';
                        }} else {{
                            cells[4].className = 'py-3 px-4 text-right font-medium text-gray-600 dark:text-gray-400';
                        }}
                    }} catch (err) {{
                        console.error(`Price update failed for ${{coinText}}:`, err);
                        // 개별 코인 업데이트 실패해도 계속 진행
                    }}
                }}
            }} catch (err) {{
                console.error('Account values update error:', err);
            }} finally {{
                isUpdating = false;  // 업데이트 완료 후 락 해제
            }}
        }}
        
        // SSE 스트림 연결 (페이지 로드 후 즉시)
        connectEventStream();
        
        // 5초마다 현재가 업데이트 (더 자주 업데이트)
        setInterval(updateAccountValues, 5000);
        // 초기 로드
        updateAccountValues();
        
        // Settings & Status 드롭다운 토글
        
        // AI 콘솔 Clear 버튼
        let consoleCleared = false;
        // 통계 초기화 버튼 이벤트 핸들러
        const clearTodayBtn = document.getElementById('clear-statistics-today-btn');
        if (clearTodayBtn) {{
            clearTodayBtn.addEventListener('click', async () => {{
                if (!confirm('오늘 기준 성과를 초기화하시겠습니까?')) {{
                    return;
                }}
                try {{
                    const response = await fetch('/statistics?today_only=true', {{
                        method: 'DELETE'
                    }});
                    const result = await response.json();
                    if (result.success) {{
                        alert(result.message || '오늘 기준 성과가 초기화되었습니다.');
                        // 통계 다시 로드
                        await loadStatistics();
                    }} else {{
                        alert('초기화 실패: ' + (result.error || '알 수 없는 오류'));
                    }}
                }} catch (error) {{
                    console.error('Failed to clear today statistics:', error);
                    alert('초기화 중 오류가 발생했습니다.');
                }}
            }});
        }}
        
        const clearCumulativeBtn = document.getElementById('clear-statistics-cumulative-btn');
        if (clearCumulativeBtn) {{
            clearCumulativeBtn.addEventListener('click', async () => {{
                if (!confirm('누적 성과를 초기화하시겠습니까? 이 작업은 되돌릴 수 없습니다.')) {{
                    return;
                }}
                try {{
                    const response = await fetch('/statistics?today_only=false', {{
                        method: 'DELETE'
                    }});
                    const result = await response.json();
                    if (result.success) {{
                        alert(result.message || '누적 성과가 초기화되었습니다.');
                        // 통계 다시 로드
                        await loadStatistics();
                    }} else {{
                        alert('초기화 실패: ' + (result.error || '알 수 없는 오류'));
                    }}
                }} catch (error) {{
                    console.error('Failed to clear cumulative statistics:', error);
                    alert('초기화 중 오류가 발생했습니다.');
                }}
            }});
        }}
        
        document.getElementById('console-clear-btn').addEventListener('click', () => {{
            const consoleEl = document.getElementById('ai-console-content');
            const waitingEl = document.getElementById('ai-console-waiting');
            consoleEl.innerHTML = '';
            if (waitingEl) {{
                waitingEl.remove();
            }}
            consoleCleared = true;
            // 초기화 메시지 추가
            const initMsg = document.createElement('div');
            initMsg.className = 'text-gray-500 py-0.5';
            initMsg.textContent = '🔄 콘솔 초기화됨...';
            consoleEl.appendChild(initMsg);
        }});
        
        // AI 분석 메시지 추가 함수 (최대 50줄 유지, 50줄 초과 시 자동 클리어)
        window.addAIConsoleMessage = function(message, type = 'info') {{
            const console = document.getElementById('ai-console-content');
            if (!console) return;
            
            // 첫 메시지면 대기 메시지 제거
            const waitingMsg = document.getElementById('ai-console-waiting');
            if (waitingMsg && !consoleCleared) {{
                waitingMsg.remove();
                consoleCleared = false;
            }}
            
            // 타입에 따른 색상 설정
            let color = 'text-gray-400';
            if (type === 'error' || type === 'red') {{
                color = 'text-red-400';
            }} else if (type === 'success' || type === 'green') {{
                color = 'text-green-400';
            }} else if (type === 'yellow') {{
                color = 'text-yellow-400';
            }}
            
            const line = document.createElement('div');
            line.className = `{{color}} py-0.5`;
            line.textContent = message;
            console.appendChild(line);
            
            // 최대 50줄만 유지 (50줄 초과 시 자동 클리어)
            const lines = console.querySelectorAll('div');
            if (lines.length > 50) {{
                // 자동 클리어: 오래된 메시지 30줄 제거 (최신 20줄 유지)
                const removeCount = lines.length - 20;
                for (let i = 0; i < removeCount; i++) {{
                    if (lines[i] && lines[i].id !== 'ai-console-waiting') {{
                        lines[i].remove();
                    }}
                }}
                // 자동 클리어 알림 추가
                const clearMsg = document.createElement('div');
                clearMsg.className = 'text-yellow-400 py-0.5 italic';
                clearMsg.textContent = '... (50줄 초과로 오래된 메시지 자동 삭제됨)';
                console.insertBefore(clearMsg, console.firstChild);
                // 알림 메시지는 3초 후 제거
                setTimeout(() => {{
                    if (clearMsg.parentNode) {{
                        clearMsg.remove();
                    }}
                }}, 3000);
            }}
            
            // 자동 스크롤 (항상 최신 메시지로)
            console.scrollTop = console.scrollHeight;
        }};
        
        // 매매 예정 콘솔 메시지 추가 함수
        window.addTradingPendingMessage = function(message, type = 'info') {{
            const console = document.getElementById('trading-pending-content');
            if (!console) return;
            
            // 타입에 따른 색상 설정
            let color = 'text-blue-300';
            if (type === 'error' || type === 'red') {{
                color = 'text-red-400';
            }} else if (type === 'success' || type === 'green') {{
                color = 'text-green-400';
            }} else if (type === 'yellow') {{
                color = 'text-yellow-400';
            }} else if (type === 'cyan') {{
                color = 'text-cyan-400';
            }}
            
            const line = document.createElement('div');
            line.className = `{{color}} py-0.5`;
            line.textContent = message;
            console.appendChild(line);
            
            // 자동 스크롤 (항상 최신 메시지로)
            console.scrollTop = console.scrollHeight;
        }};
        
        // Ollama 연결 상태는 SSE 스트림에서 확인하므로 클라이언트에서 직접 호출하지 않음
        // (CORB 에러 방지를 위해 서버 사이드에서만 처리)
        
        // 차트 토글 및 렌더링
        async function toggleChart(currency, row) {{
            const chartRow = document.getElementById(`chart-row-${{currency}}`);
            
            if (chartRow.classList.contains('hidden')) {{
                // 차트 표시
                chartRow.classList.remove('hidden');
                
                // 이미 차트가 있으면 스킵
                const container = document.getElementById(`chart-container-${{currency}}`);
                if (container.children.length > 1) return;
                
                // 차트 데이터 로드
                try {{
                    const response = await fetch(`/chart/${{currency}}`);
                    if (!response.ok) {{
                        if (response.status === 404) {{
                            container.innerHTML = '<div class="flex items-center justify-center h-full text-yellow-500">코인 데이터를 찾을 수 없습니다</div>';
                        }} else {{
                            container.innerHTML = '<div class="flex items-center justify-center h-full text-red-500">차트 로드 실패 (HTTP ' + response.status + ')</div>';
                        }}
                        return;
                    }}
                    const result = await response.json();
                    
                    if (result.error) {{
                        container.innerHTML = '<div class="flex items-center justify-center h-full text-yellow-500">' + (result.error || '데이터 없음') + '</div>';
                    }} else if (result.data && result.data.length > 0) {{
                        renderChart(currency, result.data);
                    }} else {{
                        container.innerHTML = '<div class="flex items-center justify-center h-full text-gray-500">데이터 없음</div>';
                    }}
                }} catch (err) {{
                    console.error('Chart load error for ' + currency + ':', err);
                    container.innerHTML = '<div class="flex items-center justify-center h-full text-red-500">차트 로드 실패</div>';
                }}
            }} else {{
                // 차트 숨기기
                chartRow.classList.add('hidden');
            }}
        }}
        
        // Chart.js로 차트 렌더링
        function renderChart(currency, candles) {{
            const container = document.getElementById(`chart-container-${{currency}}`);
            
            // 기존 캔버스 제거
            const existingCanvas = container.querySelector('canvas');
            if (existingCanvas) existingCanvas.remove();
            
            // 새 캔버스 생성
            const canvas = document.createElement('canvas');
            container.innerHTML = '';
            container.appendChild(canvas);
            
            // 데이터 처리
            const times = candles.map(c => {{
                const d = new Date(c.time);
                return d.toLocaleTimeString('ko-KR', {{ hour: '2-digit', minute: '2-digit' }});
            }});
            
            const closes = candles.map(c => c.close);
            const opens = candles.map(c => c.open);
            
            // 차트 색상 (상승/하강)
            const colors = candles.map(c => c.close >= c.open ? 'rgba(34, 197, 94, 1)' : 'rgba(239, 68, 68, 1)');
            const bgColors = candles.map(c => c.close >= c.open ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)');
            
            // Chart.js 차트 생성
            if (currentChartInstance) {{
                currentChartInstance.destroy();
            }}
            
            currentChartInstance = new Chart(canvas, {{
                type: 'line',
                data: {{
                    labels: times,
                    datasets: [
                        {{
                            label: '종가',
                            data: closes,
                            borderColor: 'rgb(59, 130, 246)',
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.1,
                            pointRadius: 2,
                            pointBackgroundColor: 'rgb(59, 130, 246)',
                            pointHoverRadius: 4,
                        }},
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{
                            display: true,
                            labels: {{
                                color: document.body.classList.contains('dark') ? '#e5e7eb' : '#374151',
                                font: {{ size: 12 }}
                            }}
                        }},
                        title: {{
                            display: true,
                            text: `${{currency}} - 5분봉 (최근 100개)`,
                            color: document.body.classList.contains('dark') ? '#f3f4f6' : '#111827',
                            font: {{ size: 14, weight: 'bold' }}
                        }}
                    }},
                    scales: {{
                        y: {{
                            beginAtZero: false,
                            grid: {{
                                color: document.body.classList.contains('dark') ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)'
                            }},
                            ticks: {{
                                color: document.body.classList.contains('dark') ? '#d1d5db' : '#6b7280',
                                callback: function(value) {{
                                    return value.toLocaleString();
                                }}
                            }}
                        }},
                        x: {{
                            grid: {{
                                display: false
                            }},
                            ticks: {{
                                color: document.body.classList.contains('dark') ? '#d1d5db' : '#6b7280',
                                maxRotation: 45,
                                minRotation: 0
                            }}
                        }}
                    }}
                }}
            }});
        }}
        
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

        // 거래 내역 로드 (기존 함수 - 호환성 유지)
        async function loadTradeHistory() {{
            try {{
                const response = await fetch('/trades?limit=100');
                const data = await response.json();
                if (data.trades) {{
                    updateTradeHistory(data.trades);
                }}
            }} catch (error) {{
                console.error('Failed to load trade history:', error);
            }}
        }}
        
        // 거래 내역 업데이트 함수 (SSE 스트림에서 호출)
        function updateTradeHistory(trades) {{
            if (!trades || !Array.isArray(trades)) return;
            
            try {{
                const tbody = document.getElementById('trade-history-body');
                if (!tbody) return;
                
                if (trades.length > 0) {{
                    tbody.innerHTML = trades.map(trade => {{
                        const date = new Date(trade.timestamp);
                        const timeStr = date.toLocaleTimeString('ko-KR', {{ hour: '2-digit', minute: '2-digit' }});
                        const strategyName = STRATEGY_INFO[trade.strategy]?.name || trade.strategy;
                        const sideColor = trade.side === 'buy' ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400';
                        const sideBg = trade.side === 'buy' ? 'bg-green-50 dark:bg-green-900/20' : 'bg-red-50 dark:bg-red-900/20';
                        
                        // market에서 currency 추출 (KRW-BTC -> BTC)
                        const currency = trade.market ? trade.market.replace('KRW-', '') : '-';
                        
                        const price = trade.price || 0;
                        const volume = trade.volume || 0;
                        // 매도일 때는 exit_amount 사용, 매수일 때는 amount 또는 price * volume 사용
                        let totalAmount = 0;
                        if (trade.side === 'sell') {{
                            // 매도 시: exit_amount 우선, 없으면 amount, 그 다음 price * volume
                            if (trade.exit_amount && trade.exit_amount > 0) {{
                                totalAmount = trade.exit_amount;
                            }} else if (trade.amount && trade.amount > 0) {{
                                totalAmount = trade.amount;
                            }} else {{
                                totalAmount = price * volume;
                            }}
                        }} else {{
                            // 매수 시: amount 우선, 없으면 price * volume
                            if (trade.amount && trade.amount > 0) {{
                                totalAmount = trade.amount;
                            }} else {{
                                totalAmount = price * volume;
                            }}
                        }}
                        
                        // pnl은 positions 테이블에서 가져오거나 계산
                        const pnl = trade.pnl || 0;
                        const pnlPct = trade.pnl_pct || 0;
                        const pnlColor = pnl >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400';
                        
                        return `
                            <tr class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 transition ${{sideBg}}">
                                <td class="py-2 px-2 text-xs text-gray-600 dark:text-gray-400">${{timeStr}}</td>
                                <td class="py-2 px-2 text-xs font-semibold text-gray-900 dark:text-white">${{currency}}</td>
                                <td class="py-2 px-2 text-xs text-gray-900 dark:text-white">${{strategyName}}</td>
                                <td class="py-2 px-2 text-xs text-center font-semibold ${{sideColor}}">${{trade.side === 'buy' ? '🟢 매수' : '🔴 매도'}}</td>
                                <td class="py-2 px-2 text-xs text-right text-gray-900 dark:text-white">${{price.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }})}}</td>
                                <td class="py-2 px-2 text-xs text-right text-gray-600 dark:text-gray-400">${{volume.toFixed(4)}}</td>
                                <td class="py-2 px-2 text-xs text-right font-semibold text-gray-900 dark:text-white">${{totalAmount.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }})}}</td>
                                <td class="py-2 px-2 text-xs text-right font-semibold ${{pnlColor}}">${{pnl.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }})}}</td>
                                <td class="py-2 px-2 text-xs text-right font-semibold ${{pnlColor}}">${{pnlPct.toFixed(2)}}%</td>
                            </tr>
                        `;
                    }}).join('');
                }} else {{
                    tbody.innerHTML = '<tr><td colspan="9" class="py-4 text-center text-gray-500 dark:text-gray-400 text-sm">거래 내역이 없습니다.</td></tr>';
                }}
            }} catch (error) {{
                console.error('Failed to update trade history:', error);
            }}
        }}

        // 통계 업데이트 헬퍼 함수 (단일 통계 객체 업데이트)
        function updateSingleStatistics(prefix, stats) {{
            if (!stats) return;
            
            try {{
                // 총 거래
                const totalTradesEl = document.getElementById('stat-' + prefix + '-total-trades');
                if (totalTradesEl) {{
                    totalTradesEl.textContent = stats.total_trades || 0;
                }}
                
                // 승률
                const winRateEl = document.getElementById('stat-' + prefix + '-win-rate');
                if (winRateEl) {{
                    const winRate = stats.win_rate || 0;
                    winRateEl.textContent = winRate.toFixed(1) + '%';
                }}
                
                // 총 수익/손실 (마이너스 손실 포함)
                const totalPnlEl = document.getElementById('stat-' + prefix + '-total-pnl');
                if (totalPnlEl) {{
                    const totalPnl = stats.total_pnl || 0;
                    // 소숫점이 있으면 . 표시, 없으면 정수로 표시
                    let pnlText;
                    if (totalPnl % 1 === 0) {{
                        // 정수인 경우
                        pnlText = totalPnl.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                    }} else {{
                        // 소숫점이 있는 경우
                        pnlText = totalPnl.toLocaleString('ko-KR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
                    }}
                    totalPnlEl.textContent = pnlText + ' KRW';
                    // 마이너스 손실인 경우 빨간색, 플러스 수익인 경우 초록색
                    if (totalPnl < 0) {{
                        totalPnlEl.className = 'text-sm font-bold text-red-600 dark:text-red-400';
                    }} else if (totalPnl > 0) {{
                        totalPnlEl.className = 'text-sm font-bold text-green-600 dark:text-green-400';
                    }} else {{
                        totalPnlEl.className = 'text-sm font-bold text-gray-900 dark:text-white';
                    }}
                }}
                
                // 평균 수익률
                const avgPnlEl = document.getElementById('stat-' + prefix + '-avg-profit-pct');
                if (avgPnlEl) {{
                    const avgPnl = stats.avg_pnl_pct || 0;
                    avgPnlEl.textContent = avgPnl.toFixed(2) + '%';
                    // 마이너스인 경우 빨간색
                    if (avgPnl < 0) {{
                        avgPnlEl.className = 'text-lg font-bold text-red-600 dark:text-red-400';
                    }} else if (avgPnl > 0) {{
                        avgPnlEl.className = 'text-lg font-bold text-green-600 dark:text-green-400';
                    }} else {{
                        avgPnlEl.className = 'text-lg font-bold text-gray-900 dark:text-white';
                    }}
                }}
                
            }} catch (error) {{
                console.error('Failed to update statistics (' + prefix + '):', error);
            }}
        }}
        
        // 통계 로드 (오늘/누적 각각)
        async function loadStatistics() {{
            try {{
                // 오늘 통계
                const todayResponse = await fetch('/statistics?today_only=true');
                const todayStats = await todayResponse.json();
                updateSingleStatistics('today', todayStats);
                
                // 누적 통계
                const cumulativeResponse = await fetch('/statistics?today_only=false');
                const cumulativeStats = await cumulativeResponse.json();
                updateSingleStatistics('cumulative', cumulativeStats);
            }} catch (error) {{
                console.error('Failed to load statistics:', error);
            }}
        }}
        
        // 통계 업데이트 함수 (SSE 스트림에서 호출)
        function updateStatistics(stats) {{
            if (!stats) return;
            
            try {{
                // 오늘/누적 각각 업데이트
                if (stats.today) {{
                    updateSingleStatistics('today', stats.today);
                }}
                if (stats.cumulative) {{
                    updateSingleStatistics('cumulative', stats.cumulative);
                }}
                
                // 기존 형식 호환성 (단일 stats 객체인 경우)
                if (stats.total_trades !== undefined && !stats.today && !stats.cumulative) {{
                    updateSingleStatistics('today', stats);
                    updateSingleStatistics('cumulative', stats);
                }}
            }} catch (error) {{
                console.error('Failed to update statistics:', error);
            }}
        }}

        // 거래 모드 배지 업데이트 함수 (전역에서 사용)
        function updateTradingModeBadge(isDryRun) {{
            // 서버 제어 창의 거래 모드 배지 업데이트
            const modeBadge = document.getElementById('trading-mode-badge');
            if (modeBadge) {{
                if (isDryRun) {{
                    modeBadge.textContent = '🟢 모의 모드 (시뮬레이션)';
                    modeBadge.className = 'inline-block px-4 py-1.5 rounded-xl text-sm font-bold shadow-md bg-gradient-to-r from-blue-500 to-blue-600 text-white';
                }} else {{
                    modeBadge.textContent = '🔴 실전 모드 (실제 거래)';
                    modeBadge.className = 'inline-block px-4 py-1.5 rounded-xl text-sm font-bold shadow-md bg-gradient-to-r from-orange-500 to-red-600 text-white';
                }}
            }}

            // 페이지 상단의 거래 모드 배지 업데이트 (기존 요소가 없으므로 생략)
        }}
        
        // 거래 모드 버튼 처리 (즉시 적용)
        const modeDryBtn = document.getElementById('mode-dry');
        const modeLiveBtn = document.getElementById('mode-live');
        const modeInput = document.getElementById('mode');
        
        async function updateTradingMode(mode) {{
            // UI 업데이트
            if (mode === 'dry') {{
                modeInput.value = 'dry';
                modeDryBtn.classList.remove('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
                modeDryBtn.classList.add('border-blue-500', 'bg-blue-50', 'dark:bg-blue-900/30', 'text-blue-700', 'dark:text-blue-300');
                modeLiveBtn.classList.remove('border-red-500', 'bg-red-50', 'dark:bg-red-900/30', 'text-red-700', 'dark:text-red-300');
                modeLiveBtn.classList.add('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
            }} else {{
                modeInput.value = 'live';
                modeLiveBtn.classList.remove('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
                modeLiveBtn.classList.add('border-red-500', 'bg-red-50', 'dark:bg-red-900/30', 'text-red-700', 'dark:text-red-300');
                modeDryBtn.classList.remove('border-blue-500', 'bg-blue-50', 'dark:bg-blue-900/30', 'text-blue-700', 'dark:text-blue-300');
                modeDryBtn.classList.add('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
            }}
            
            // 서버에 즉시 반영
            try {{
                const formData = new FormData();
                formData.append('mode', mode);
                
                const response = await fetch('/update-settings', {{
                    method: 'POST',
                    body: formData,
                }});
                
                const result = await response.json();
                
                if (result.success) {{
                    console.log('거래 모드 변경 완료:', mode, 'updates:', result.updates);
                    // 거래 모드 배지 업데이트 (명시적으로 값 확인)
                    // mode 값으로도 판단 (dry_run이 없어도 mode로 판단)
                    const isDryRun = result.updates?.dry_run === true || 
                                     (result.updates?.dry_run === undefined && mode === 'dry') ||
                                     (result.updates?.mode === 'dry');
                    console.log('거래 모드 배지 업데이트:', isDryRun ? '모의 모드' : '실전 모드', '(dry_run:', result.updates?.dry_run, ', mode:', mode, ')');
                    updateTradingModeBadge(isDryRun);
                }} else {{
                    console.error('거래 모드 변경 실패:', result.error);
                    alert('거래 모드 변경 실패: ' + (result.error || '알 수 없는 오류'));
                }}
            }} catch (error) {{
                console.error('거래 모드 변경 오류:', error);
                alert('거래 모드 변경 중 오류가 발생했습니다.');
            }}
        }}
        
        if (modeDryBtn && modeLiveBtn && modeInput) {{
            modeDryBtn.addEventListener('click', () => {{
                updateTradingMode('dry');
            }});
            
            modeLiveBtn.addEventListener('click', () => {{
                if (confirm('⚠️ 실제 거래 모드(LIVE)로 전환하시겠습니까?\\n실제 돈이 거래됩니다!')) {{
                    updateTradingMode('live');
                }}
            }});
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
                        // 이전 메시지 제거 (있다면)
                        const existingMessages = settingsForm.querySelectorAll('.mb-4.p-3.bg-green-50, .mb-4.p-3.bg-red-50');
                        existingMessages.forEach(msg => msg.remove());
                        
                        // 상태창 즉시 업데이트
                        if (result.updates) {{
                            // Current Strategy 업데이트
                            if (result.updates.strategy) {{
                                const strategyElements = document.querySelectorAll('.flex.justify-between.items-center');
                                strategyElements.forEach(el => {{
                                    if (el.querySelector('span:first-child')?.textContent === 'Current Strategy') {{
                                        const strategyText = el.querySelector('span:last-child');
                                        if (strategyText) {{
                                            strategyText.textContent = result.updates.strategy;
                                        }}
                                    }}
                                }});
                            }}
                            
                            // Current Market 업데이트 제거: 5개 코인을 모두 모니터링하므로 단일 market 표시 불필요
                            
                            // Order Size 업데이트
                            if (result.updates.order_amount_pct !== undefined) {{
                                const orderSizeElements = document.querySelectorAll('.flex.justify-between.items-center');
                                orderSizeElements.forEach(el => {{
                                    if (el.querySelector('span:first-child')?.textContent.includes('💰 Order Size')) {{
                                        const orderSizeText = el.querySelector('span:last-child');
                                        if (orderSizeText) {{
                                            orderSizeText.textContent = result.updates.order_amount_pct + '%';
                                        }}
                                    }}
                                }});
                            }}
                            
                            // 거래 모드 업데이트 - 명시적 값 확인
                            if (result.updates.dry_run !== undefined || result.updates.mode) {{
                                const isDryRun = result.updates.dry_run === true || (result.updates.dry_run !== false && result.updates.dry_run !== undefined && result.updates.mode === 'dry');
                                updateTradingModeBadge(isDryRun);
                            }}
                        }}
                        
                        // 성공 메시지 표시
                        const messageDiv = document.createElement('div');
                        messageDiv.className = 'mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg';
                        messageDiv.innerHTML = `
                            <p class="text-sm text-green-600 dark:text-green-400">설정이 성공적으로 업데이트되었습니다.</p>
                        `;
                        settingsForm.insertBefore(messageDiv, settingsForm.firstChild);
                        
                        // 3초 후 메시지 제거
                        setTimeout(() => {{
                            messageDiv.remove();
                        }}, 3000);
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
        
        // SSE 스트림은 위에서 이미 연결됨
        
        // 자동 새로고침 제거됨 (SSE 스트림으로 실시간 업데이트)

        // 실시간 업데이트 (5초마다)
        setInterval(() => {{
            fetch('/status')
                .then(response => response.json())
                .then(data => {{
                    // /status 엔드포인트는 TradingState.as_dict()를 반환하므로
                    // data.running, data.dry_run 형태로 직접 접근 가능
                    const statusDot = document.getElementById('server-status-dot');
                    const statusText = document.getElementById('server-status-text');
                    const modeBadge = document.getElementById('trading-mode-badge');
                    const lastRunTime = document.getElementById('last-run-time');
                    const lastSignalBadge = document.getElementById('last-signal-badge');
                    
                    // 서버 상태 업데이트 (명시적 값 확인)
                    if (statusDot && statusText) {{
                        const isRunning = data.running === true;
                        if (isRunning) {{
                            statusDot.classList.add('bg-green-500', 'animate-pulse');
                            statusDot.classList.remove('bg-red-500');
                            statusText.textContent = '🟢 동작 중';
                            statusText.classList.add('text-green-600', 'dark:text-green-400');
                            statusText.classList.remove('text-red-600', 'dark:text-red-400');
                        }} else {{
                            statusDot.classList.remove('bg-green-500', 'animate-pulse');
                            statusDot.classList.add('bg-red-500');
                            statusText.textContent = '🔴 중지됨';
                            statusText.classList.remove('text-green-600', 'dark:text-green-400');
                            statusText.classList.add('text-red-600', 'dark:text-red-400');
                        }}
                    }}
                    
                    // 거래 모드 업데이트 (서버 제어 창 + 페이지 상단) - 명시적 값 확인
                    if (modeBadge) {{
                        const isDryRun = data.dry_run === true;
                        if (isDryRun) {{
                            modeBadge.textContent = '🟢 모의 모드 (시뮬레이션)';
                            modeBadge.className = 'inline-block px-4 py-1.5 rounded-xl text-sm font-bold shadow-md bg-gradient-to-r from-blue-500 to-blue-600 text-white';
                        }} else {{
                            modeBadge.textContent = '🔴 실전 모드 (실제 거래)';
                            modeBadge.className = 'inline-block px-4 py-1.5 rounded-xl text-sm font-bold shadow-md bg-gradient-to-r from-orange-500 to-red-600 text-white';
                        }}
                    }}

                    // 페이지 상단 거래 모드 업데이트 (기존 요소가 없으므로 생략)
                    
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
        
        // 서버 시작 버튼 핸들러
        const startForm = document.querySelector('form[action="/start"]');
        if (startForm) {{
            startForm.addEventListener('submit', async (e) => {{
                e.preventDefault();
                
                const modeInput = document.getElementById('mode');
                const mode = modeInput ? modeInput.value : 'dry';
                
                try {{
                    const submitBtn = startForm.querySelector('button[type="submit"]');
                    if (submitBtn) {{
                        submitBtn.disabled = true;
                        submitBtn.innerHTML = '<svg class="w-5 h-5 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><span>시작 중...</span>';
                    }}
                    
                    const formData = new FormData();
                    formData.append('mode', mode);
                    
                    const response = await fetch('/start', {{
                        method: 'POST',
                        body: formData,
                    }});
                    
                    const data = await response.json();
                    
                    if (data.success) {{
                        console.log('✅ 서버 시작됨:', data);
                        // SSE 스트림이 자동으로 상태 업데이트를 전달할 것입니다
                    }} else {{
                        alert('❌ 서버 시작 실패:\\n' + (data.error || '알 수 없는 에러'));
                    }}
                }} catch (error) {{
                    console.error('서버 시작 에러:', error);
                    alert('❌ 서버 시작 중 오류가 발생했습니다.');
                }} finally {{
                    const submitBtn = startForm.querySelector('button[type="submit"]');
                    if (submitBtn) {{
                        submitBtn.disabled = false;
                        submitBtn.innerHTML = '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg><span>서버 시작</span>';
                    }}
                }}
            }});
        }}
        
        // 서버 중지 버튼 핸들러
        const stopForm = document.querySelector('form[action="/stop"]');
        if (stopForm) {{
            stopForm.addEventListener('submit', async (e) => {{
                e.preventDefault();
                
                try {{
                    const submitBtn = stopForm.querySelector('button[type="submit"]');
                    if (submitBtn) {{
                        submitBtn.disabled = true;
                        submitBtn.innerHTML = '<svg class="w-5 h-5 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><span>중지 중...</span>';
                    }}
                    
                    const response = await fetch('/stop', {{
                        method: 'POST',
                    }});
                    
                    const data = await response.json();
                    
                    if (data.success) {{
                        console.log('✅ 서버 중지됨:', data);
                        // SSE 스트림이 자동으로 상태 업데이트를 전달할 것입니다
                    }} else {{
                        alert('❌ 서버 중지 실패:\\n' + (data.error || '알 수 없는 에러'));
                    }}
                }} catch (error) {{
                    console.error('서버 중지 에러:', error);
                    alert('❌ 서버 중지 중 오류가 발생했습니다.');
                }} finally {{
                    const submitBtn = stopForm.querySelector('button[type="submit"]');
                    if (submitBtn) {{
                        submitBtn.disabled = false;
                        submitBtn.innerHTML = '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 10h6v4H9z"></path></svg><span>서버 중지</span>';
                    }}
                }}
            }});
        }}
        
        // 강제 탈출 버튼 핸들러
        const forceExitBtn = document.getElementById('force-exit-btn');
        if (forceExitBtn) {{
            forceExitBtn.addEventListener('click', async () => {{
                // 확인 메시지
                const confirmed = confirm(
                    '⚠️ 경고!\\n\\n' +
                    '보유한 모든 거래 가능 코인을 시장가로 매도합니다.\\n' +
                    '이 작업은 되돌릴 수 없습니다.\\n\\n' +
                    '계속하시겠습니까?'
                );
                
                if (!confirmed) return;
                
                try {{
                    forceExitBtn.disabled = true;
                    forceExitBtn.textContent = '🔄 실행 중...';
                    
                    const response = await fetch('/force-exit', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }}
                    }});
                    
                    const data = await response.json();
                    
                    if (data.success) {{
                        alert(
                            '✅ 강제 탈출 완료!\\n\\n' +
                            data.result.message + '\\n' +
                            (data.result.errors.length > 0 
                                ? '\\n⚠️ 에러:\\n' + data.result.errors.join('\\n')
                                : '')
                        );
                    }} else {{
                        alert('❌ 강제 탈출 실패:\\n' + (data.error || '알 수 없는 에러'));
                    }}
                }} catch (err) {{
                    alert('❌ 요청 실패: ' + err.message);
                }} finally {{
                    forceExitBtn.disabled = false;
                    forceExitBtn.innerHTML = '<span>🚪</span><span>강제 탈출 (모든 코인 매도)</span>';
                }}
            }});
        }}
        
        // 거래 내역 동기화 버튼 핸들러
        const syncTradesBtn = document.getElementById('sync-trades-btn');
        if (syncTradesBtn) {{
            syncTradesBtn.addEventListener('click', async () => {{
                if (!confirm('업비트에서 직접 거래한 내역을 동기화하시겠습니까?')) {{
                    return;
                }}
                
                try {{
                    syncTradesBtn.disabled = true;
                    const originalText = syncTradesBtn.innerHTML;
                    syncTradesBtn.innerHTML = '<svg class="w-5 h-5 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg><span>동기화 중...</span>';
                    
                    const response = await fetch('/api/sync-trades', {{
                        method: 'POST',
                    }});
                    
                    const data = await response.json();
                    
                    if (data.success) {{
                        alert('✅ ' + data.message);
                        // 거래 내역 다시 로드
                        loadTradeHistory();
                        loadStatistics();
                    }} else {{
                        alert('❌ 동기화 실패:\\n' + (data.error || '알 수 없는 에러'));
                    }}
                }} catch (error) {{
                    console.error('Sync trades error:', error);
                    alert('❌ 동기화 중 오류가 발생했습니다.');
                }} finally {{
                    syncTradesBtn.disabled = false;
                    syncTradesBtn.innerHTML = originalText;
                }}
            }});
        }}
        
    </script>
</body>
</html>"""
    return html
