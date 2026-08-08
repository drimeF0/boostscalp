"""Хранение закрытых сделок в SQLite (для обучения мета-модели)."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import List, Optional

from .config import DB_PATH

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                qty REAL,
                entry_price REAL,
                exit_price REAL,
                entry_ts INTEGER,
                exit_ts INTEGER,
                pnl REAL,
                fee REAL,
                mode TEXT,
                features TEXT,
                label INTEGER
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit ON trades(exit_ts)")


def insert_trade(trade: dict, mode: str = ""):
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO trades
               (symbol, side, qty, entry_price, exit_price, entry_ts, exit_ts,
                pnl, fee, mode, features, label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trade.get("symbol", ""),
                trade.get("side", ""),
                trade.get("qty", 0.0),
                trade.get("entryPrice", 0.0),
                trade.get("exitPrice", 0.0),
                trade.get("entryTs", 0),
                trade.get("exitTs", 0),
                trade.get("pnl", 0.0),
                trade.get("fee", 0.0),
                mode,
                json.dumps(trade.get("features") or {}),
                int(trade.get("label", 0)),
            ),
        )


def fetch_trades(limit: int = 500) -> List[dict]:
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT * FROM trades ORDER BY exit_ts DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["features"] = json.loads(d.get("features") or "{}")
        except Exception:
            d["features"] = {}
        out.append(d)
    return out


def count_trades() -> int:
    with _lock, _conn() as c:
        (n,) = c.execute("SELECT COUNT(*) FROM trades").fetchone()
    return n
