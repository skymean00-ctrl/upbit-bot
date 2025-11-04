"""FastAPI application exposing a simple dashboard for the trading bot."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from upbit_bot.config import Settings, load_settings
from upbit_bot.core import UpbitClient
from upbit_bot.services import ExecutionEngine, PositionSizer, RiskConfig, RiskManager
from upbit_bot.strategies import get_strategy
from upbit_bot.utils import ConsoleNotifier, SlackNotifier, TelegramNotifier

from .controller import TradingController, TradingState

LOGGER = logging.getLogger(__name__)


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
    kwargs: dict[str, Any] = {}
    if components and settings.strategy == "composite":
        kwargs["components"] = components
    else:
        if short_window is not None:
            kwargs["short_window"] = short_window
        if long_window is not None:
            kwargs["long_window"] = long_window
    return get_strategy(settings.strategy, **kwargs)


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

    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market=settings.market,
        dry_run=True,
        risk_manager=risk_manager,
        position_sizer=position_sizer,
        notifiers=_build_notifiers(settings),
        min_order_amount=5000.0,
    )
    controller = TradingController(engine=engine, client=client)

    app = FastAPI(title="Upbit Trading Bot Dashboard")
    app.state.controller = controller
    app.state.settings = settings

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:  # noqa: D401
        state = controller.get_state()
        account = controller.get_account_overview()
        html = _render_dashboard(state, account)
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

    return app


def _render_dashboard(state: TradingState, account: dict[str, Any]) -> str:
    running_badge = "🟢 RUNNING" if state.running else "🔴 STOPPED"
    dry_run_badge = "DRY-RUN" if state.dry_run else "LIVE"
    last_order_html = (
        "<pre>"
        + json.dumps(state.last_order, ensure_ascii=False, indent=2)
        + "</pre>"
        if state.last_order
        else "<em>No orders yet</em>"
    )
    accounts_rows = ""
    for entry in account.get("accounts", []):
        currency = entry.get("currency", "?")
        balance = entry.get("balance", "?")
        locked = entry.get("locked", "0")
        avg_price = entry.get("avg_buy_price", "-")
        accounts_rows += (
            f"<tr><td>{currency}</td><td>{balance}</td><td>{locked}</td><td>{avg_price}</td></tr>"
        )
    if not accounts_rows:
        accounts_rows = "<tr><td colspan='4'>No account data</td></tr>"

    krw_balance = account.get("krw_balance", 0.0)

    html = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8" />
    <title>Upbit Bot Dashboard</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ margin-bottom: 0; }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            margin-right: 8px;
            border-radius: 4px;
            background: #444;
            color: #fff;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
        }}
        section {{ border: 1px solid #ccc; border-radius: 8px; padding: 16px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
        th, td {{ border: 1px solid #ddd; padding: 6px; text-align: left; }}
        form {{ margin-top: 8px; }}
        button {{ padding: 6px 12px; }}
    </style>
    <meta http-equiv="refresh" content="15" />
</head>
<body>
    <h1>Upbit Trading Bot</h1>
    <div>
        <span class="badge">{running_badge}</span>
        <span class="badge">{dry_run_badge}</span>
    </div>
    <div class="grid">
        <section>
            <h2>Status</h2>
            <p><strong>Market:</strong> {state.market}</p>
            <p><strong>Strategy:</strong> {state.strategy}</p>
            <p><strong>Minimum Order:</strong> {state.min_order_amount:.0f} KRW</p>
            <p><strong>Last Signal:</strong> {state.last_signal or "N/A"}</p>
            <p><strong>Last Run:</strong> {state.last_run_at or "N/A"}</p>
            <p><strong>Last Error:</strong> {state.last_error or "-"}</p>
        </section>
        <section>
            <h2>Controls</h2>
            <form method="post" action="/start">
                <label for="mode">Mode:</label>
                <select id="mode" name="mode">
                    <option value="dry" {"selected" if state.dry_run else ""}>Dry-run</option>
                    <option value="live" {"selected" if not state.dry_run else ""}>Live</option>
                </select>
                <button type="submit">Start</button>
            </form>
            <form method="post" action="/stop">
                <button type="submit">Stop</button>
            </form>
        </section>
        <section>
            <h2>Latest Order</h2>
            {last_order_html}
        </section>
        <section>
            <h2>Account Snapshot</h2>
            <p><strong>KRW Balance:</strong> {krw_balance:,.0f} KRW</p>
            {"<p class='error'>" + account['error'] + "</p>" if account.get("error") else ""}
            <table>
                <thead>
                    <tr><th>Currency</th><th>Balance</th><th>Locked</th><th>Avg Buy</th></tr>
                </thead>
                <tbody>
                    {accounts_rows}
                </tbody>
            </table>
        </section>
    </div>
</body>
</html>
"""
    return html
