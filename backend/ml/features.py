"""Извлечение признаков для мета-лейблинга (только данные, доступные на входе!)."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from ..market import MarketState

# Порядок колонок фиксирован — используется и при обучении, и при инференсе.
FEATURE_COLUMNS = [
    "side_sign",        # 1 для buy, -1 для sell
    "ret_1m", "ret_5m", "ret_15m", "ret_60m",
    "rsi14",
    "atr_pct",
    "vol_ratio",
    "ema_slope",
    "range_pos",        # позиция цены в диапазоне 20 свечей
    "spread_bps",
    "book_imbalance",
    "vwap_dist_bps",    # расстояние до VWAP в б.п.
    "hour", "minute", "dow",
]


def compute_features(state: MarketState, side: str) -> Optional[dict]:
    if len(state.candles) < 30 or state.last_price <= 0:
        return None
    t = dt.datetime.utcfromtimestamp(state.last_ts / 1000) if state.last_ts else dt.datetime.utcnow()
    vwap = state.vwap()
    f = {
        "side_sign": 1 if side == "buy" else -1,
        "ret_1m": state.ret_n(1),
        "ret_5m": state.ret_n(5),
        "ret_15m": state.ret_n(15),
        "ret_60m": state.ret_n(60),
        "rsi14": state.rsi(14),
        "atr_pct": state.atr_pct(14),
        "vol_ratio": state.vol_ratio(20),
        "ema_slope": state.ema_slope(20, 50),
        "range_pos": state.pos_in_range(20),
        "spread_bps": state.spread_bps(),
        "book_imbalance": state.book_imbalance(5),
        "vwap_dist_bps": (state.last_price - vwap) / vwap * 1e4 if vwap else 0.0,
        "hour": t.hour,
        "minute": t.minute,
        "dow": t.weekday(),
    }
    return f
