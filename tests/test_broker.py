import unittest
import asyncio
from unittest.mock import patch

from backend.broker import PaperBroker
from backend.market import MarketState
from backend.session import Session


class ClosePositionTests(unittest.TestCase):
    def setUp(self):
        self.state = MarketState("TUT/USDT", tick_size=0.00001)
        self.state.on_book([[0.01999, 10000]], [[0.02001, 10000]])
        self.broker = PaperBroker(self.state)
        self.events = []
        self.broker.subscribe(lambda kind, payload: self.events.append((kind, payload)))

    def test_close_long_at_best_bid(self):
        self.broker.place_market("buy", 5000, 1)
        result = self.broker.close_position(2)

        self.assertEqual(result["price"], 0.01999)
        self.assertEqual(self.broker.position.qty, 0)
        self.assertTrue(any(kind == "trade_closed" for kind, _ in self.events))
        self.assertEqual([p["side"] for k, p in self.events if k == "fill"], ["buy", "sell"])

    def test_close_short_at_best_ask(self):
        self.broker.place_market("sell", 5000, 1)
        result = self.broker.close_position(2)

        self.assertEqual(result["price"], 0.02001)
        self.assertEqual(self.broker.position.qty, 0)

    def test_close_without_position_returns_none(self):
        self.assertIsNone(self.broker.close_position(1))

    def test_market_order_rejects_missing_price(self):
        state = MarketState("TUT/USDT")
        broker = PaperBroker(state)
        with self.assertRaisesRegex(ValueError, "рыночной цены"):
            broker.place_market("buy", 1, 1)


class SessionClosePositionTests(unittest.IsolatedAsyncioTestCase):
    def test_taker_ratio_is_reconstructed_from_delta_history(self):
        session = Session(asyncio.Queue())
        session._update_taker_ratio({"volume": 10, "delta": 4})
        self.assertAlmostEqual(session.state.deriv.taker_ls_ratio, 7 / 3)

    async def test_websocket_command_closes_and_acknowledges_position(self):
        queue = asyncio.Queue()
        session = Session(queue)
        session.mode = "live"
        session.symbol = "TUT/USDT"
        session.state.symbol = session.symbol
        session.state.on_book([[0.01999, 10000]], [[0.02001, 10000]])
        session.state.last_ts = 1_786_266_010_000

        with patch("backend.session.insert_trade") as insert, \
                patch("backend.session.count_trades", return_value=1):
            await session.handle({
                "type": "order", "side": "buy", "orderType": "market", "sizeUsd": 100,
            })
            await session.handle({"type": "close_position"})

        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait())
        self.assertEqual(session.broker.position.qty, 0)
        self.assertEqual(insert.call_count, 1)
        self.assertTrue(any(m.get("type") == "position" and m["data"]["qty"] == 0
                            for m in messages))
        self.assertTrue(any(m.get("type") == "notification" and "Позиция закрыта" in m["text"]
                            for m in messages))
