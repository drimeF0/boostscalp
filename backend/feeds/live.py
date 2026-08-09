"""Лайв-фид: реальные данные USDT-M фьючерсов через ccxt.pro (WebSocket + REST-поллинг OI/фандинга)."""
from __future__ import annotations

import asyncio
import logging
import math
from typing import AsyncGenerator

log = logging.getLogger("live")

SUPPORTED = ["binance", "bybit", "okx", "kucoin"]

# биржа -> (класс ccxt.pro, опции) для USDT-M перпов
EXCHANGE_MAP = {
    "binance": ("binanceusdm", {}),
    "bybit": ("bybit", {"options": {"defaultType": "swap"}}),
    "okx": ("okx", {"options": {"defaultType": "swap"}}),
    "kucoin": ("kucoinfutures", {}),
}

OI_POLL_SEC = 15
FUNDING_POLL_SEC = 60
BOOK_LEVELS = 25
DEFAULT_HISTORY_LIMIT = 500
MAX_HISTORY_LIMIT = 3000
FEATURE_HISTORY_BARS = 80
TIMEFRAME_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
                     "30m": 1800, "1h": 3600, "4h": 14400}


def to_swap_symbol(symbol: str) -> str:
    """BTC/USDT -> BTC/USDT:USDT (линейный перп в нотации ccxt)."""
    return symbol if ":" in symbol else symbol + ":USDT"


