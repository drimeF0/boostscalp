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
    "book_imbalance",   # перевес стакана, топ-5
    "book_imbalance_10",  # перевес стакана, топ-10
    "vwap_dist_bps",    # расстояние до VWAP в б.п.
    "funding_rate",     # текущая ставка фандинга (0.0001 = 0.01%)
    "oi_chg_15m",       # относительное изменение открытого интереса за 15 мин
    "oi_chg_1h",        # ... за 1 час
    "taker_ls_ratio",   # taker buy/sell volume ratio (из futures metrics)
    "hour", "minute", "dow",
]


def compute_features(state: MarketState, side: str) -> Optional[dict]:
    if len(state.candles) < 30 or state.last_price <= 0:
        return None
    t = dt.datetime.utcfromtimestamp(state.last_ts / 1000) if state.last_ts else dt.datetime.utcnow()
    vwap = state.vwap()
    d = state.deriv
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
        "book_imbalance_10": state.book_imbalance(10),
        "vwap_dist_bps": (state.last_price - vwap) / vwap * 1e4 if vwap else 0.0,
        "funding_rate": d.funding_rate,
        "oi_chg_15m": d.oi_chg(15, state.last_ts),
        "oi_chg_1h": d.oi_chg(60, state.last_ts),
        "taker_ls_ratio": d.taker_ls_ratio,
        "hour": t.hour,
        "minute": t.minute,
        "dow": t.weekday(),
    }
    return f
