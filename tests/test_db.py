import json
import asyncio
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend import db
from backend.session import Session


class TradeDatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "trades.db")
        self.path_patch = patch.object(db, "DB_PATH", self.path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_existing_schema_is_migrated_without_losing_trade(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT, qty REAL,
                entry_price REAL, exit_price REAL, entry_ts INTEGER, exit_ts INTEGER,
                pnl REAL, fee REAL, mode TEXT, features TEXT, label INTEGER)""")
            connection.execute("""INSERT INTO trades
                (symbol, side, qty, entry_price, exit_price, entry_ts, exit_ts,
                 pnl, fee, mode, features, label)
                VALUES ('BTC/USDT','buy',1,10,11,1,2,1,0,'live','{}',1)""")

        db.init_db()
        trade = db.fetch_trade(1)

        self.assertEqual(trade["symbol"], "BTC/USDT")
        self.assertEqual(trade["context"], [])
        self.assertEqual(trade["entry_tags"], [])
        self.assertEqual(trade["exit_tags"], [])
        self.assertEqual(trade["entry_actions"], [])
        self.assertEqual(trade["exit_features"], {})
        self.assertIsNone(trade["exit_label"])

    def test_context_features_and_tags_round_trip(self):
        db.init_db()
        trade_id = db.insert_trade({
            "symbol": "TUT/USDT", "side": "buy", "qty": 2,
            "entryPrice": .02, "exitPrice": .021, "entryTs": 60_000,
            "exitTs": 120_000, "pnl": 2, "fee": .1, "label": 1,
            "features": {"rsi14": 55.5},
            "entryActions": [{"action": "entry", "qty": 2,
                              "features": {"rsi14": 55.5}}],
            "exitFeatures": {"position_pnl_pct": 5.0}, "exitLabel": 1,
            "context": [{"time": 60, "open": .02, "high": .021,
                         "low": .019, "close": .0205, "volume": 10}],
        }, "live")
        self.assertTrue(db.update_trade_tags(
            trade_id, ["пробой", "объём"], ["тейк"], "по плану",
        ))

        trade = db.fetch_trade(trade_id)
        self.assertEqual(trade["features"], {"rsi14": 55.5})
        self.assertEqual(len(trade["entry_actions"]), 1)
        self.assertEqual(trade["exit_features"], {"position_pnl_pct": 5.0})
        self.assertEqual(trade["exit_label"], 1)
        self.assertEqual(trade["entry_tags"], ["пробой", "объём"])
        self.assertEqual(trade["exit_tags"], ["тейк"])
        self.assertEqual(trade["notes"], "по плану")
        self.assertEqual(len(trade["context"]), 1)

    def test_each_trade_keeps_its_own_context(self):
        db.init_db()
        base = {
            "symbol": "TUT/USDT", "side": "buy", "qty": 1,
            "entryPrice": .02, "exitPrice": .021, "pnl": 1, "fee": .01, "label": 1,
        }
        first_id = db.insert_trade({
            **base, "entryTs": 60_000, "exitTs": 120_000,
            "context": [{"time": 60, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}],
        })
        second_id = db.insert_trade({
            **base, "entryTs": 600_000, "exitTs": 660_000,
            "context": [{"time": 600, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 2}],
        })

        first = db.fetch_trade(first_id)
        second = db.fetch_trade(second_id)
        self.assertEqual(first["context"][0]["time"], 60)
        self.assertEqual(second["context"][0]["time"], 600)
        self.assertNotEqual(first["context"], second["context"])


class TradeDetailProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(db, "DB_PATH", os.path.join(self.tmp.name, "trades.db"))
        self.path_patch.start()
        db.init_db()

    async def asyncTearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    async def test_closed_trade_exposes_context_features_and_editable_tags(self):
        queue = asyncio.Queue()
        session = Session(queue)
        session.mode = "live"
        session.symbol = session.state.symbol = "TUT/USDT"
        for minute in range(80):
            session.state.candles.append({
                "time": minute * 60, "open": .02, "high": .021,
                "low": .019, "close": .0205, "volume": 10, "delta": 2,
            })
        session.state.last_ts = 79 * 60_000
        session.state.last_price = .0205
        session.state.on_book([[.0204, 10000]], [[.0206, 10000]])

        await session.handle({"type": "order", "side": "buy", "sizeUsd": 100})
        session.state.last_ts += 10_000
        await session.handle({"type": "close_position"})
        await session.handle({"type": "get_trade_detail", "tradeId": 1,
                              "prefixMinutes": 30, "suffixMinutes": 15})

        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait())
        detail = next(m["trade"] for m in messages if m["type"] == "trade_detail")
        self.assertEqual(detail["id"], 1)
        self.assertTrue(detail["candles"])
        self.assertIn("rsi14", detail["features"])
        self.assertEqual(len(detail["entryActions"]), 1)
        self.assertIn("position_pnl_pct", detail["exitFeatures"])

        await session.handle({"type": "update_trade_tags", "tradeId": 1,
                              "entryTags": ["пробой"], "exitTags": ["ручной"],
                              "notes": "тест", "prefixMinutes": 30, "suffixMinutes": 15})
        saved = db.fetch_trade(1)
        self.assertEqual(saved["entry_tags"], ["пробой"])
        self.assertEqual(saved["exit_tags"], ["ручной"])
