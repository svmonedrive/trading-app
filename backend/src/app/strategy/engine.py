"""NIFTYBEES strategy engine.

* Market data: yfinance (free).  Orders: Kite Connect (LIMIT only).
* What to trade: the strategy selected in the Live Strategy card (a preset from presets.py) with its saved
  limits, plus the shared order settings. It stays selected until the user picks another one.
* The engine - not the presets - enforces: max holding, the no-loss rule (never sell below the bot's average
  buy price + min profit %), and every safety gate in `block_reasons()`.
* The bot only ever sells what IT bought (tracked in algo.db), never your other NIFTYBEES holdings.
"""
from __future__ import annotations

import math
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import yfinance as yf

from app.core.config import ConfigStore, Settings, market_status, now_ist
from app.core.db import TradeDB
from app.services.ip_monitor import IPMonitor
from app.services.kite_service import KiteService, normalize_kite_order
from app.strategy.presets import PRESETS, Signals, build_params, compose_plan

TICK_SIZE = 0.01  # NSE tick size for NIFTYBEES


@dataclass
class Decision:
    signal: str  # BUY | SELL | HOLD
    reason: str
    next_trigger: str
    rules: list[str] = field(default_factory=list)  # one line per armed rule, shown under "Next trigger"
    indicators: list[dict[str, Any]] = field(default_factory=list)
    min_sell_price: float | None = None


def _limit_price(ltp: float, side: str, buffer_pct: float) -> float:
    factor = 1 + buffer_pct / 100 if side == "BUY" else 1 - buffer_pct / 100
    raw = ltp * factor
    ticks = math.ceil(raw / TICK_SIZE) if side == "BUY" else math.floor(raw / TICK_SIZE)
    return round(ticks * TICK_SIZE, 2)


def min_sell_price(avg_price: float | None, min_profit_pct: float) -> float | None:
    """No-loss floor: lowest SELL price allowed, rounded UP to the tick."""
    if not avg_price:
        return None
    return round(math.ceil(avg_price * (1 + min_profit_pct / 100) / TICK_SIZE - 1e-9) * TICK_SIZE, 2)


def decide(sig: Signals, pos: dict[str, Any], p: dict[str, Any], price: float) -> Decision:
    """Turn a preset's raw buy/sell conditions into one action, applying holding limits and the no-loss rule."""
    qty, avg = pos["algo_qty"], pos.get("avg_price")
    max_hold, order_q = int(p["max_holding_qty"]), int(p["order_quantity"])
    floor = min_sell_price(avg, float(p["min_profit_pct"])) if qty > 0 else None
    if not sig.ready:
        return Decision("HOLD", sig.not_ready, "Waiting for more history", [], sig.indicators, floor)

    rules: list[str] = []
    if qty > 0:
        if sig.sell_rule:
            rules.append(f"SELL {qty} when {sig.sell_rule}"
                         + (f" AND sell price ≥ {floor:.2f} (no-loss)" if floor else ""))
        else:
            rules.append(f"Holding {qty} - this plan has no sell rule")
    buy_qty = min(order_q, max_hold - qty)
    if buy_qty > 0:
        if sig.buy_rule:
            rules.append(f"BUY {buy_qty} when {sig.buy_rule}")
    else:
        rules.append(f"No more buying: bot holds {qty} (max {max_hold})")
    next_trigger = " | ".join(rules) or "No rule armed"

    if qty > 0 and sig.sell:
        sell_limit = _limit_price(price, "SELL", float(p["limit_buffer_pct"]))
        if floor and sell_limit < floor:
            return Decision("HOLD", f"SELL signal ({sig.sell_reason}) held back by no-loss rule: sell price "
                                    f"{sell_limit:.2f} < {floor:.2f} (avg buy {avg:.2f} + {p['min_profit_pct']:g}%)",
                            next_trigger, rules, sig.indicators, floor)
        return Decision("SELL", sig.sell_reason, next_trigger, rules, sig.indicators, floor)
    if buy_qty > 0 and sig.buy:
        return Decision("BUY", sig.buy_reason, next_trigger, rules, sig.indicators, floor)
    if qty > 0:
        reason = f"Holding {qty} @ avg {avg:.2f} - waiting for a sell signal" if avg else f"Holding {qty}"
    else:
        reason = "Flat - waiting for a buy signal"
    return Decision("HOLD", reason, next_trigger, rules, sig.indicators, floor)


