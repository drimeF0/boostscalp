"""Feature engineering для отдельных entry/exit meta-моделей."""
from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Optional

from ..market import MarketState

# Порядок задаёт приоритет correlation filter: более интерпретируемая фича
# остаётся, последующие высоко-коррелированные варианты удаляются.
FEATURE_COLUMNS = [
    "side_sign", "action_is_average", "action_is_exit",
    "ret_1m", "ret_2m", "ret_3m", "ret_5m", "ret_10m", "ret_15m",
    "ret_30m", "ret_60m",
    "rsi7", "rsi14", "rsi21",
    "atr_pct_7", "atr_pct", "atr_pct_28",
    "realized_vol_5", "realized_vol_20", "realized_vol_60",
    "vol_ratio", "volume_z20", "volume_trend_5_20",
    "ema_slope", "trend_strength", "range_pos",
    "body_pct", "upper_wick_pct", "lower_wick_pct",
    "delta_ratio", "delta_ratio_5", "cvd_slope_10",
    "spread_bps", "book_imbalance_3", "book_imbalance",
    "book_imbalance_10", "book_imbalance_20",
    "vwap_dist_bps", "funding_rate", "funding_abs",
    "oi_chg_5m", "oi_chg_15m", "oi_chg_30m", "oi_chg_1h", "oi_chg_4h",
    "taker_ls_ratio",
    "position_notional_log", "position_pnl_pct", "distance_entry_bps",
    "holding_minutes", "scale_in_count",
    "hour_sin", "hour_cos", "minute_sin", "minute_cos", "dow_sin", "dow_cos",
]
MIN_FEATURE_CANDLES = 61


def _realized_vol(closes: list[float], period: int) -> float:
    values = closes[-(period + 1):]
    if len(values) < 3:
        return 0.0
    returns = [(values[i] / values[i - 1] - 1.0) for i in range(1, len(values))
               if values[i - 1] > 0]
    return statistics.pstdev(returns) * 100 if len(returns) > 1 else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_features(state: MarketState, side: str, position=None,
                     action: str = "entry") -> Optional[dict]:
    """Снимок доступных в момент действия данных без look-ahead leakage."""
    if len(state.candles) < MIN_FEATURE_CANDLES or state.last_price <= 0:
        return None
    now = (dt.datetime.utcfromtimestamp(state.last_ts / 1000)
           if state.last_ts else dt.datetime.utcnow())
    candles = list(state.candles)
    closes = [float(c["close"]) for c in candles]
    volumes = [float(c.get("volume") or 0.0) for c in candles]
    last = candles[-1]
    price = state.last_price
    vwap = state.vwap()
    d = state.deriv

    recent20 = volumes[-20:]
    vol_mean = _mean(recent20)
    vol_std = statistics.pstdev(recent20) if len(recent20) > 1 else 0.0
    recent_delta = [float(c.get("delta") or 0.0) for c in candles[-10:]]
    delta5 = sum(float(c.get("delta") or 0.0) for c in candles[-5:])
    volume5 = sum(float(c.get("volume") or 0.0) for c in candles[-5:])
    candle_range = max(float(last["high"]) - float(last["low"]), state.tick_size, 1e-12)
    body_high = max(float(last["open"]), float(last["close"]))
    body_low = min(float(last["open"]), float(last["close"]))
    atr = state.atr_pct(14)

    p_qty = float(getattr(position, "qty", 0.0) or 0.0)
    p_entry = float(getattr(position, "entry", 0.0) or 0.0)
    p_open_ts = int(getattr(position, "open_ts", 0) or 0)
    entry_actions = getattr(position, "entry_actions", []) or []
    position_pnl_pct = ((price - p_entry) / p_entry * (1 if p_qty > 0 else -1) * 100
                        if p_qty and p_entry > 0 else 0.0)

    f = {
        "side_sign": 1 if side == "buy" else -1,
        "action_is_average": 1 if action == "average" else 0,
        "action_is_exit": 1 if action == "exit" else 0,
        "ret_1m": state.ret_n(1), "ret_2m": state.ret_n(2),
        "ret_3m": state.ret_n(3), "ret_5m": state.ret_n(5),
        "ret_10m": state.ret_n(10), "ret_15m": state.ret_n(15),
        "ret_30m": state.ret_n(30), "ret_60m": state.ret_n(60),
        "rsi7": state.rsi(7), "rsi14": state.rsi(14), "rsi21": state.rsi(21),
        "atr_pct_7": state.atr_pct(7), "atr_pct": atr, "atr_pct_28": state.atr_pct(28),
        "realized_vol_5": _realized_vol(closes, 5),
        "realized_vol_20": _realized_vol(closes, 20),
        "realized_vol_60": _realized_vol(closes, 60),
        "vol_ratio": state.vol_ratio(20),
        "volume_z20": ((volumes[-1] - vol_mean) / vol_std if vol_std > 0 else 0.0),
        "volume_trend_5_20": (_mean(volumes[-5:]) / vol_mean - 1 if vol_mean > 0 else 0.0),
        "ema_slope": state.ema_slope(20, 50),
        "trend_strength": abs(state.ema_slope(20, 50)) / atr if atr > 0 else 0.0,
        "range_pos": state.pos_in_range(20),
        "body_pct": abs(float(last["close"]) - float(last["open"])) / candle_range,
        "upper_wick_pct": (float(last["high"]) - body_high) / candle_range,
        "lower_wick_pct": (body_low - float(last["low"])) / candle_range,
        "delta_ratio": float(last.get("delta") or 0.0) / max(float(last.get("volume") or 0.0), 1e-12),
        "delta_ratio_5": delta5 / max(volume5, 1e-12),
        "cvd_slope_10": sum(recent_delta) / max(sum(volumes[-10:]), 1e-12),
        "spread_bps": state.spread_bps(),
        "book_imbalance_3": state.book_imbalance(3),
        "book_imbalance": state.book_imbalance(5),
        "book_imbalance_10": state.book_imbalance(10),
        "book_imbalance_20": state.book_imbalance(20),
        "vwap_dist_bps": (price - vwap) / vwap * 1e4 if vwap else 0.0,
        "funding_rate": d.funding_rate, "funding_abs": abs(d.funding_rate),
        "oi_chg_5m": d.oi_chg(5, state.last_ts),
        "oi_chg_15m": d.oi_chg(15, state.last_ts),
        "oi_chg_30m": d.oi_chg(30, state.last_ts),
        "oi_chg_1h": d.oi_chg(60, state.last_ts),
        "oi_chg_4h": d.oi_chg(240, state.last_ts),
        "taker_ls_ratio": d.taker_ls_ratio,
        "position_notional_log": math.log1p(abs(p_qty) * price),
        "position_pnl_pct": position_pnl_pct,
        "distance_entry_bps": ((price - p_entry) / p_entry * 1e4 if p_entry > 0 else 0.0),
        "holding_minutes": max(0.0, (state.last_ts - p_open_ts) / 60_000) if p_open_ts else 0.0,
        "scale_in_count": len(entry_actions),
        "hour_sin": math.sin(2 * math.pi * now.hour / 24),
        "hour_cos": math.cos(2 * math.pi * now.hour / 24),
        "minute_sin": math.sin(2 * math.pi * now.minute / 60),
        "minute_cos": math.cos(2 * math.pi * now.minute / 60),
        "dow_sin": math.sin(2 * math.pi * now.weekday() / 7),
        "dow_cos": math.cos(2 * math.pi * now.weekday() / 7),
    }
    return {key: (float(value) if math.isfinite(float(value)) else 0.0)
            for key, value in f.items()}