class LiveFeed:
    def __init__(self, exchange_id: str, symbol: str,
                 history_limit: int = DEFAULT_HISTORY_LIMIT, timeframe: str = "1m"):
        self.exchange_id = exchange_id
        self.symbol = to_swap_symbol(symbol)
        try:
            history_limit = int(history_limit)
        except (TypeError, ValueError):
            history_limit = DEFAULT_HISTORY_LIMIT
        self.history_limit = (0 if history_limit <= 0 else
                              max(FEATURE_HISTORY_BARS, min(history_limit, MAX_HISTORY_LIMIT)))
        self.timeframe = timeframe if timeframe in TIMEFRAME_SECONDS else "1m"
        self._stop = False
        self.exchange = None
        self.tick_size: float | None = None
        self.market_id: str | None = None

    async def _open(self):
        import ccxt.pro as ccxtpro  # ленивый импорт
        cls_name, opts = EXCHANGE_MAP.get(self.exchange_id, (self.exchange_id, {}))
        cls = getattr(ccxtpro, cls_name)
        cfg = {"enableRateLimit": True, "newUpdates": True, **opts}
        self.exchange = cls(cfg)
        await self.exchange.load_markets()
        market = self.exchange.market(self.symbol)
        self.market_id = market.get("id")
        prec = (market.get("precision") or {}).get("price")
        if prec and prec < 1:
            self.tick_size = float(prec)
        log.info("live feed %s %s tick=%s", self.exchange_id, self.symbol, self.tick_size)

    async def _fetch_indicator_history(self) -> dict:
        """Короткий прогрев Delta/OI: достаточно для 60-bar фич, без загрузки всего графика."""
        result = {"delta": [], "oi": []}
        if not self.history_limit:
            return result
        interval_ms = TIMEFRAME_SECONDS[self.timeframe] * 1000
        now = self.exchange.milliseconds()

        # Binance возвращает taker-buy volume в raw klines (поле 9), которое
        # ccxt OHLCV намеренно отбрасывает. Delta = buy - sell = 2*buy - total.
        raw_method = getattr(self.exchange, "fapiPublicGetKlines", None)
        if self.exchange_id == "binance" and raw_method and self.market_id:
            try:
                rows = await raw_method({"symbol": self.market_id, "interval": self.timeframe,
                                         "limit": FEATURE_HISTORY_BARS + 1})
                current_bucket = now // interval_ms * interval_ms
                for row in rows or []:
                    if len(row) < 10 or int(row[0]) >= current_bucket:
                        continue
                    total = _non_negative_float(row[5])
                    taker_buy = _non_negative_float(row[9])
                    if total is not None and taker_buy is not None:
                        result["delta"].append({"time": int(row[0]) // 1000,
                                                "value": 2 * taker_buy - total})
                result["delta"] = result["delta"][-FEATURE_HISTORY_BARS:]
            except Exception as e:
                log.info("delta history unavailable: %s", e)

        oi_method = getattr(self.exchange, "fetch_open_interest_history", None)
        if oi_method:
            try:
                # Большинство derivatives API начинает OI history с 5m.
                oi_timeframe = "5m" if TIMEFRAME_SECONDS[self.timeframe] < 300 else self.timeframe
                oi_interval_ms = TIMEFRAME_SECONDS[oi_timeframe]
                since = now - FEATURE_HISTORY_BARS * oi_interval_ms * 1000
                rows = await oi_method(self.symbol, oi_timeframe, since, FEATURE_HISTORY_BARS)
                for row in rows or []:
                    ts = row.get("timestamp")
                    value = _positive_float(row.get("openInterestAmount") or
                                            row.get("openInterestValue"))
                    if isinstance(ts, (int, float)) and value is not None:
                        result["oi"].append({"ts": int(ts), "value": value})
                result["oi"].sort(key=lambda point: point["ts"])
                result["oi"] = result["oi"][-FEATURE_HISTORY_BARS:]
            except Exception as e:
                log.info("OI history unavailable: %s", e)
        return result

    async def stop(self):
        self._stop = True
        if self.exchange is not None:
            try:
                await self.exchange.close()
            except Exception:
                pass

    async def run(self) -> AsyncGenerator[dict, None]:
        """Мультиплексирует сделки, стакан, OI и фандинг в один поток событий."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)

        async def trades_loop():
            while not self._stop:
                try:
                    trades = await self.exchange.watch_trades(self.symbol)
                    for t in trades:
                        price = _positive_float(t.get("price"))
                        qty = _non_negative_float(t.get("amount"))
                        ts = t.get("timestamp") or self.exchange.milliseconds()
                        if price is None or qty is None or not isinstance(ts, (int, float)):
                            # Binance изредка присылает служебные trade-события
                            # p=0/q=0. Это не ошибка потока и не должно пугать
                            # пользователя в production-логе.
                            log.debug("ignored empty/malformed trade: %r", t)
                            continue
                        _put(queue, {
                            "type": "tick",
                            "ts": int(ts),
                            "price": price,
                            "qty": qty,
                            "side": t.get("side") if t.get("side") in ("buy", "sell") else None,
                        })
                except Exception as e:
                    if not self._stop:
                        _put(queue, {"type": "error", "text": f"trades: {e}"})
                        await asyncio.sleep(3)

        async def book_loop():
            while not self._stop:
                try:
                    # Не передаём 25 в API: у Binance допустимы только
                    # 5/10/20/50/100/500/1000. ccxt выберет поддерживаемую
                    # глубину, а до нужного размера обрежем уже локально.
                    ob = await self.exchange.watch_order_book(self.symbol)
                    bids = _clean_book_side(ob.get("bids"), BOOK_LEVELS, reverse=True)
                    asks = _clean_book_side(ob.get("asks"), BOOK_LEVELS, reverse=False)
                    if bids and asks:
                        _put(queue, {"type": "book", "bids": bids, "asks": asks})
                except Exception as e:
                    if not self._stop:
                        _put(queue, {"type": "error", "text": f"orderbook: {e}"})
                        await asyncio.sleep(3)

        async def oi_loop():
            """Открытый интерес — REST-поллинг (меняется медленно)."""
            while not self._stop:
                try:
                    r = await self.exchange.fetch_open_interest(self.symbol)
                    oi = r.get("openInterestAmount") or r.get("openInterestValue") or 0
                    ts = r.get("timestamp") or self.exchange.milliseconds()
                    if oi:
                        _put(queue, {"type": "metrics", "ts": int(ts), "oi": float(oi), "taker": 0.0})
                except Exception as e:
                    log.warning("OI poll: %s", e)
                await asyncio.sleep(OI_POLL_SEC)

        async def funding_loop():
            while not self._stop:
                try:
                    r = await self.exchange.fetch_funding_rate(self.symbol)
                    rate = r.get("fundingRate")
                    ts = r.get("fundingTimestamp") or r.get("timestamp") or self.exchange.milliseconds()
                    if rate is not None:
                        _put(queue, {"type": "funding", "ts": int(ts), "rate": float(rate)})
                except Exception as e:
                    log.warning("funding poll: %s", e)
                await asyncio.sleep(FUNDING_POLL_SEC)

        try:
            await self._open()
        except Exception as e:
            await self.stop()
            yield {"type": "error", "text": f"Не удалось подключиться к {self.exchange_id}: {e}"}
            return

        # Даём графику контекст сразу после подключения. Текущую незакрытую
        # минуту не включаем: она будет собрана из live-сделок.
        try:
            rows = []
            if self.history_limit:
                # paginate=True позволяет ccxt разбить запрос, когда значение
                # больше лимита одной REST-страницы конкретной биржи.
                rows = await self.exchange.fetch_ohlcv(
                    # +1 компенсирует текущую незакрытую свечу, которую ниже
                    # намеренно отбрасываем.
                    self.symbol, self.timeframe, limit=self.history_limit + 1,
                    params={"paginate": True},
                )
            interval_ms = TIMEFRAME_SECONDS[self.timeframe] * 1000
            current_minute_ms = self.exchange.milliseconds() // interval_ms * interval_ms
            candles_by_time = {}
            for row in rows or []:
                candle = _clean_ohlcv(row, current_minute_ms)
                if candle is not None:
                    candles_by_time[candle["time"]] = candle
            candles = [candles_by_time[ts] for ts in sorted(candles_by_time)]
            candles = candles[-self.history_limit:]
            indicators = await self._fetch_indicator_history()
            delta_by_time = {point["time"]: point["value"] for point in indicators["delta"]}
            for candle in candles:
                candle["delta"] = delta_by_time.get(candle["time"], 0.0)
            if candles:
                yield {"type": "warmup", "candles": candles}
                if indicators["oi"]:
                    yield {"type": "indicator_history", **indicators}
                yield {"type": "status", "text": f"Загружено свечей истории: {len(candles)}"}
        except Exception as e:
            log.warning("live OHLCV warmup: %s", e)

        tasks = [asyncio.create_task(trades_loop()), asyncio.create_task(book_loop()),
                 asyncio.create_task(oi_loop()), asyncio.create_task(funding_loop())]
        try:
            while not self._stop:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield ev
                except asyncio.TimeoutError:
                    continue
        finally:
            for t in tasks:
                t.cancel()
            await self.stop()


def _put(q: asyncio.Queue, item: dict):
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass


def _positive_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _non_negative_float(value) -> float | None:
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _clean_book_side(levels, limit: int, reverse: bool) -> list[list[float]]:
    clean = []
    for level in levels or []:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price = _positive_float(level[0])
        qty = _positive_float(level[1])
        if price is not None and qty is not None:
            clean.append([price, qty])
    clean.sort(key=lambda level: level[0], reverse=reverse)
    return clean[:limit]


def _clean_ohlcv(row, current_minute_ms: int) -> dict | None:
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return None
    try:
        ts = int(row[0])
        values = [float(value) for value in row[1:6]]
    except (TypeError, ValueError):
        return None
    open_, high, low, close, volume = values
    if (ts >= current_minute_ms or any(not math.isfinite(v) for v in values)
            or min(open_, high, low, close) <= 0 or volume < 0
            or high < max(open_, low, close) or low > min(open_, high, close)):
        return None
    return {
        "time": ts // 1000,
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume,
    }
