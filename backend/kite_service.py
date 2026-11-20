"""Zerodha Kite Connect wrapper — used ONLY for login, order placement, order book and positions.

Market data comes from yfinance (see strategy.py) because the free Personal API tier has no data feed.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from kiteconnect import KiteConnect
from kiteconnect import exceptions as kite_exc

from config import ConfigStore, Settings, now_ist, token_is_fresh
from db import TradeDB

_CACHE_TTL_SECONDS = 4.0  # dashboard polls every 5 s; stay well under Kite's rate limits


class KiteNotReady(RuntimeError):
    pass


class KiteService:
    def __init__(self, settings: Settings, store: ConfigStore, db: TradeDB):
        self._settings = settings
        self._store = store
        self._db = db
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, Any]] = {}
        self.kite: KiteConnect | None = None
        if settings.kite_api_key:
            self.kite = KiteConnect(api_key=settings.kite_api_key)
            token = store.get("access_token")
            if token and token_is_fresh(store.get("access_token_created_at")):
                self.kite.set_access_token(token)

    # ---------------- auth ----------------
    @property
    def api_configured(self) -> bool:
        return bool(self._settings.kite_api_key and self._settings.kite_api_secret)

    def login_url(self) -> str:
        if not self.kite:
            raise KiteNotReady("KITE_API_KEY missing in backend/.env")
        return self.kite.login_url()

    def handle_callback(self, request_token: str) -> dict[str, Any]:
        if not self.kite or not self.api_configured:
            raise KiteNotReady("KITE_API_KEY / KITE_API_SECRET missing in backend/.env")
        session = self.kite.generate_session(request_token, api_secret=self._settings.kite_api_secret)
        self.kite.set_access_token(session["access_token"])
        self._store.set(
            access_token=session["access_token"],
            access_token_created_at=now_ist().isoformat(timespec="seconds"),
            user_name=session.get("user_name"),
            user_id=session.get("user_id"),
        )
        self._cache.clear()
        self._db.log("INFO", f"Zerodha session started for {session.get('user_name')} ({session.get('user_id')}).")
        return session

    def is_session_active(self) -> bool:
        return bool(
            self.kite
            and self._store.get("access_token")
            and token_is_fresh(self._store.get("access_token_created_at"))
        )

    def session_status(self) -> dict[str, Any]:
        return {
            "active": self.is_session_active(),
            "api_configured": self.api_configured,
            "user_name": self._store.get("user_name"),
            "user_id": self._store.get("user_id"),
            "login_time": self._store.get("access_token_created_at"),
        }

    def invalidate_session(self, reason: str) -> None:
        if self._store.get("access_token"):
            self._db.log("WARN", f"Zerodha session invalidated: {reason}")
        self._store.set(access_token=None, access_token_created_at=None)
        self._cache.clear()

    def _call(self, fn, *args, **kwargs):
        """Run a Kite API call; drop the session if Zerodha says the token is dead."""
        if not self.is_session_active():
            raise KiteNotReady("Zerodha login required")
        try:
            return fn(*args, **kwargs)
        except kite_exc.TokenException as exc:
            self.invalidate_session(str(exc))
            raise

    def _cached(self, key: str, loader):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
                return hit[1]
        value = loader()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    # ---------------- orders ----------------
    def place_limit_order(self, *, transaction_type: str, quantity: int, price: float,
                          tradingsymbol: str, exchange: str, product: str) -> str:
        assert self.kite is not None
        order_id = self._call(
            self.kite.place_order,
            variety=self.kite.VARIETY_REGULAR,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            product=product,
            order_type=self.kite.ORDER_TYPE_LIMIT,  # never MARKET
            price=price,
            validity=self.kite.VALIDITY_DAY,
            tag="nbeesalgo",
        )
        self._cache.pop("orders", None)
        return str(order_id)

    def raw_orders(self) -> list[dict[str, Any]]:
        assert self.kite is not None
        return self._cached("orders", lambda: self._call(self.kite.orders))

    def orders_today(self) -> list[dict[str, Any]]:
        return [normalize_kite_order(o) for o in self.raw_orders()]

    def demat_quantity(self, tradingsymbol: str) -> int:
        """Holdings (incl. T1) + today's CNC net position for the symbol."""
        assert self.kite is not None

        def load() -> int:
            qty = 0
            for h in self._call(self.kite.holdings):
                if h.get("tradingsymbol") == tradingsymbol:
                    qty += int(h.get("quantity", 0)) + int(h.get("t1_quantity", 0))
            for p in self._call(self.kite.positions).get("net", []):
                if p.get("tradingsymbol") == tradingsymbol and p.get("product") == "CNC":
                    qty += int(p.get("quantity", 0))
            return qty

        return self._cached(f"qty:{tradingsymbol}", load)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value) if value else None


def normalize_kite_order(o: dict[str, Any]) -> dict[str, Any]:
    status = o.get("status") or "UNKNOWN"
    price = o.get("average_price") if status == "COMPLETE" and o.get("average_price") else o.get("price")
    return {
        "order_id": str(o.get("order_id")),
        "timestamp": _iso(o.get("order_timestamp")),
        "tradingsymbol": o.get("tradingsymbol"),
        "transaction_type": o.get("transaction_type"),
        "order_type": o.get("order_type"),
        "quantity": int(o.get("quantity") or 0),
        "filled_quantity": int(o.get("filled_quantity") or 0),
        "price": float(price or 0),
        "status": status,
        "status_message": o.get("status_message") or o.get("status_message_raw"),
        "source": "ZERODHA",
    }
