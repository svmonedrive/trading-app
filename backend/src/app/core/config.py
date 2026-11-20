"""Environment settings, persistent JSON state (config.json) and NSE market-hours helpers."""
from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# backend\  (this file is backend\src\app\core\config.py)
BASE_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = BASE_DIR / "data"  # runtime data: config.json (saved state, Zerodha token) + algo.db (orders, log)
DATA_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    kite_api_key: str = os.getenv("KITE_API_KEY", "").strip()
    kite_api_secret: str = os.getenv("KITE_API_SECRET", "").strip()
    dry_run: bool = _env_bool("DRY_RUN", True)
    # Paper mode only: allow simulated orders outside 09:15-15:30 so the flow can be tested on weekends.
    paper_ignore_market_hours: bool = _env_bool("PAPER_IGNORE_MARKET_HOURS", False)
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:4200").rstrip("/")
    ip_check_interval_seconds: int = int(os.getenv("IP_CHECK_INTERVAL_SECONDS", "15"))
    strategy_interval_seconds: int = int(os.getenv("STRATEGY_INTERVAL_SECONDS", "300"))
    # Dual-stack lookup: returns the IP (v4 or v6) this PC actually uses for outbound traffic,
    # i.e. the same IP Zerodha sees on api.kite.trade (which is also dual-stack).
    ip_lookup_url: str = os.getenv("IP_LOOKUP_URL", "https://api64.ipify.org?format=json")
    # Second, independent service used to detect ISPs that rotate shared IPs per connection. Empty = off.
    ip_secondary_lookup_url: str = os.getenv("IP_SECONDARY_LOOKUP_URL", "https://icanhazip.com")
    config_path: Path = DATA_DIR / "config.json"
    db_path: Path = DATA_DIR / "algo.db"

    @property
    def mode(self) -> str:
        return "PAPER" if self.dry_run else "LIVE"


settings = Settings()


DEFAULT_CONFIG: dict[str, Any] = {
    "whitelisted_ip": None,  # IPv4 or IPv6 registered on developers.kite.trade
    "whitelisted_at": None,  # when the user last confirmed it
    "last_seen_ip": None,  # last successfully detected public IP
    "ip_changes": [],  # newest first: {"from_ip", "to_ip", "at"}
    "zerodha_ip_rejection": None,  # set when Zerodha rejects an order because of the IP; cleared on confirm
    "access_token": None,
    "access_token_created_at": None,
    "user_name": None,
    "user_id": None,
    "algo_active": False,
    # Optional list of NSE trading holidays as "YYYY-MM-DD" strings (weekends are handled automatically).
    "market_holidays": [],
    # System settings shared by every strategy (not edited from the strategy UI).
    "strategy": {
        "symbol": "NIFTYBEES",
        "exchange": "NSE",
        "yf_ticker": "NIFTYBEES.NS",
        "product": "CNC",
        "retry_cooldown_minutes": 15,  # wait after a failed/rejected order before retrying the same side
        "max_data_age_minutes": 20,  # refuse to trade on a stale yfinance price during market hours
    },
    # The strategy the bot runs (key in presets.PRESETS). Stays selected until changed in the UI.
    "active_strategy": "rsi_mean_reversion",
    # Saved limits per strategy: {preset key: {field: value}}. Missing strategies use their defaults.
    "strategy_limits": {
        "rsi_mean_reversion": {"rsi_period": 14, "rsi_buy_below": 30.0, "rsi_sell_above": 65.0, "sma_period": 200},
    },
    # Shared by every strategy (see presets.COMMON_FIELDS).
    "order_settings": {
        "order_quantity": 1,
        "max_holding_qty": 1,
        "min_profit_pct": 0.5,
        "limit_buffer_pct": 0.10,
        "candle_interval": "1d",
    },
}

# Keys that used to live in "strategy" before strategies were selectable (migrated on load).
_LEGACY_KEYS = {"strategy", "rsi_period", "rsi_buy_below", "rsi_sell_above", "sma_period",
                "order_quantity", "limit_buffer_pct", "candle_interval", "history_period"}
_RSI_KEYS = ("rsi_period", "rsi_buy_below", "rsi_sell_above", "sma_period")


class ConfigStore:
    """Thread-safe JSON store with atomic writes."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        data = copy.deepcopy(DEFAULT_CONFIG)
        if self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf-8"))
                saved_strategy = saved.get("strategy", {})
                if "active_strategy" not in saved:  # migrate the old flat RSI settings
                    legacy = {k: v for k, v in saved_strategy.items() if k in _LEGACY_KEYS}
                    rsi = data["strategy_limits"]["rsi_mean_reversion"]
                    rsi.update({k: legacy[k] for k in _RSI_KEYS if k in legacy})
                    orders = data["order_settings"]
                    orders.update({k: legacy[k] for k in ("order_quantity", "limit_buffer_pct", "candle_interval")
                                   if k in legacy})
                    orders["max_holding_qty"] = max(orders["max_holding_qty"], orders["order_quantity"])
                for obsolete in ("default_plan", "day_plans"):  # short-lived day-plan format
                    saved.pop(obsolete, None)
                strategy = {**data["strategy"],
                            **{k: v for k, v in saved_strategy.items() if k not in _LEGACY_KEYS}}
                data.update(saved)
                data["strategy"] = strategy
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[config] could not read {self._path}: {exc}; using defaults")
        return data

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

    def set(self, **values: Any) -> None:
        with self._lock:
            self._data.update(values)
            self._save()

    def system_settings(self) -> dict[str, Any]:
        """Symbol, exchange, product, cooldowns - shared by every strategy."""
        return self.get("strategy")

    # ---------- strategy selection ----------
    def strategy_selection(self) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any]]:
        """(active strategy key, saved limits per strategy, shared order settings)."""
        with self._lock:
            return (self._data["active_strategy"], copy.deepcopy(self._data["strategy_limits"]),
                    copy.deepcopy(self._data["order_settings"]))

    def set_strategy_limits(self, key: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._data["strategy_limits"][key] = params
            self._save()


config_store = ConfigStore(settings.config_path)

def now_ist() -> datetime:
    return datetime.now(IST)


def market_status(now: datetime | None = None) -> tuple[bool, str]:
    """Return (is_open, human readable reason) for NSE cash-market hours."""
    now = now or now_ist()
    if now.weekday() >= 5:
        return False, "Market closed (weekend)"
    if now.date().isoformat() in set(config_store.get("market_holidays", [])):
        return False, "Market closed (NSE holiday)"
    if now.time() < MARKET_OPEN:
        return False, "Market not open yet (opens 09:15 IST)"
    if now.time() >= MARKET_CLOSE:
        return False, "Market closed for the day (closed 15:30 IST)"
    return True, "Market open"


def token_is_fresh(created_at_iso: str | None) -> bool:
    """Kite access tokens expire at ~06:00 IST the next morning."""
    if not created_at_iso:
        return False
    try:
        created = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return False
    now = now_ist()
    cutoff = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < cutoff:
        cutoff -= timedelta(days=1)
    return created >= cutoff
