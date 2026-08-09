import math
import unittest

from backend.market import CandleBuilder, MarketState


class CandleBuilderTests(unittest.TestCase):
    def test_zero_nan_and_negative_prices_do_not_change_candle(self):
        builder = CandleBuilder()
        builder.update(10, 2, 60_000)
        original = dict(builder.current)
        for price in (0, -1, math.nan, math.inf):
            builder.update(price, 1, 61_000)
        self.assertEqual(builder.current, original)

    def test_late_trade_cannot_mutate_current_minute(self):
        builder = CandleBuilder()
        builder.update(10, 1, 120_000)
        builder.update(0.5, 100, 60_000)
        self.assertEqual(builder.current["low"], 10)
        self.assertEqual(builder.current["volume"], 1)

    def test_ohlc_is_built_from_valid_ticks(self):
        builder = CandleBuilder()
        for price in (10, 12, 9, 11):
            builder.update(price, 1, 60_000)
        self.assertEqual(
            {key: builder.current[key] for key in ("open", "high", "low", "close", "volume")},
            {"open": 10, "high": 12, "low": 9, "close": 11, "volume": 4},
        )


class MarketStateTests(unittest.TestCase):
    def test_out_of_order_tick_is_ignored_entirely(self):
        state = MarketState()
        state.on_tick(10, 1, 120_000)
        state.on_tick(2, 1, 119_999)
        self.assertEqual(state.last_price, 10)
        self.assertEqual(state.last_ts, 120_000)
