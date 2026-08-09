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
        # Неблокирующая миграция существующих баз.
        columns = {row[1] for row in c.execute("PRAGMA table_info(trades)")}
        migrations = {
            "context": "TEXT NOT NULL DEFAULT '[]'",
            "entry_tags": "TEXT NOT NULL DEFAULT '[]'",
            "exit_tags": "TEXT NOT NULL DEFAULT '[]'",
            "notes": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in migrations.items():
            if name not in columns:
                c.execute(f"ALTER TABLE trades ADD COLUMN {name} {ddl}")


def insert_trade(trade: dict, mode: str = "") -> int:
    with _lock, _conn() as c:
        cursor = c.execute(
            """INSERT INTO trades
               (symbol, side, qty, entry_price, exit_price, entry_ts, exit_ts,
                pnl, fee, mode, features, label, context, entry_tags, exit_tags, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                json.dumps(trade.get("context") or []),
                json.dumps(trade.get("entryTags") or []),
                json.dumps(trade.get("exitTags") or []),
                str(trade.get("notes") or ""),
            ),
        )
        return int(cursor.lastrowid)


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


def fetch_trade(trade_id: int) -> Optional[dict]:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    for key, fallback in (("features", {}), ("context", []),
                          ("entry_tags", []), ("exit_tags", [])):
        try:
            result[key] = json.loads(result.get(key) or json.dumps(fallback))
        except (TypeError, json.JSONDecodeError):
            result[key] = fallback
    return result


def update_trade_tags(trade_id: int, entry_tags: List[str], exit_tags: List[str],
                      notes: str = "") -> bool:
    with _lock, _conn() as c:
        cursor = c.execute(
            "UPDATE trades SET entry_tags=?, exit_tags=?, notes=? WHERE id=?",
            (json.dumps(entry_tags), json.dumps(exit_tags), notes, trade_id),
        )
        return cursor.rowcount > 0


def update_trade_context(trade_id: int, candles: List[dict]) -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE trades SET context=? WHERE id=?", (json.dumps(candles), trade_id))


def count_trades() -> int:
    with _lock, _conn() as c:
        (n,) = c.execute("SELECT COUNT(*) FROM trades").fetchone()
    return n
