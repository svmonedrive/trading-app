"""Strategy presets + validation of their limits.

The user picks ONE strategy in the UI (it stays selected until changed). Each strategy keeps its own
saved limits; quantity / max holding / min profit / buffer / candle size are shared order settings.
The engine runs `compose_plan(...)`:
    {"strategy": <preset key>, "params": {...that preset's limits...}, **order_settings}

Presets only say *whether* their buy / sell condition is true. The engine decides what to do with
that (position limits, the no-loss rule, safety gates), so every preset gets the same protections.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import pandas as pd


# ---------------------------------------------------------------- field specs (rendered as a form in the UI)
@dataclass
class ParamSpec:
    key: str
    label: str
    kind: str = "float"  # int | float | select
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    help: str = ""
    options: list[dict[str, str]] | None = None  # for kind == "select": [{"value", "label"}]


def _opts(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"value": v, "label": l} for v, l in pairs]


CANDLE_OPTIONS = _opts(("1d", "Daily"), ("1h", "1 hour"), ("30m", "30 min"), ("15m", "15 min"), ("5m", "5 min"))
# yfinance limits how far back intraday data goes; pick the longest allowed window per interval.
HISTORY_FOR_INTERVAL = {"1d": "2y", "1h": "1y", "30m": "60d", "15m": "60d", "5m": "60d"}

COMMON_FIELDS: list[ParamSpec] = [
    ParamSpec("order_quantity", "Quantity per order", "int", 1, 1, 100_000, 1, "units",
              "How many NIFTYBEES units each BUY order is for."),
    ParamSpec("max_holding_qty", "Max holding by bot", "int", 1, 1, 1_000_000, 1, "units",
              "The bot stops buying once it holds this many (bought by the bot - your other holdings don't count)."),
    ParamSpec("min_profit_pct", "No-loss: min profit to sell", "float", 0.5, 0, 50, 0.05, "%",
              "A SELL only goes through if the sell price is at least this % above the bot's average buy price. "
              "Keep it above your charges (STT, exchange fees, DP charge) - otherwise a 'profit' can still lose money."),
    ParamSpec("limit_buffer_pct", "LIMIT price buffer", "float", 0.10, 0, 2, 0.01, "%",
              "BUY limit = price + buffer, SELL limit = price - buffer, so the order fills quickly."),
    ParamSpec("candle_interval", "Candle size", "select", "1d", options=CANDLE_OPTIONS,
              help="Daily candles suit swing trading; 5-15 min candles react within the day (data is from Yahoo Finance)."),
]


# ---------------------------------------------------------------- indicator helpers
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.where(avg_loss != 0, 100.0)


def _last(s: pd.Series, back: int = 1) -> float | None:
    if len(s) < back:
        return None
    v = s.iloc[-back]
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


def ind(label: str, value: float | None, note: str = "", unit: str = "",
        gauge: dict[str, float] | None = None) -> dict[str, Any]:
    """One stat tile in the UI. `gauge` = {min, max, low, high} draws a bar with buy/sell zones."""
    return {"label": label, "value": _r(value), "note": note, "unit": unit, "gauge": gauge}


def daily_closes(close: pd.Series) -> pd.Series:
    """Last close of each calendar day (works for daily and intraday candles)."""
    return close.groupby(close.index.date).last()


# ---------------------------------------------------------------- presets
@dataclass
class Signals:
    indicators: list[dict[str, Any]] = field(default_factory=list)
    buy: bool = False
    buy_reason: str = ""
    buy_rule: str = ""
    sell: bool = False
    sell_reason: str = ""
    sell_rule: str = ""
    ready: bool = True
    not_ready: str = ""


class Preset(ABC):
    key: str
    name: str
    description: str
    fields: list[ParamSpec]

    def validate(self, params: dict[str, Any]) -> None:
        """Cross-field checks; raise ValueError with a user-facing message."""

    @abstractmethod
    def evaluate(self, close: pd.Series, price: float, pos: dict[str, Any], p: dict[str, Any],
                 today: date) -> Signals: ...

    def describe(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "description": self.description,
                "fields": [asdict(f) for f in self.fields]}


class RSIMeanReversion(Preset):
    key = "rsi_mean_reversion"
    name = "RSI Mean Reversion"
    description = ("Buy a short-term dip while the long-term trend is up: RSI below the buy level and price above "
                   "the trend SMA. Sell when RSI recovers above the sell level.")
    fields = [
        ParamSpec("rsi_period", "RSI period", "int", 14, 2, 100, 1, "candles"),
        ParamSpec("rsi_buy_below", "Buy when RSI below", "float", 30, 1, 99, 1),
        ParamSpec("rsi_sell_above", "Sell when RSI above", "float", 65, 1, 99, 1),
        ParamSpec("sma_period", "Trend filter SMA", "int", 200, 0, 500, 1, "candles",
                  "Only buy while price is above this SMA. 0 = no trend filter."),
    ]

    def validate(self, p: dict[str, Any]) -> None:
        if p["rsi_buy_below"] >= p["rsi_sell_above"]:
            raise ValueError("'Buy when RSI below' must be lower than 'Sell when RSI above'")

    def evaluate(self, close, price, pos, p, today) -> Signals:
        n, m, lo, hi = int(p["rsi_period"]), int(p["sma_period"]), float(p["rsi_buy_below"]), float(p["rsi_sell_above"])
        rsi = _last(compute_rsi(close, n))
        trend = _last(sma(close, m)) if m else None
        tiles = [ind(f"RSI ({n})", rsi, gauge={"min": 0, "max": 100, "low": lo, "high": hi})]
        if m:
            tiles.append(ind(f"SMA ({m})", trend, ("Price above ▲" if price > trend else "Price below ▼") if trend else ""))
        if rsi is None or (m and trend is None):
            return Signals(tiles, ready=False, not_ready=f"Not enough candles ({len(close)}) for RSI{n}/SMA{m}")
        trend_ok = not m or price > trend
        buy_rule = f"RSI({n}) < {lo:g} (now {rsi:.1f})" + (
            f" AND price > SMA{m} {trend:.2f} ({'✓' if trend_ok else '✗'})" if m else "")
        return Signals(
            tiles,
            buy=rsi < lo and trend_ok, buy_reason=f"RSI {rsi:.1f} < {lo:g}" + (f", price above SMA{m}" if m else ""),
            buy_rule=buy_rule,
            sell=rsi > hi, sell_reason=f"RSI {rsi:.1f} > {hi:g}", sell_rule=f"RSI({n}) > {hi:g} (now {rsi:.1f})",
        )


class MACrossover(Preset):
    key = "ma_crossover"
    name = "Moving Average Crossover"
    description = ("Trend following: buy when the fast average crosses above the slow one (on the latest candle), "
                   "sell when it crosses back below.")
    fields = [
        ParamSpec("ma_type", "Average type", "select", "EMA", options=_opts(("EMA", "EMA"), ("SMA", "SMA"))),
        ParamSpec("fast_period", "Fast average", "int", 20, 2, 200, 1, "candles"),
        ParamSpec("slow_period", "Slow average", "int", 50, 3, 400, 1, "candles"),
    ]

    def validate(self, p: dict[str, Any]) -> None:
        if p["fast_period"] >= p["slow_period"]:
            raise ValueError("'Fast average' must be shorter than 'Slow average'")

    def evaluate(self, close, price, pos, p, today) -> Signals:
        f_n, s_n, kind = int(p["fast_period"]), int(p["slow_period"]), p["ma_type"]
        avg = ema if kind == "EMA" else sma
        fast, slow = avg(close, f_n), avg(close, s_n)
        f_now, s_now, f_prev, s_prev = _last(fast), _last(slow), _last(fast, 2), _last(slow, 2)
        tiles = [ind(f"{kind} ({f_n})", f_now, ("Above slow ▲" if f_now > s_now else "Below slow ▼")
                     if f_now is not None and s_now is not None else ""),
                 ind(f"{kind} ({s_n})", s_now)]
        if None in (f_now, s_now, f_prev, s_prev):
            return Signals(tiles, ready=False, not_ready=f"Not enough candles ({len(close)}) for {kind}{s_n}")
        crossed_up = f_prev <= s_prev and f_now > s_now
        crossed_down = f_prev >= s_prev and f_now < s_now
        return Signals(
            tiles,
            buy=crossed_up, buy_reason=f"{kind}{f_n} crossed above {kind}{s_n}",
            buy_rule=f"{kind}{f_n} crosses above {kind}{s_n} (now {f_now:.2f} vs {s_now:.2f})",
            sell=crossed_down, sell_reason=f"{kind}{f_n} crossed below {kind}{s_n}",
            sell_rule=f"{kind}{f_n} crosses below {kind}{s_n}",
        )


class BollingerBounce(Preset):
    key = "bollinger_bounce"
    name = "Bollinger Band Bounce"
    description = "Buy when price falls below the lower Bollinger band; sell when it rises to the middle or upper band."
    fields = [
        ParamSpec("bb_period", "Band period", "int", 20, 5, 200, 1, "candles"),
        ParamSpec("bb_std", "Band width", "float", 2.0, 0.5, 4, 0.1, "std dev"),
        ParamSpec("bb_exit", "Sell at", "select", "upper", options=_opts(("upper", "Upper band"), ("middle", "Middle band"))),
    ]

    def evaluate(self, close, price, pos, p, today) -> Signals:
        n, k = int(p["bb_period"]), float(p["bb_std"])
        mid_s = sma(close, n)
        sd = close.rolling(n).std()
        mid, dev = _last(mid_s), _last(sd)
        if mid is None or dev is None:
            return Signals([ind("Middle band", None)], ready=False,
                           not_ready=f"Not enough candles ({len(close)}) for a {n}-period band")
        lower, upper = mid - k * dev, mid + k * dev
        exit_level, exit_name = (upper, "upper") if p["bb_exit"] == "upper" else (mid, "middle")
        tiles = [ind("Lower band", lower, "BUY zone below"), ind("Middle band", mid),
                 ind("Upper band", upper, "SELL zone above" if exit_name == "upper" else "")]
        return Signals(
            tiles,
            buy=price < lower, buy_reason=f"Price {price:.2f} below lower band {lower:.2f}",
            buy_rule=f"price < lower band {lower:.2f}",
            sell=price > exit_level, sell_reason=f"Price {price:.2f} above {exit_name} band {exit_level:.2f}",
            sell_rule=f"price > {exit_name} band {exit_level:.2f}",
        )


class DipBuy(Preset):
    key = "dip_buy"
    name = "Buy the Dip %"
    description = ("Buy when price is a set % below the previous close (or the recent high); sell when it is a set % "
                   "above the bot's average buy price.")
    fields = [
        ParamSpec("dip_reference", "Dip measured from", "select", "prev_close",
                  options=_opts(("prev_close", "Previous day's close"), ("recent_high", "Highest close of last N days"))),
        ParamSpec("lookback_days", "N days (for recent high)", "int", 20, 2, 250, 1, "days"),
        ParamSpec("dip_pct", "Buy when down by", "float", 1.0, 0.1, 50, 0.1, "%"),
        ParamSpec("target_pct", "Sell when up from avg buy by", "float", 2.0, 0.1, 100, 0.1, "%"),
    ]

    def evaluate(self, close, price, pos, p, today) -> Signals:
        days = daily_closes(close)
        past = days[[d < today for d in days.index]]  # completed days only
        if past.empty:
            return Signals([], ready=False, not_ready="No previous-day data yet")
        if p["dip_reference"] == "prev_close":
            ref, ref_name = float(past.iloc[-1]), "previous close"
        else:
            n = int(p["lookback_days"])
            ref, ref_name = float(past.iloc[-n:].max()), f"{n}-day high"
        dip, target = float(p["dip_pct"]), float(p["target_pct"])
        buy_level = ref * (1 - dip / 100)
        avg = pos.get("avg_price")
        target_level = avg * (1 + target / 100) if avg else None
        tiles = [ind(ref_name.capitalize(), ref, unit="₹"),
                 ind("Buy at or below", buy_level, f"{(price / ref - 1) * 100:+.2f}% vs {ref_name}", unit="₹")]
        if target_level:
            tiles.append(ind("Sell target", target_level, f"avg buy {avg:.2f} +{target:g}%", unit="₹"))
        return Signals(
            tiles,
            buy=price <= buy_level, buy_reason=f"Price {price:.2f} is {dip:g}%+ below {ref_name} {ref:.2f}",
            buy_rule=f"price ≤ {buy_level:.2f} ({dip:g}% below {ref_name} {ref:.2f})",
            sell=bool(target_level and price >= target_level),
            sell_reason=f"Price {price:.2f} reached target {target_level:.2f}" if target_level else "",
            sell_rule=f"price ≥ {target_level:.2f} (avg buy +{target:g}%)" if target_level else f"avg buy +{target:g}%",
        )


class PriceLevels(Preset):
    key = "price_levels"
    name = "Fixed Price Levels"
    description = "Buy at or below an exact price, sell at or above an exact price. 0 = off."
    fields = [
        ParamSpec("buy_at_or_below", "Buy at or below", "float", 0, 0, 100_000, 0.01, "₹", "0 = don't buy"),
        ParamSpec("sell_at_or_above", "Sell at or above", "float", 0, 0, 100_000, 0.01, "₹", "0 = don't sell"),
    ]

    def validate(self, p: dict[str, Any]) -> None:
        b, s = p["buy_at_or_below"], p["sell_at_or_above"]
        if b <= 0 and s <= 0:
            raise ValueError("Set at least one of 'Buy at or below' / 'Sell at or above'")
        if b > 0 and s > 0 and b >= s:
            raise ValueError("'Buy at or below' must be lower than 'Sell at or above'")

    def evaluate(self, close, price, pos, p, today) -> Signals:
        b, s = float(p["buy_at_or_below"]), float(p["sell_at_or_above"])
        tiles = []
        if b > 0:
            tiles.append(ind("Buy level", b, f"price {(price / b - 1) * 100:+.2f}% away", unit="₹"))
        if s > 0:
            tiles.append(ind("Sell level", s, f"price {(price / s - 1) * 100:+.2f}% away", unit="₹"))
        return Signals(
            tiles,
            buy=b > 0 and price <= b, buy_reason=f"Price {price:.2f} ≤ {b:.2f}",
            buy_rule=f"price ≤ {b:.2f}" if b > 0 else "",
            sell=s > 0 and price >= s, sell_reason=f"Price {price:.2f} ≥ {s:.2f}",
            sell_rule=f"price ≥ {s:.2f}" if s > 0 else "",
        )


PRESETS: dict[str, Preset] = {p.key: p for p in
                              [RSIMeanReversion(), MACrossover(), BollingerBounce(), DipBuy(), PriceLevels()]}


# ---------------------------------------------------------------- plan validation / params
def _coerce(spec: ParamSpec, raw: Any) -> Any:
    if raw is None or raw == "":
        raw = spec.default
    if spec.kind == "select":
        allowed = [o["value"] for o in spec.options or []]
        if raw not in allowed:
            raise ValueError(f"'{spec.label}' must be one of: {', '.join(allowed)}")
        return raw
    try:
        num = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{spec.label}' must be a number") from None
    if math.isnan(num) or math.isinf(num):
        raise ValueError(f"'{spec.label}' must be a number")
    if spec.kind == "int":
        if num != int(num):
            raise ValueError(f"'{spec.label}' must be a whole number")
        num = int(num)
    if spec.min is not None and num < spec.min:
        raise ValueError(f"'{spec.label}' must be at least {spec.min:g}")
    if spec.max is not None and num > spec.max:
        raise ValueError(f"'{spec.label}' must be at most {spec.max:g}")
    return num


def get_preset(key: str | None) -> Preset:
    preset = PRESETS.get(key or "")
    if not preset:
        raise ValueError(f"Unknown strategy '{key}'")
    return preset


def validate_limits(key: str, raw: dict[str, Any] | None, strict: bool = True) -> dict[str, Any]:
    """Clean limits for one strategy (unknown keys dropped, defaults filled) or raise ValueError.
    strict=False skips the cross-field checks - used only to *display* limits that aren't set up yet
    (e.g. Fixed Price Levels starts with both prices off). Saving, selecting and running are always strict."""
    preset = get_preset(key)
    raw = raw or {}
    params = {f.key: _coerce(f, raw.get(f.key)) for f in preset.fields}
    if strict:
        preset.validate(params)
    return params


def validate_order_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    out = {f.key: _coerce(f, raw.get(f.key)) for f in COMMON_FIELDS}
    if out["max_holding_qty"] < out["order_quantity"]:
        raise ValueError("'Max holding by bot' must be at least 'Quantity per order'")
    return out


def compose_plan(active: str, limits: dict[str, dict[str, Any]], order_settings: dict[str, Any]) -> dict[str, Any]:
    """The plan the engine runs: selected strategy + its saved limits + the shared order settings."""
    return {"strategy": active, "params": validate_limits(active, limits.get(active)),
            **validate_order_settings(order_settings)}


def build_params(system: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Flat dict the engine uses: system settings + plan settings + the preset's own params."""
    p = {**system, **{k: v for k, v in plan.items() if k != "params"}, **plan["params"]}
    p["history_period"] = HISTORY_FOR_INTERVAL.get(p["candle_interval"], "2y")
    return p


def presets_catalog() -> dict[str, Any]:
    return {"presets": [p.describe() for p in PRESETS.values()],
            "common_fields": [asdict(f) for f in COMMON_FIELDS]}
