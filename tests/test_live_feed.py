import asyncio
import unittest

from backend.feeds.live import LiveFeed, _clean_book_side, _clean_ohlcv


class FakeExchange:
    def __init__(self):
        self.closed = False
        self.book_calls = 0

    def milliseconds(self):
        return 180_000

    async def fetch_ohlcv(self, symbol, timeframe, limit, params=None):
        self.history_timeframe = timeframe
        self.history_limit = limit
        self.history_params = params
        return [
            [60_000, 10, 12, 9, 11, 50],
            [120_000, 11, 13, 0, 12, 50],
            [180_000, 12, 13, 11, 12, 20],
        ]

    async def fapiPublicGetKlines(self, params):
        self.delta_params = params
        return [
            [60_000, "10", "12", "9", "11", "10", 0, 0, 0, "7"],
            [180_000, "11", "12", "10", "11", "5", 0, 0, 0, "2"],
        ]

    async def fetch_open_interest_history(self, symbol, timeframe, since, limit):
        self.oi_timeframe = timeframe
        return [
            {"timestamp": 60_000, "openInterestAmount": 100},
            {"timestamp": 120_000, "openInterestAmount": 110},
        ]

    async def watch_trades(self, symbol):
        await asyncio.sleep(0)
        return [
            {"timestamp": 180_001, "price": 0, "amount": 1},
            {"timestamp": 180_002, "price": 12.5, "amount": 2},
        ]

    async def watch_order_book(self, symbol):
        self.book_calls += 1
        await asyncio.sleep(0)
        return {"bids": [[12.4, 3], [0, 10]], "asks": [[12.6, 4]]}

    async def fetch_open_interest(self, symbol):
        await asyncio.sleep(3600)

    async def fetch_funding_rate(self, symbol):
        await asyncio.sleep(3600)

    async def close(self):
        self.closed = True


class LiveFeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_book_uses_exchange_default_depth_and_bad_ticks_are_dropped(self):
        exchange = FakeExchange()
        feed = LiveFeed("binance", "TUT/USDT")

        async def fake_open():
            feed.exchange = exchange
            feed.tick_size = 0.0001
            feed.market_id = "TUTUSDT"

        feed._open = fake_open
        events = []
        async for event in feed.run():
            events.append(event)
            if any(e["type"] == "book" for e in events) and any(e["type"] == "tick" for e in events):
                await feed.stop()
                break

        self.assertEqual(events[0]["type"], "warmup")
        self.assertEqual(len(events[0]["candles"]), 1)
        self.assertEqual(events[0]["candles"][0]["delta"], 4.0)
        indicator_event = next(e for e in events if e["type"] == "indicator_history")
        self.assertEqual([point["value"] for point in indicator_event["oi"]], [100.0, 110.0])
        self.assertEqual(exchange.oi_timeframe, "5m")
        self.assertEqual(exchange.history_limit, 501)
        self.assertEqual(exchange.history_timeframe, "1m")
        self.assertEqual(exchange.history_params, {"paginate": True})
        self.assertEqual([e["price"] for e in events if e["type"] == "tick"], [12.5])
        self.assertGreater(exchange.book_calls, 0)
        self.assertTrue(exchange.closed)

    async def test_open_failure_closes_exchange(self):
        exchange = FakeExchange()
        feed = LiveFeed("binance", "TUT/USDT")

        async def failing_open():
            feed.exchange = exchange
            raise RuntimeError("no market")

        feed._open = failing_open
        events = [event async for event in feed.run()]
        self.assertEqual(events[0]["type"], "error")
        self.assertTrue(exchange.closed)


class LiveNormalizationTests(unittest.TestCase):
    def test_history_limit_is_clamped_and_can_be_disabled(self):
        self.assertEqual(LiveFeed("binance", "BTC/USDT", 99_999).history_limit, 3000)
        self.assertEqual(LiveFeed("binance", "BTC/USDT", 0).history_limit, 0)
        self.assertEqual(LiveFeed("binance", "BTC/USDT", "bad").history_limit, 500)
        self.assertEqual(LiveFeed("binance", "BTC/USDT", 10).history_limit, 80)

    def test_timeframe_is_validated(self):
        self.assertEqual(LiveFeed("binance", "BTC/USDT", timeframe="15m").timeframe, "15m")
        self.assertEqual(LiveFeed("binance", "BTC/USDT", timeframe="2m").timeframe, "1m")

    def test_book_is_sorted_filtered_and_limited(self):
        rows = [[2, 1], [3, 2], [0, 9], [1, -1], [4, float("nan")]]
        self.assertEqual(_clean_book_side(rows, 2, reverse=True), [[3.0, 2.0], [2.0, 1.0]])

    def test_ohlcv_rejects_zero_low_and_open_candle(self):
        self.assertIsNone(_clean_ohlcv([60_000, 1, 2, 0, 1, 5], 180_000))
        self.assertIsNone(_clean_ohlcv([180_000, 1, 2, 1, 2, 5], 180_000))
        self.assertEqual(_clean_ohlcv([60_000, 1, 2, 1, 2, 5], 180_000)["low"], 1.0)
