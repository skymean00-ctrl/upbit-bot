"""FastAPI application exposing a simple dashboard for the trading bot."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from threading import Thread
from typing import Any, AsyncGenerator, Optional

import requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from upbit_bot.config import Settings, load_settings
from upbit_bot.core import UpbitClient
from upbit_bot.data.performance_tracker import PerformanceTracker
from upbit_bot.data.trade_history import TradeHistoryStore
from upbit_bot.services import ExecutionEngine, PositionSizer, RiskConfig, RiskManager
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

    @app.get("/api/stream")
    async def stream_updates() -> StreamingResponse:
        """Server-Sent Events stream for real-time updates."""
        async def generate() -> AsyncGenerator[str, None]:
            while True:
                try:
                    # Get current account overview
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
                    
                    # AI 전략이면 항상 AI 분석 결과 가져오기 (SSE 스트림에서 직접 실행)
                    ai_analysis = None
                    if state.get("strategy") == "ai_market_analyzer":
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
                            # Ollama 연결 확인 (더 상세한 검사)
                            ollama_status = "disconnected"
                            ollama_error = None
                            try:
                                test_response = requests.get("http://100.98.189.30:11434/api/tags", timeout=3)
                                if test_response.status_code == 200:
                                    models = test_response.json().get("models", [])
                                    model_names = [m.get("name", "") for m in models]
                                    if "qwen2.5-coder:7b" in model_names:
                                        ollama_status = "connected"
                                        LOGGER.info(f"Ollama 연결 확인: {len(models)}개 모델 사용 가능")
                                    else:
                                        ollama_status = "model_missing"
                                        ollama_error = f"필요한 모델 'qwen2.5-coder:7b' 없음 (사용 가능: {', '.join(model_names[:3])}...)"
                                        LOGGER.warning(ollama_error)
                                else:
                                    ollama_status = "error"
                                    ollama_error = f"HTTP {test_response.status_code}"
                                    LOGGER.warning(f"Ollama 응답 오류: {ollama_error}")
                            except requests.exceptions.Timeout:
                                ollama_status = "timeout"
                                ollama_error = "연결 시간 초과 (3초)"
                                LOGGER.warning(f"Ollama 연결 시간 초과")
                            except requests.exceptions.ConnectionError as e:
                                ollama_status = "disconnected"
                                ollama_error = f"연결 오류: {str(e)[:100]}"
                                LOGGER.error(f"Ollama 연결 실패: {e}")
                            except Exception as e:
                                ollama_status = "error"
                                ollama_error = f"예기치 않은 오류: {str(e)[:100]}"
                                LOGGER.error(f"Ollama 확인 중 오류: {e}", exc_info=True)
                            
                            # 분석 중이면 "analyzing" 상태, 아니면 "waiting" 또는 에러 상태
                            if analysis_in_progress:
                                status = "analyzing"
                            elif ollama_status == "connected":
                                # Ollama가 연결되어 있으면 분석을 시작해야 하므로 "analyzing"으로 표시
                                # (실제로는 분석이 곧 시작되거나 진행 중일 수 있음)
                                status = "analyzing"
                            else:
                                status = "ollama_disconnected"
                            
                            ai_analysis = {
                                "selected_market": state.get("market", "N/A"),
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
                    
                    data = {
                        "timestamp": int(__import__("time").time() * 1000),
                        "balance": account,
                        "state": state,
                        "ai_analysis": ai_analysis,  # AI 전략이면 항상 포함
                        "statistics": statistics_data,  # 통계 데이터 포함
                        "recent_trades": recent_trades,  # 최근 거래 내역 포함
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
            ticker = controller.engine.client.get_ticker(market)
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
                <div class="flex items-center space-x-3">
                    <div class="flex items-center px-5 py-2.5 rounded-xl bg-white dark:bg-gray-800 shadow-lg border border-gray-200 dark:border-gray-700">
                        <span class="status-indicator {running_status}"></span>
                        <span class="text-sm font-bold text-gray-700 dark:text-gray-300">
                            {state.running and "RUNNING" or "STOPPED"}
                        </span>
                    </div>
                    <div class="px-5 py-2.5 rounded-xl shadow-lg font-bold text-sm {'bg-gradient-to-r from-blue-500 to-blue-600 text-white dark:from-blue-600 dark:to-blue-700' if state.dry_run else 'bg-gradient-to-r from-orange-500 to-red-600 text-white dark:from-orange-600 dark:to-red-700'}">
                        {state.dry_run and "DRY-RUN" or "LIVE"}
                    </div>
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
                    <p class="text-sm text-red-700 dark:text-red-300">AI 시장 분석 서비스를 사용할 수 없습니다. 노트북의 Ollama 서버 상태를 확인해주세요. (IP: 100.98.189.30:11434)</p>
    </div>
            </div>
        </div>

        <!-- AI Analysis Console Window (Always Visible - Scrollable) -->
        <div class="mb-6 bg-gradient-to-br from-gray-900 via-gray-900 to-gray-950 dark:from-gray-950 dark:via-gray-900 dark:to-black rounded-2xl shadow-2xl border border-gray-700 dark:border-gray-800 overflow-hidden">
            <div class="bg-gradient-to-r from-gray-800 to-gray-900 dark:from-gray-900 dark:to-gray-800 px-5 py-4 border-b border-gray-700 dark:border-gray-800 flex items-center justify-between">
                <div class="flex items-center gap-4">
                    <h3 class="text-base font-bold text-green-400 flex items-center gap-3">
                        <span class="text-2xl animate-pulse">🤖</span>
                        <span>AI 분석 콘솔</span>
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
            <div id="ai-console-content" class="overflow-y-auto p-5 font-mono text-sm text-green-400 bg-gray-900 dark:bg-black" style="height: 20em; line-height: 1.5em; max-height: 20em;">
                <div id="ai-console-waiting" class="text-gray-500 flex items-center gap-2">
                    <span class="animate-spin">🔄</span>
                    <span>AI 분석 대기 중...</span>
                </div>
            </div>
        </div>

        <!-- Balance Cards -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
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
                    <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
                        <span class="text-xl">📅</span>
                        <span>오늘 기준 성과</span>
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
                    <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-3 flex items-center gap-2">
                        <span class="text-xl">📈</span>
                        <span>누적 성과</span>
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
                </div>
            </div>

            <!-- Trade History -->
            <div class="card bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-7">
                <h2 class="text-2xl font-extrabold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                    <span class="text-3xl">📋</span>
                    <span>거래 내역</span>
                </h2>
                <div id="trade-history" class="overflow-x-auto overflow-y-auto" style="height: 18em;">
                    <table class="w-full text-xs">
                        <thead>
                            <tr class="border-b-2 border-gray-300 dark:border-gray-600 bg-gradient-to-r from-gray-50 to-gray-100 dark:from-gray-700 dark:to-gray-800">
                                <th class="text-left py-3 px-3 font-bold text-gray-800 dark:text-gray-200">시간</th>
                                <th class="text-left py-3 px-3 font-bold text-gray-800 dark:text-gray-200">코인</th>
                                <th class="text-left py-3 px-3 font-bold text-gray-800 dark:text-gray-200">전략</th>
                                <th class="text-center py-3 px-3 font-bold text-gray-800 dark:text-gray-200">신호</th>
                                <th class="text-right py-3 px-3 font-bold text-gray-800 dark:text-gray-200">가격</th>
                                <th class="text-right py-3 px-3 font-bold text-gray-800 dark:text-gray-200">수량</th>
                                <th class="text-right py-3 px-3 font-bold text-gray-800 dark:text-gray-200">총액</th>
                                <th class="text-right py-3 px-3 font-bold text-gray-800 dark:text-gray-200">수익/손실</th>
                                <th class="text-right py-3 px-3 font-bold text-gray-800 dark:text-gray-200">수익률 (%)</th>
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
                                <span class="text-xl font-extrabold text-gray-900 dark:text-white" id="server-status-text">Running</span>
                            </div>
                        </div>
                        <div class="text-right">
                            <p class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2 uppercase tracking-wide">거래 모드</p>
                            <span class="inline-block px-4 py-1.5 rounded-xl text-sm font-bold shadow-md {'bg-gradient-to-r from-blue-500 to-blue-600 text-white' if state.dry_run else 'bg-gradient-to-r from-orange-500 to-red-600 text-white'}" id="trading-mode-badge">{state.dry_run and 'Dry-run' or 'LIVE'}</span>
                        </div>
                    </div>
                </div>
                
                <div class="space-y-4">
                    <form method="post" action="/start" class="space-y-3">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">📊 거래 모드 선택</label>
                            <div class="grid grid-cols-2 gap-2">
                                <button type="button" id="mode-dry" class="w-full px-4 py-2 border-2 rounded-lg font-semibold transition-all {'border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300' if state.dry_run else 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:border-blue-400'}">
                                    🟢 Dry-run
                                </button>
                                <button type="button" id="mode-live" class="w-full px-4 py-2 border-2 rounded-lg font-semibold transition-all {'border-red-500 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300' if not state.dry_run else 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:border-red-400'}">
                                    🔴 Live
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
                            <tr class="border-b-2 border-gray-300 dark:border-gray-600 bg-gradient-to-r from-gray-50 to-gray-100 dark:from-gray-700 dark:to-gray-800">
                                <th class="text-left py-4 px-4 font-bold text-gray-800 dark:text-gray-200">코인</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">보유량</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">매수가</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">구매금액 (원)</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">현재가</th>
                                <th class="text-right py-4 px-4 font-bold text-gray-800 dark:text-gray-200">현재가치 (원)</th>
                            </tr>
                </thead>
                <tbody>
                            {''.join([f'''
                            <tr class="table-row border-b border-gray-100 dark:border-gray-700 cursor-pointer transition-all duration-200" onclick="toggleChart('{entry.get('currency', '?')}', this)">
                                <td class="py-3 px-4 font-medium text-gray-900 dark:text-white">{entry.get('currency', '?')} <span class="text-xs text-gray-400 ml-1">📊</span></td>
                                <td class="py-3 px-4 text-right text-gray-900 dark:text-white">{float(entry.get('balance', 0)):,.8f}</td>
                                <td class="py-3 px-4 text-right text-gray-600 dark:text-gray-400">{f"{float(entry.get('avg_buy_price', 0)):,.0f}" if entry.get('avg_buy_price') and float(entry.get('avg_buy_price', 0)) > 0 else '-'}</td>
                                <td class="py-3 px-4 text-right font-medium text-blue-600 dark:text-blue-400">{f"{float(entry.get('purchase_amount', 0)):,.0f}" if entry.get('purchase_amount') else '-'}</td>
                                <td class="py-3 px-4 text-right text-gray-600 dark:text-gray-400">{f"{float(entry.get('current_price', 0)):,.0f}" if entry.get('current_price') and float(entry.get('current_price', 0)) > 0 else '-'}</td>
                                <td class="py-3 px-4 text-right font-medium text-green-600 dark:text-green-400">{f"{float(entry.get('crypto_value', 0)):,.0f}" if entry.get('crypto_value') else '-'}</td>
                            </tr>
                            <tr id="chart-row-{entry.get('currency', '?')}" class="hidden">
                                <td colspan="6" class="py-4 px-4">
                                    <div id="chart-container-{entry.get('currency', '?')}" class="w-full h-64 bg-gray-50 dark:bg-gray-900 rounded-lg p-4">
                                        <div class="flex items-center justify-center h-full text-gray-500">
                                            <span>📈 차트 로딩 중...</span>
                                        </div>
                                    </div>
                                </td>
                            </tr>''' for entry in accounts_data]) if accounts_data else '<tr><td colspan="6" class="py-4 px-4 text-center text-gray-500 dark:text-gray-400">거래 가능한 코인이 없습니다</td></tr>'}
                </tbody>
            </table>
    </div>
            </div>
        </div>

        <!-- Settings & Status (드롭다운 - 맨 아래) -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <!-- Settings Card -->
            <div class="card bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-7">
                <h2 class="text-2xl font-extrabold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                        <span class="text-3xl">⚙️</span>
                        <span>설정</span>
                    </h2>
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
                    <div>
                        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            Market
                        </label>
                        <div class="p-3 bg-gray-50 dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700">
                            <p class="text-sm font-semibold text-gray-900 dark:text-white">
                                {state.market or 'KRW-BTC'}
                            </p>
                        </div>
                        <input type="hidden" name="market" value="{state.market or 'KRW-BTC'}">
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
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
                            💡 보유 원화의 %를 1건당 매수 금액으로 계산<br/>
                            • 매수: 계산값 &lt; 6,000원이면 6,000원으로 매수<br/>
                            • 매도: 신호 발생 시 무조건 매도 (포지션 &lt; 5,000원이면 추가 매수 후 즉시 판매) (기본값: 3%)
                        </p>
                    </div>
                    <button 
                        type="submit" 
                        class="btn-primary w-full text-white font-bold py-3 px-6 rounded-xl transition-all duration-200 shadow-lg hover:shadow-xl"
                    >
                        설정 저장
                    </button>
                </form>
            </div>
            
            <!-- Status Card -->
            <div class="card bg-white dark:bg-gray-800 rounded-2xl shadow-xl p-7">
                <h2 class="text-2xl font-extrabold text-gray-900 dark:text-white mb-6 flex items-center gap-2">
                        <span class="text-3xl">📊</span>
                        <span>상태</span>
                    </h2>
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
        </div>

    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <script>
        const STRATEGY_INFO = {json.dumps({k: v for k, v in strategy_info.items()}, ensure_ascii=False)};
        let currentChartInstance = null;
        let eventSource = null;
        
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
                // Ollama 연결 상태 업데이트
                if (data.ollama_status) {{
                    const statusBadge = document.getElementById('ollama-status-badge');
                    const statusIcon = document.getElementById('ollama-status-icon');
                    const statusText = document.getElementById('ollama-status-text');
                    
                    if (statusBadge && statusIcon && statusText) {{
                        const connected = data.ollama_status.connected;
                        const error = data.ollama_status.error;
                        const model = data.ollama_status.model || 'N/A';
                        const modelAvailable = data.ollama_status.model_available;
                        
                        if (connected && modelAvailable) {{
                            // 연결됨 + 모델 사용 가능
                            statusBadge.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-green-900/30 text-green-400 border border-green-600/50';
                            statusIcon.className = 'w-2 h-2 rounded-full bg-green-400 animate-pulse';
                            statusText.textContent = `✅ Ollama 연결됨 (${model})`;
                        }} else if (connected && !modelAvailable) {{
                            // 연결됨 + 모델 없음
                            statusBadge.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-yellow-900/30 text-yellow-400 border border-yellow-600/50';
                            statusIcon.className = 'w-2 h-2 rounded-full bg-yellow-400 animate-pulse';
                            statusText.textContent = `⚠️ Ollama 연결됨 (모델 ${model} 없음)`;
                        }} else {{
                            // 연결 안됨
                            statusBadge.className = 'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-red-900/30 text-red-400 border border-red-600/50';
                            statusIcon.className = 'w-2 h-2 rounded-full bg-red-400';
                            const errorMsg = error ? `: ${error}` : '';
                            statusText.textContent = `❌ Ollama 연결 실패${errorMsg}`;
                        }}
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
                
                // 상태 업데이트 (마지막 실행, 마지막 신호)
                if (data.state) {{
                    const lastRunEl = document.getElementById('last-run-time');
                    const lastSignalEl = document.getElementById('last-signal-badge');
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
                            }} catch {{
                                lastRunEl.textContent = lastRun;
                            }}
                        }} else {{
                            lastRunEl.textContent = '-';
                        }}
                    }}
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
                if (data.state && data.state.strategy === 'ai_market_analyzer') {{
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
                                waitingEl.innerHTML = '<span class="animate-spin">🔄</span><span>[' + timestamp + '] ' + coinName + ' | AI 분석 실행 중... (잠시만 기다려주세요)</span>';
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
                                        errorMsg += ' (IP: 100.98.189.30:11434) - 노트북에서 "ollama serve" 실행 필요';
                                    }} else if (analysis.ollama_status === 'model_missing') {{
                                        errorMsg += ' - 노트북에서 "ollama pull qwen2.5-coder:7b" 실행 필요';
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
                                }} else if (status === 'no_analysis') {{
                                    const message = '[' + timestamp + '] ' + coinName + ' | ⚠️ AI 분석 결과 없음 (Ollama 연결 확인 필요)';
                                    addAIConsoleMessage(message, 'yellow');
                                }} else if (status === 'insufficient_data') {{
                                    const message = '[' + timestamp + '] ' + coinName + ' | ⚠️ 데이터 부족 (최소 5개 캔들 필요)';
                                    addAIConsoleMessage(message, 'yellow');
                                }} else if (status === 'calculation_failed') {{
                                    const message = '[' + timestamp + '] ' + coinName + ' | ⚠️ 기술적 지표 계산 실패';
                                    addAIConsoleMessage(message, 'yellow');
                                }} else {{
                        // 신호에 따른 이모지와 색상
                        let signalEmoji = '⚪';
                        let signalColor = 'gray';
                        if (signal === 'BUY' || signal.toUpperCase() === 'BUY') {{
                            signalEmoji = '🟢';
                            signalColor = 'green';
                        }} else if (signal === 'SELL' || signal.toUpperCase() === 'SELL') {{
                            signalEmoji = '🔴';
                            signalColor = 'red';
                        }} else {{
                            signalEmoji = '⚪';
                            signalColor = 'gray';
                        }}
                        
                                    // marketData가 있으면 상세 정보 표시, 없으면 간단히 표시
                                    let message;
                                    if (marketData && Object.keys(marketData).length > 0 && marketData.current_price) {{
                                        const price = (marketData.current_price || 0).toLocaleString('ko-KR');
                                        const vol = (marketData.volatility || 0).toFixed(2);
                                        const volRatio = (marketData.volume_ratio || 0).toFixed(2);
                                        message = '[' + timestamp + '] ' + coinName + ' | ' + signalEmoji + ' ' + signal + ' (신뢰도: ' + confidence.toFixed(1) + '%) | 가격: ' + price + '원 | 변동성: ' + vol + '% | 거래량: ' + volRatio + 'x';
                                    }} else {{
                                        // marketData가 없어도 signal과 confidence는 표시
                                        message = '[' + timestamp + '] ' + coinName + ' | ' + signalEmoji + ' ' + signal + ' (신뢰도: ' + confidence.toFixed(1) + '%)';
                                    }}
                                    
                                    addAIConsoleMessage(message, signalColor);
                                    
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
            }} catch (err) {{
                console.error('Stream update error:', err);
            }}
        }}
        
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
                        if (cells.length >= 6) {{
                            // 코인명 (이미 있음)
                            // 보유량
                            cells[1].textContent = balance.toFixed(8);
                            // 매수가
                            cells[2].textContent = avgBuyPrice > 0 ? avgBuyPrice.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }}) : '-';
                            // 구매금액
                            cells[3].textContent = purchaseAmount.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                            // 현재가
                            cells[4].textContent = currentPrice > 0 ? currentPrice.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }}) : '-';
                            // 현재가치
                            cells[5].textContent = currentValue.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                            
                            // 수익률에 따른 색상 변경
                            if (currentValue > purchaseAmount) {{
                                cells[5].className = 'py-3 px-4 text-right font-medium text-green-600 dark:text-green-400';
                            }} else if (currentValue < purchaseAmount) {{
                                cells[5].className = 'py-3 px-4 text-right font-medium text-red-600 dark:text-red-400';
                            }} else {{
                                cells[5].className = 'py-3 px-4 text-right font-medium text-gray-600 dark:text-gray-400';
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
        async function updateAccountValues() {{
            try {{
                const table = document.querySelector('table tbody');
                if (!table) return;
                
                const rows = table.querySelectorAll('tr');
                for (const row of rows) {{
                    // 차트 행 제외
                    if (row.id && row.id.startsWith('chart-row-')) continue;
                    
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 6) continue;
                    
                    // 코인명 추출
                    const coinText = cells[0].textContent.trim().split(' ')[0];
                    if (!coinText || coinText === '보유한') continue;
                    
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
                        
                            const balance = parseFloat(cells[1].textContent);
                            const currentPrice = data.data[data.data.length - 1].close;
                            const currentValue = balance * currentPrice;
                            
                            // 현재가 업데이트
                            cells[4].textContent = currentPrice.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                            
                            // 현재가치 업데이트
                            cells[5].textContent = currentValue.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
                            
                            // 초록색 또는 빨강색으로 표시
                            const purchaseValue = parseFloat(cells[3].textContent);
                            if (currentValue > purchaseValue) {{
                                cells[5].className = 'py-3 px-4 text-right font-medium text-green-600 dark:text-green-400';
                            }} else if (currentValue < purchaseValue) {{
                                cells[5].className = 'py-3 px-4 text-right font-medium text-red-600 dark:text-red-400';
                            }} else {{
                                cells[5].className = 'py-3 px-4 text-right font-medium text-gray-600 dark:text-gray-400';
                        }}
                    }} catch (err) {{
                        console.debug(`Price update failed for ${{coinText}}:`, err);
                    }}
                }}
            }} catch (err) {{
                console.error('Account values update error:', err);
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
                        const totalAmount = price * volume;
                        
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
                    const pnlText = totalPnl.toLocaleString('ko-KR', {{ maximumFractionDigits: 0 }});
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

        // 거래 모드 버튼 처리 (드롭다운 대신 버튼)
        const modeDryBtn = document.getElementById('mode-dry');
        const modeLiveBtn = document.getElementById('mode-live');
        const modeInput = document.getElementById('mode');
        
        if (modeDryBtn && modeLiveBtn && modeInput) {{
            modeDryBtn.addEventListener('click', () => {{
                modeInput.value = 'dry';
                modeDryBtn.classList.remove('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
                modeDryBtn.classList.add('border-blue-500', 'bg-blue-50', 'dark:bg-blue-900/30', 'text-blue-700', 'dark:text-blue-300');
                modeLiveBtn.classList.remove('border-red-500', 'bg-red-50', 'dark:bg-red-900/30', 'text-red-700', 'dark:text-red-300');
                modeLiveBtn.classList.add('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
            }});
            
            modeLiveBtn.addEventListener('click', () => {{
                modeInput.value = 'live';
                modeLiveBtn.classList.remove('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
                modeLiveBtn.classList.add('border-red-500', 'bg-red-50', 'dark:bg-red-900/30', 'text-red-700', 'dark:text-red-300');
                modeDryBtn.classList.remove('border-blue-500', 'bg-blue-50', 'dark:bg-blue-900/30', 'text-blue-700', 'dark:text-blue-300');
                modeDryBtn.classList.add('border-gray-300', 'dark:border-gray-600', 'bg-white', 'dark:bg-gray-700', 'text-gray-700', 'dark:text-gray-300');
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
                        // 성공 메시지 표시
                        const messageDiv = document.createElement('div');
                        messageDiv.className = 'mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg';
                        messageDiv.innerHTML = `
                            <p class="text-sm text-green-600 dark:text-green-400">설정이 성공적으로 업데이트되었습니다.</p>
                        `;
                        settingsForm.insertBefore(messageDiv, settingsForm.firstChild);
                        
                        // 3초 후 메시지 제거 (페이지 새로고침 없이)
                        setTimeout(() => {{
                            messageDiv.remove();
                            // 페이지 새로고침 없이 SSE 스트림으로 업데이트됨
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
        
        // 자동 새로고침 (20초마다) - 숫자 업데이트를 위해 필요
        setInterval(() => {{
            location.reload();
        }}, 20000);  // 20초마다 페이지 새로고침

        // Auto-refresh 기능 완전히 제거됨 (SSE 스트림으로 실시간 업데이트)

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
    </script>
</body>
</html>"""
    return html
