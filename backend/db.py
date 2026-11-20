"""SQLite trade log (algo.db): every order the bot attempts (paper or live) plus an activity/event log."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from config import now_ist

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    mode            TEXT NOT NULL,          -- PAPER | LIVE
    timestamp       TEXT NOT NULL,          -- ISO, IST
    trade_date      TEXT NOT NULL,          -- YYYY-MM-DD (IST)
    tradingsymbol   TEXT NOT NULL,
    transaction_type TEXT NOT NULL,         -- BUY | SELL
    quantity        INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    price           REAL NOT NULL,
    status          TEXT NOT NULL,          -- COMPLETE | OPEN | REJECTED | CANCELLED | UNKNOWN
    status_message  TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level     TEXT NOT NULL,                -- INFO | WARN | ERROR | TRADE
    message   TEXT NOT NULL
);
"""

FINAL_STATUSES = {"COMPLETE", "REJECTED", "CANCELLED", "UNKNOWN"}


class TradeDB:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------- events ----------
    def log(self, level: str, message: str) -> None:
        print(f"[{level}] {message}")
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (timestamp, level, message) VALUES (?, ?, ?)",
                (now_ist().isoformat(timespec="seconds"), level, message),
            )
            self._conn.commit()

    def last_event_time(self, message_prefix: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT timestamp FROM events WHERE message LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 1",
                (message_prefix.replace("%", r"\%").replace("_", r"\_") + "%",),
            ).fetchone()
        return row["timestamp"] if row else None

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT timestamp, level, message FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- orders ----------
    def insert_order(
        self,
        *,
        mode: str,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        price: float,
        status: str,
        status_message: str | None = None,
        order_id: str | None = None,
        filled_quantity: int = 0,
    ) -> str:
        now = now_ist()
        order_id = order_id or f"{mode[:1]}-{now:%Y%m%d}-{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._conn.execute(
                """INSERT INTO orders (order_id, mode, timestamp, trade_date, tradingsymbol, transaction_type,
                   quantity, filled_quantity, price, status, status_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id, mode, now.isoformat(timespec="seconds"), now.date().isoformat(),
                    tradingsymbol, transaction_type, quantity, filled_quantity, price, status, status_message,
                ),
            )
            self._conn.commit()
        return order_id

    def update_order(self, order_id: str, *, status: str, filled_quantity: int,
                     status_message: str | None, price: float | None = None) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE orders SET status = ?, filled_quantity = ?, status_message = ?,
                   price = COALESCE(?, price) WHERE order_id = ?""",
                (status, filled_quantity, status_message, price, order_id),
            )
            self._conn.commit()

    def orders(self, *, mode: str, trade_date: str | None = None, only_open: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM orders WHERE mode = ?"
        args: list[Any] = [mode]
        if trade_date:
            sql += " AND trade_date = ?"
            args.append(trade_date)
        if only_open:
            sql += f" AND status NOT IN ({','.join('?' * len(FINAL_STATUSES))})"
            args.extend(sorted(FINAL_STATUSES))
        sql += " ORDER BY timestamp DESC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def algo_position_detail(self, *, mode: str, tradingsymbol: str) -> dict[str, Any]:
        """Bot's net quantity and average buy price (average-cost method: sells don't change the average).
        Uses filled quantities and fill prices of the bot's own orders only."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT transaction_type, filled_quantity, price FROM orders
                   WHERE mode = ? AND tradingsymbol = ? AND filled_quantity > 0
                   ORDER BY timestamp, rowid""",
                (mode, tradingsymbol),
            ).fetchall()
        qty, cost = 0, 0.0
        for r in rows:
            q = int(r["filled_quantity"])
            if r["transaction_type"] == "BUY":
                qty, cost = qty + q, cost + q * float(r["price"])
            else:
                if qty <= 0:
                    continue
                q = min(q, qty)
                cost -= cost / qty * q
                qty -= q
        avg = round(cost / qty, 4) if qty > 0 else None
        return {"qty": qty, "avg_price": avg}

    def algo_position(self, *, mode: str, tradingsymbol: str) -> int:
        """Net quantity bought by THIS bot (filled BUYs - filled SELLs). Never touches your other demat holdings."""
        with self._lock:
            row = self._conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN transaction_type = 'BUY' THEN filled_quantity
                                            ELSE -filled_quantity END), 0) AS qty
                   FROM orders WHERE mode = ? AND tradingsymbol = ?""",
                (mode, tradingsymbol),
            ).fetchone()
        return int(row["qty"])