# ---------------------------------------------------------------- engine
class StrategyEngine:
    def __init__(self, settings: Settings, store: ConfigStore, db: TradeDB,
                 kite: KiteService, ip_monitor: IPMonitor):
        self._settings = settings
        self._store = store
        self._db = db
        self._kite = kite
        self._ip = ip_monitor
        self._run_lock = threading.Lock()
        self._last_failure: dict[str, datetime] = {}
        self._last_block_msg: str | None = None
        self.last_snapshot: dict[str, Any] = {"status": "not_run_yet"}

    # ---------- state ----------
    @property
    def mode(self) -> str:
        return self._settings.mode

    def is_active(self) -> bool:
        return bool(self._store.get("algo_active"))

    def set_active(self, active: bool) -> bool:
        self._store.set(algo_active=active)
        self._db.log("INFO", f"Algo {'ACTIVATED' if active else 'PAUSED (kill switch)'} by user [{self.mode}]")
        return active

    def has_open_order(self) -> bool:
        return bool(self._db.orders(mode=self.mode, only_open=True))

    def block_reasons(self) -> list[str]:
        reasons: list[str] = []
        if not self.is_active():
            reasons.append("Algo is paused (kill switch)")
        if self._store.get("zerodha_ip_rejection"):
            reasons.append("Zerodha rejected an order because of the IP - re-whitelist and confirm")
        elif not self._ip.is_matched():
            reasons.append("Public IP not verified against Zerodha whitelist")
        is_open, why = market_status()
        if not is_open and not (self._settings.dry_run and self._settings.paper_ignore_market_hours):
            reasons.append(why)
        if not self._settings.dry_run and not self._kite.is_session_active():
            reasons.append("Zerodha login required")
        return reasons

    def positions(self, symbol: str) -> dict[str, Any]:
        detail = self._db.algo_position_detail(mode=self.mode, tradingsymbol=symbol)
        demat_qty = None
        if not self._settings.dry_run and self._kite.is_session_active():
            try:
                demat_qty = self._kite.demat_quantity(symbol)
            except Exception:
                demat_qty = None
        return {"algo_qty": detail["qty"], "avg_price": detail["avg_price"], "demat_qty": demat_qty}

    # ---------- market data ----------
    def _fetch_market_data(self, p: dict[str, Any]) -> tuple[pd.Series, float, datetime]:
        ticker = yf.Ticker(p["yf_ticker"])
        hist = ticker.history(period=p["history_period"], interval=p["candle_interval"], auto_adjust=False)
        if hist.empty:
            raise RuntimeError(f"yfinance returned no data for {p['yf_ticker']} ({p['candle_interval']})")
        close = hist["Close"].dropna().copy()
        price, price_time = float(close.iloc[-1]), close.index[-1].to_pydatetime()

        if p["candle_interval"] == "1d":
            # Daily candles: overlay the most recent 1-minute price as today's (live) close.
            try:
                intraday = ticker.history(period="1d", interval="1m", auto_adjust=False)["Close"].dropna()
                if not intraday.empty:
                    ts = intraday.index[-1]
                    price, price_time = float(intraday.iloc[-1]), ts.to_pydatetime()
                    if close.index[-1].date() == ts.date():
                        close.iloc[-1] = price
                    else:
                        close = pd.concat([close, pd.Series([price], index=[ts.normalize()])])
            except Exception as exc:
                self._db.log("WARN", f"Intraday price fetch failed, using last daily close: {exc}")
        return close, price, price_time

    # ---------- main loop ----------
    def run_check(self, trigger: str = "scheduler") -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return {**self.last_snapshot, "note": "A check is already running"}
        try:
            self.sync_live_orders()
            plan = compose_plan(*self._store.strategy_selection())
            preset = PRESETS[plan["strategy"]]
            p = build_params(self._store.system_settings(), plan)

            close, price, price_time = self._fetch_market_data(p)
            pos = self.positions(p["symbol"])
            sig = preset.evaluate(close, price, {"qty": pos["algo_qty"], "avg_price": pos["avg_price"]},
                                  p, now_ist().date())
            decision = decide(sig, pos, p, price)
            data_age_min = (now_ist() - price_time.astimezone(now_ist().tzinfo)).total_seconds() / 60

            action = self._maybe_execute(decision, price, pos, p, data_age_min)
            self.last_snapshot = {
                "status": "ok",
                "strategy": preset.name,
                "preset": preset.key,
                "symbol": p["symbol"],
                "price": round(price, 2),
                "price_time": price_time.isoformat(timespec="seconds"),
                "data_age_minutes": round(data_age_min, 1),
                "candles": len(close),
                "candle_interval": p["candle_interval"],
                **asdict(decision),
                **pos,
                "max_holding_qty": int(p["max_holding_qty"]),
                "min_profit_pct": float(p["min_profit_pct"]),
                "last_action": action,
                "checked_at": now_ist().isoformat(timespec="seconds"),
                "trigger": trigger,
            }
            if trigger != "scheduler":
                self._db.log("INFO", f"{trigger.capitalize()} check ({preset.name}): {decision.signal} - "
                                     f"{decision.reason}" + (f" | {action}" if action else ""))
        except Exception as exc:
            self._db.log("ERROR", f"Strategy check failed: {exc}")
            self.last_snapshot = {**self.last_snapshot, "status": "error", "error": str(exc),
                                  "checked_at": now_ist().isoformat(timespec="seconds")}
        finally:
            self._run_lock.release()
        return self.last_snapshot

    def _maybe_execute(self, d: Decision, price: float, pos: dict[str, Any],
                       p: dict[str, Any], data_age_min: float) -> str | None:
        if d.signal == "HOLD":
            self._last_block_msg = None
            return None

        self._ip.check()  # fresh public-IP reading right before deciding to send an order
        reasons = self.block_reasons()
        max_age = float(p.get("max_data_age_minutes", 20))
        if market_status()[0] and data_age_min > max_age:
            reasons.append(f"Price data is stale ({data_age_min:.0f} min old > {max_age:g})")
        if reasons:
            msg = f"{d.signal} signal BLOCKED: " + "; ".join(reasons)
            if msg != self._last_block_msg:  # don't spam the log every interval
                self._db.log("WARN", msg)
                self._last_block_msg = msg
            return msg
        self._last_block_msg = None

        if self.has_open_order():
            return f"{d.signal} skipped: an earlier order is still OPEN"
        last_fail = self._last_failure.get(d.signal)
        cooldown = timedelta(minutes=float(p["retry_cooldown_minutes"]))
        if last_fail and now_ist() - last_fail < cooldown:
            return f"{d.signal} skipped: cooling down after a failed order (until {(last_fail + cooldown):%H:%M})"

        limit = _limit_price(price, d.signal, float(p["limit_buffer_pct"]))
        if d.signal == "BUY":
            qty = min(int(p["order_quantity"]), int(p["max_holding_qty"]) - pos["algo_qty"])
        else:
            qty = pos["algo_qty"]
            if pos["demat_qty"] is not None:
                qty = min(qty, pos["demat_qty"])
            if d.min_sell_price:  # never place a SELL below the no-loss floor
                limit = max(limit, d.min_sell_price)
        if qty <= 0:
            return f"{d.signal} skipped: quantity is 0"
        return self._place(d.signal, qty, limit, p, d.reason)

    def _place(self, side: str, qty: int, limit: float, p: dict[str, Any], reason: str) -> str:
        symbol = p["symbol"]
        if self._settings.dry_run:
            oid = self._db.insert_order(mode="PAPER", tradingsymbol=symbol, transaction_type=side, quantity=qty,
                                        filled_quantity=qty, price=limit, status="COMPLETE",
                                        status_message="Paper trade: simulated fill at limit price")
            msg = f"PAPER {side} {qty} {symbol} @ {limit:.2f} LIMIT ({reason}) [{oid}]"
            self._db.log("TRADE", msg)
            return msg
        try:
            oid = self._kite.place_limit_order(transaction_type=side, quantity=qty, price=limit,
                                               tradingsymbol=symbol, exchange=p["exchange"], product=p["product"])
            self._db.insert_order(order_id=oid, mode="LIVE", tradingsymbol=symbol, transaction_type=side,
                                  quantity=qty, price=limit, status="OPEN", status_message="Submitted to Zerodha")
            msg = f"LIVE {side} {qty} {symbol} @ {limit:.2f} LIMIT placed ({reason}) [order {oid}]"
            self._db.log("TRADE", msg)
            return msg
        except Exception as exc:
            self._last_failure[side] = now_ist()
            local_id = self._db.insert_order(mode="LIVE", tradingsymbol=symbol, transaction_type=side, quantity=qty,
                                             price=limit, status="REJECTED",
                                             status_message=f"Zerodha API refused: {exc}")
            self._ip.report_zerodha_rejection(str(exc), local_id)
            msg = f"LIVE {side} {qty} {symbol} @ {limit:.2f} FAILED: {exc}"
            self._db.log("ERROR", msg)
            return msg

    # ---------- order book ----------
    def sync_live_orders(self) -> None:
        """Copy status/fills of our LIVE orders from Zerodha into algo.db (drives the algo position)."""
        if self._settings.dry_run or not self._kite.is_session_active():
            return
        local_open = self._db.orders(mode="LIVE", only_open=True)
        if not local_open:
            return
        try:
            remote = {str(o["order_id"]): o for o in self._kite.raw_orders()}
        except Exception as exc:
            self._db.log("WARN", f"Could not sync order status from Zerodha: {exc}")
            return
        today = now_ist().date().isoformat()
        for local in local_open:
            o = remote.get(local["order_id"])
            if o:
                n = normalize_kite_order(o)
                if n["status"] != local["status"] or n["filled_quantity"] != local["filled_quantity"]:
                    self._db.update_order(local["order_id"], status=n["status"], filled_quantity=n["filled_quantity"],
                                          status_message=n["status_message"], price=n["price"] or None)
                    self._db.log("TRADE" if n["status"] == "COMPLETE" else "WARN",
                                 f"Order {local['order_id']} {local['transaction_type']} -> {n['status']}"
                                 + (f": {n['status_message']}" if n["status_message"] else ""))
                    if n["status"] in {"REJECTED", "CANCELLED"}:
                        self._last_failure[local["transaction_type"]] = now_ist()
                    if n["status"] == "REJECTED":
                        self._ip.report_zerodha_rejection(n["status_message"] or "", local["order_id"])
            elif local["trade_date"] != today:
                self._db.update_order(local["order_id"], status="UNKNOWN", filled_quantity=local["filled_quantity"],
                                      status_message="Not in today's Zerodha order book - verify on Kite and fix algo.db")
                self._db.log("WARN", f"Order {local['order_id']} from {local['trade_date']} has unknown final status")

    def orders_today(self) -> dict[str, Any]:
        today = now_ist().date().isoformat()
        if self._settings.dry_run:
            rows = [{**o, "source": "PAPER"} for o in self._db.orders(mode="PAPER", trade_date=today)]
            return {"mode": "PAPER", "orders": rows, "error": None}

        local_today = self._db.orders(mode="LIVE", trade_date=today)
        if not self._kite.is_session_active():
            rows = [{**o, "source": "LOCAL"} for o in local_today]
            return {"mode": "LIVE", "orders": rows, "error": "Zerodha login required to load the order book"}
        try:
            self.sync_live_orders()
            remote = self._kite.orders_today()
        except Exception as exc:
            rows = [{**o, "source": "LOCAL"} for o in local_today]
            return {"mode": "LIVE", "orders": rows, "error": f"Kite orders() failed: {exc}"}
        remote_ids = {o["order_id"] for o in remote}
        # Orders Zerodha refused at submission never reach its order book - show them from our log.
        extra = [{**o, "source": "LOCAL"} for o in local_today if o["order_id"] not in remote_ids]
        rows = sorted(remote + extra, key=lambda o: o["timestamp"] or "", reverse=True)
        return {"mode": "LIVE", "orders": rows, "error": None}
