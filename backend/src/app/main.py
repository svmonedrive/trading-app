"""NIFTYBEES Algo Trader - FastAPI backend (http://localhost:8000).

Run (from backend/):  .venv/Scripts/python.exe -m uvicorn app.main:app --app-dir src --host 127.0.0.1 --port 8000
(Bind to 127.0.0.1 only - this API has no authentication and can place real orders.)
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlencode

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.core.config import config_store, market_status, now_ist, settings
from app.core.db import TradeDB
from app.services.ip_monitor import IPMonitor
from app.services.kite_service import KiteNotReady, KiteService
from app.strategy.presets import PRESETS, presets_catalog, validate_limits, validate_order_settings
from app.strategy.engine import StrategyEngine


class _QuietPolling(logging.Filter):
    """Hide successful access-log lines for endpoints the dashboard polls every few seconds.
    Errors (4xx/5xx) on them are still logged, as is every other request."""
    PATHS = ("/api/dashboard", "/api/ip/status", "/api/auth/status", "/api/algo/status",
             "/api/strategy/status", "/api/orders", "/api/events", "/api/health")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            method, path, status = args[1], str(args[2]).split("?")[0], args[4]
            if method == "GET" and path in self.PATHS and isinstance(status, int) and status < 400:
                return False
        return True


logging.getLogger("uvicorn.access").addFilter(_QuietPolling())

db = TradeDB(settings.db_path)
ip_monitor = IPMonitor(settings, config_store, db)
kite_service = KiteService(settings, config_store, db)
engine = StrategyEngine(settings, config_store, db, kite_service, ip_monitor)
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Safety: always boot with the kill switch ON (paused). Start it from the dashboard.
    config_store.set(algo_active=False)
    db.log("INFO", f"Backend started in {settings.mode} mode. Algo is PAUSED until you start it.")
    if not kite_service.api_configured:
        db.log("WARN", "KITE_API_KEY / KITE_API_SECRET not set in backend/.env - Zerodha login disabled.")
    now = datetime.now()
    scheduler.add_job(ip_monitor.check, "interval", seconds=settings.ip_check_interval_seconds,
                      id="ip_monitor", next_run_time=now, max_instances=1, coalesce=True)
    scheduler.add_job(engine.run_check, "interval", seconds=settings.strategy_interval_seconds,
                      id="strategy", next_run_time=now + timedelta(seconds=5), max_instances=1, coalesce=True)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="NIFTYBEES Algo Trader", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://127.0.0.1:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ helpers
def algo_status() -> dict[str, Any]:
    is_open, why = market_status()
    reasons = engine.block_reasons()
    return {
        "algo_active": engine.is_active(),
        "mode": settings.mode,
        "dry_run": settings.dry_run,
        "can_trade": not reasons,
        "block_reasons": reasons,
        "market_open": is_open,
        "market_message": why,
        "server_time": now_ist().isoformat(timespec="seconds"),
        "strategy_interval_seconds": settings.strategy_interval_seconds,
    }


# ------------------------------------------------------------------ health / dashboard
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "mode": settings.mode, "time": now_ist().isoformat(timespec="seconds")}


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    """Everything the UI needs in one call (polled every 5 s)."""
    return {
        "auth": kite_service.session_status(),
        "ip": ip_monitor.status(),
        "algo": algo_status(),
        "strategy": engine.last_snapshot,
        "strategy_config": strategy_config(),
        "orders": engine.orders_today(),
        "events": db.recent_events(30),
    }


# ------------------------------------------------------------------ auth
@app.get("/api/auth/login-url")
def auth_login_url() -> dict[str, str]:
    try:
        return {"login_url": kite_service.login_url()}
    except KiteNotReady as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/auth/callback")
def auth_callback(request_token: str | None = Query(None), status: str | None = Query(None)):
    """Zerodha redirects here after login. Set this exact URL as the Redirect URL of your Kite app."""
    def back(**params: str) -> RedirectResponse:
        return RedirectResponse(f"{settings.frontend_url}/?{urlencode(params)}")

    if status != "success" or not request_token:
        return back(login="failed", reason=f"Zerodha returned status={status}")
    try:
        kite_service.handle_callback(request_token)
    except Exception as exc:
        db.log("ERROR", f"Zerodha token exchange failed: {exc}")
        return back(login="failed", reason=str(exc))
    return back(login="success")


@app.get("/api/auth/status")
def auth_status() -> dict[str, Any]:
    return kite_service.session_status()


# ------------------------------------------------------------------ IPv6 monitor
@app.get("/api/ip/status")
def ip_status() -> dict[str, Any]:
    return ip_monitor.status()


@app.post("/api/ip/check")
def ip_check_now() -> dict[str, Any]:
    return ip_monitor.check()


@app.post("/api/ip/confirm")
def ip_confirm() -> dict[str, Any]:
    try:
        return ip_monitor.confirm()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ------------------------------------------------------------------ algo / strategy
class ToggleRequest(BaseModel):
    active: bool | None = None  # omit to flip the current state


@app.get("/api/algo/status")
def get_algo_status() -> dict[str, Any]:
    return algo_status()


@app.post("/api/algo/toggle")
def algo_toggle(req: ToggleRequest | None = Body(None)) -> dict[str, Any]:
    target = (not engine.is_active()) if req is None or req.active is None else req.active
    engine.set_active(target)
    return algo_status()


@app.get("/api/strategy/status")
def strategy_status() -> dict[str, Any]:
    return engine.last_snapshot


@app.post("/api/strategy/run")
def strategy_run_now() -> dict[str, Any]:
    """Manual 'Run Check Now'. Evaluates immediately and trades if every safety gate passes."""
    return engine.run_check(trigger="manual")


# ---------- strategy selection: one selected strategy, per-strategy limits, shared order settings ----------
class SelectRequest(BaseModel):
    strategy: str


def strategy_config() -> dict[str, Any]:
    """Selected strategy, the (saved or default) limits of EVERY strategy, and the shared order settings."""
    active, limits, orders = config_store.strategy_selection()
    all_limits = {}
    for key in PRESETS:
        try:
            all_limits[key] = validate_limits(key, limits.get(key), strict=False)
        except ValueError:  # saved values no longer valid (e.g. a field's range changed) -> show defaults
            all_limits[key] = validate_limits(key, {}, strict=False)
    return {"active": active, "limits": all_limits, "order_settings": validate_order_settings(orders)}


def _apply_strategy_change(affects_running: bool, change: Callable[[], None], log_msg: str) -> dict[str, Any]:
    if affects_running and engine.has_open_order():
        raise HTTPException(status_code=409, detail="An order is still OPEN - wait until it completes (or cancel it "
                                                    "on Kite) before changing the running strategy.")
    change()
    db.log("INFO", log_msg)
    if affects_running:  # re-evaluate right away with the new settings
        threading.Thread(target=engine.run_check, args=("strategy change",), daemon=True).start()
    return strategy_config()


def _valid(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _fmt(values: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in values.items())


@app.get("/api/strategy/presets")
def get_presets() -> dict[str, Any]:
    """Strategy list with the form fields (limits) the UI renders for each one."""
    return presets_catalog()


@app.get("/api/strategy/config")
def get_strategy_config() -> dict[str, Any]:
    return strategy_config()


@app.put("/api/strategy/active")
def select_strategy(req: SelectRequest) -> dict[str, Any]:
    preset = PRESETS.get(req.strategy)
    if not preset:
        raise HTTPException(status_code=400, detail=f"Unknown strategy '{req.strategy}'")
    active, limits, _ = config_store.strategy_selection()
    if req.strategy == active:
        return strategy_config()
    params = _valid(lambda: validate_limits(req.strategy, limits.get(req.strategy)))
    return _apply_strategy_change(True, lambda: config_store.set(active_strategy=req.strategy),
                                  f"Strategy selected: {preset.name} ({_fmt(params)})")


@app.put("/api/strategy/limits/{key}")
def save_limits(key: str, params: dict[str, Any] = Body(...)) -> dict[str, Any]:
    preset = PRESETS.get(key)
    if not preset:
        raise HTTPException(status_code=404, detail=f"Unknown strategy '{key}'")
    clean = _valid(lambda: validate_limits(key, params))
    running = config_store.strategy_selection()[0] == key
    return _apply_strategy_change(running, lambda: config_store.set_strategy_limits(key, clean),
                                  f"{preset.name} limits saved{' (running strategy)' if running else ''}: {_fmt(clean)}")


@app.put("/api/strategy/order-settings")
def save_order_settings(values: dict[str, Any] = Body(...)) -> dict[str, Any]:
    clean = _valid(lambda: validate_order_settings(values))
    return _apply_strategy_change(True, lambda: config_store.set(order_settings=clean),
                                  f"Order settings saved: {_fmt(clean)}")

# ------------------------------------------------------------------ orders / events
@app.get("/api/orders")
def get_orders() -> dict[str, Any]:
    return engine.orders_today()


@app.get("/api/events")
def get_events(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return db.recent_events(limit)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
