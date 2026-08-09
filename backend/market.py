"""Рыночное состояние: тики -> свечи, стакан, индикаторы."""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional

MAX_CANDLES = 3000


def _new_candle(ts_min: int, price: float, qty: float) -> dict:
    return {
        "time": ts_min,  # unix seconds, начало минуты
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": qty,
    }


class CandleBuilder:
    """Собирает 1-минутные свечи из тиков."""

    def __init__(self, on_candle_close: Optional[Callable[[dict], None]] = None):
        self.current: Optional[dict] = None
        self.on_candle_close = on_candle_close

    def update(self, price: float, qty: float, ts_ms: int) -> Optional[dict]:
        """Возвращает закрытую свечу (если минута сменилась)."""
        if (not math.isfinite(price) or price <= 0 or not math.isfinite(qty)
                or qty < 0 or not isinstance(ts_ms, (int, float)) or ts_ms <= 0):
            return None
        ts_min = int(ts_ms // 60000) * 60
        closed = None
        if self.current is None or ts_min > self.current["time"]:
            if self.current is not None:
                closed = self.current
                if self.on_candle_close:
                    self.on_candle_close(closed)
            self.current = _new_candle(ts_min, price, qty)
        elif ts_min == self.current["time"]:
            c = self.current
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price
            c["volume"] += qty
        # Запоздалые сделки из уже закрытой минуты игнорируем. Иначе они
        # повреждают OHLC текущей свечи.
        return closed


class DerivativesState:
    """Фьючерсные данные: фандинг и история открытого интереса."""

    OI_MAXLEN = 2000  # ~7 дней 5-минутных точек

    def __init__(self):
        self.funding_rate: float = 0.0
        self.funding_ts: int = 0
        self.oi: Deque = deque(maxlen=self.OI_MAXLEN)  # (ts_ms, oi)
        self.taker_ls_ratio: float = 0.0  # taker buy/sell vol ratio (из metrics)

    def reset(self):
        self.funding_rate = 0.0
        self.funding_ts = 0
        self.oi.clear()
        self.taker_ls_ratio = 0.0

    def on_funding(self, ts_ms: int, rate: float):
        self.funding_rate = rate
        self.funding_ts = ts_ms

    def on_oi(self, ts_ms: int, oi: float, taker_ratio: float = 0.0):
        if self.oi and ts_ms < self.oi[-1][0]:
            return
        self.oi.append((ts_ms, oi))
        if taker_ratio:
            self.taker_ls_ratio = taker_ratio

    def oi_now(self) -> float:
        return self.oi[-1][1] if self.oi else 0.0

    def oi_chg(self, minutes: int, ref_ts: int) -> float:
        """Относительное изменение OI за последние `minutes` минут."""
        if len(self.oi) < 2 or ref_ts <= 0:
            return 0.0
        target = ref_ts - minutes * 60_000
        past = None
        for ts, v in self.oi:
            if ts <= target:
                past = v
            else:
                break
        if past is None or past <= 0:
            # данных раньше target нет — берём самую старую точку
            past = self.oi[0][1]
            if past <= 0 or ref_ts - self.oi[0][0] < minutes * 30_000:
                return 0.0
        now = self.oi[-1][1]
        return (now - past) / past


class MarketState:
    """Хранит последние цены, свечи, стакан; считает индикаторы."""

    def __init__(self, symbol: str = "", tick_size: float = 0.01):
        self.symbol = symbol
        self.tick_size = tick_size
        self.last_price: float = 0.0
        self.last_ts: int = 0  # ms
        self.candles: Deque[dict] = deque(maxlen=MAX_CANDLES)
        self.builder = CandleBuilder(on_candle_close=self._on_candle)
        self.bids: List[List[float]] = []
        self.asks: List[List[float]] = []
        self.deriv = DerivativesState()
        # сессионный VWAP
        self._vwap_day: Optional[int] = None
        self._pv: float = 0.0
        self._vv: float = 0.0

    # -------------------- обновление --------------------

    def _on_candle(self, candle: dict) -> None:
        self.candles.append(candle)

    def reset(self, symbol: str, tick_size: Optional[float] = None):
        self.symbol = symbol
        if tick_size:
            self.tick_size = tick_size
        self.last_price = 0.0
        self.last_ts = 0
        self.candles.clear()
        self.builder = CandleBuilder(on_candle_close=self._on_candle)
        self.bids = []
        self.asks = []
        self.deriv.reset()
        self._vwap_day = None
        self._pv = 0.0
        self._vv = 0.0

    def on_tick(self, price: float, qty: float, ts_ms: int) -> Optional[dict]:
        if (not math.isfinite(price) or price <= 0 or not math.isfinite(qty)
                or qty < 0 or not isinstance(ts_ms, (int, float)) or ts_ms <= 0):
            return None
        if self.last_ts and ts_ms < self.last_ts:
            return None
        self.last_price = price
        self.last_ts = ts_ms
        # VWAP по дню
        day = int(ts_ms // 86_400_000)
        if self._vwap_day != day:
            self._vwap_day = day
            self._pv = 0.0
            self._vv = 0.0
        if qty > 0:
            self._pv += price * qty
            self._vv += qty
        return self.builder.update(price, qty, ts_ms)

    def on_candle_closed_external(self, candle: dict):
        """Для бэктеста: свеча пришла из файла (не из тиков)."""
        self.candles.append(candle)
        self.builder.current = None
        self.last_price = candle["close"]
        self.last_ts = candle["time"] * 1000 + 59_999

    def on_book(self, bids: List[List[float]], asks: List[List[float]]):
        self.bids = bids
        self.asks = asks
        if bids and asks:
            self.last_price = (bids[0][0] + asks[0][0]) / 2

    # -------------------- рынок --------------------

    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else self.last_price

    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else self.last_price

    def spread_bps(self) -> float:
        if self.bids and self.asks and self.last_price > 0:
            return (self.asks[0][0] - self.bids[0][0]) / self.last_price * 1e4
        return 0.0

    def book_imbalance(self, depth: int = 5) -> float:
        """(bidVol - askVol) / (bidVol + askVol) по топ-N уровням."""
        bv = sum(q for _, q in self.bids[:depth])
        av = sum(q for _, q in self.asks[:depth])
        if bv + av <= 0:
            return 0.0
        return (bv - av) / (bv + av)

    def vwap(self) -> float:
        return self._pv / self._vv if self._vv > 0 else self.last_price

    # -------------------- индикаторы --------------------

    def closes(self, n: Optional[int] = None) -> List[float]:
        cs = [c["close"] for c in self.candles]
        return cs[-n:] if n else cs

    def ret_n(self, n: int) -> float:
        """Доходность за последние n закрытых свечей, в долях."""
        cs = self.closes(n + 1)
        if len(cs) < n + 1 or cs[0] == 0:
            return 0.0
        return (cs[-1] - cs[0]) / cs[0]

    def rsi(self, period: int = 14) -> float:
        cs = self.closes(period + 1)
        if len(cs) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(1, len(cs)):
            d = cs[i] - cs[i - 1]
            if d >= 0:
                gains += d
            else:
                losses -= d
        if losses == 0:
            return 100.0
        rs = gains / losses
        return 100.0 - 100.0 / (1.0 + rs)

    def atr_pct(self, period: int = 14) -> float:
        """ATR в процентах от цены."""
        cds = list(self.candles)[-(period + 1):]
        if len(cds) < 2:
            return 0.0
        trs = []
        for i in range(1, len(cds)):
            h, l, pc = cds[i]["high"], cds[i]["low"], cds[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = sum(trs) / len(trs)
        return atr / self.last_price * 100 if self.last_price > 0 else 0.0

    def vol_ratio(self, period: int = 20) -> float:
        cds = list(self.candles)[-(period + 1):]
        if len(cds) < period + 1:
            return 1.0
        avg = sum(c["volume"] for c in cds[:-1]) / period
        if avg <= 0:
            return 1.0
        return cds[-1]["volume"] / avg

    def ema_slope(self, fast: int = 20, slow: int = 50) -> float:
        """Разница EMA fast/slow в процентах от цены."""
        cs = self.closes(slow + 10)
        if len(cs) < slow:
            return 0.0

        def ema(vals, n):
            k = 2 / (n + 1)
            e = vals[0]
            for v in vals[1:]:
                e = v * k + e * (1 - k)
            return e

        ef, es = ema(cs, fast), ema(cs, slow)
        if self.last_price <= 0:
            return 0.0
        return (ef - es) / self.last_price * 100

    def pos_in_range(self, n: int = 20) -> float:
        """Где цена в диапазоне high/low последних n свечей: 0..1."""
        cds = list(self.candles)[-n:]
        if not cds:
            return 0.5
        hi = max(c["high"] for c in cds)
        lo = min(c["low"] for c in cds)
        if hi <= lo:
            return 0.5
        return (self.last_price - lo) / (hi - lo)

    def book_snapshot(self, levels: int = 25) -> dict:
        return {
            "bids": self.bids[:levels],
            "asks": self.asks[:levels],
            "tickSize": self.tick_size,
        }

    def candle_history(self, n: int = 500) -> List[dict]:
        return list(self.candles)[-n:]
