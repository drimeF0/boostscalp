"""Генератор правдоподобного стакана для бэктеста (исторических стаканов нет)."""
from __future__ import annotations

import random
from typing import List, Tuple

from .config import BOOK_LEVELS


def gen_book(price: float, tick: float, levels: int = BOOK_LEVELS,
             rng: random.Random | None = None) -> Tuple[List[List[float]], List[List[float]]]:
    rng = rng or random
    if price <= 0:
        return [], []
    spread = max(tick * 2, price * 0.0002)  # ~2 б.п.
    half = spread / 2
    bids, asks = [], []
    base_qty = max(price, 1.0)
    for i in range(1, levels + 1):
        step = tick * (i - 1) + tick * rng.random() * 0.5
        # объёмы затухают с глубиной + шум
        bq = round((base_qty / price) * (levels / (i + 4)) * (0.5 + rng.random()), 5)
        aq = round((base_qty / price) * (levels / (i + 4)) * (0.5 + rng.random()), 5)
        bids.append([round(price - half - step, 8), bq])
        asks.append([round(price + half + step, 8), aq])
    return bids, asks
