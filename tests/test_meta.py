import math
import unittest

import pandas as pd

from backend.broker import Position
from backend.market import MarketState
from backend.ml.features import FEATURE_COLUMNS, compute_features
from backend.ml.meta import MetaModel
from backend.session import _model_samples


class CorrelationSelectionTests(unittest.TestCase):
    def test_constants_and_highly_correlated_features_are_removed(self):
        frame = pd.DataFrame({
            "primary": [1, 2, 3, 4, 5, 6],
            "duplicate": [2, 4, 6, 8, 10, 12],
            "independent": [1, 0, 2, 0, 1, 3],
            "constant": [7, 7, 7, 7, 7, 7],
        })
        selected, dropped = MetaModel.select_uncorrelated_features(frame, .9)

        self.assertEqual(selected, ["primary", "independent"])
        self.assertEqual(dropped["duplicate"], "primary")
        self.assertEqual(dropped["constant"], "constant")


class LifecycleSampleTests(unittest.TestCase):
    def test_entry_averaging_and_exit_become_separate_samples(self):
        trades = [{
            "label": 1,
            "entry_actions": [
                {"action": "entry", "features": {"action_is_average": 0}},
                {"action": "average", "features": {"action_is_average": 1}},
            ],
            "exit_features": {"action_is_exit": 1},
            "exit_label": 0,
        }]
        entries, exits = _model_samples(trades)

        self.assertEqual(len(entries), 2)
        self.assertEqual({sample["action"] for sample in entries}, {"entry", "average"})
        self.assertEqual(exits, [{"features": {"action_is_exit": 1},
                                  "label": 0, "action": "exit"}])

    def test_feature_snapshot_has_full_schema_and_position_context(self):
        state = MarketState("TUT/USDT", tick_size=.00001)
        for minute in range(80):
            price = .02 + minute * .00001
            state.candles.append({
                "time": minute * 60, "open": price, "high": price + .00002,
                "low": price - .00001, "close": price + .00001,
                "volume": 100 + minute, "delta": 10,
            })
        state.last_ts = 79 * 60_000
        state.last_price = state.candles[-1]["close"]
        state.on_book([[state.last_price - .00001, 100]],
                      [[state.last_price + .00001, 80]])
        position = Position()
        position.qty = 1000
        position.entry = .02
        position.open_ts = 60_000
        position.entry_actions = [{"action": "entry"}, {"action": "average"}]

        features = compute_features(state, "buy", position, action="exit")

        self.assertEqual(set(features), set(FEATURE_COLUMNS))
        self.assertTrue(all(math.isfinite(value) for value in features.values()))
        self.assertEqual(features["action_is_exit"], 1.0)
        self.assertEqual(features["scale_in_count"], 2.0)
        self.assertGreater(features["holding_minutes"], 0)


if __name__ == "__main__":
    unittest.main()
