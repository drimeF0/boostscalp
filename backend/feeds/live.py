"""Лайв-фид: реальные данные USDT-M фьючерсов через ccxt.pro (WebSocket + REST-поллинг OI/фандинга)."""
from __future__ import annotations

import asyncio
import logging
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


def to_swap_symbol(symbol: str) -> str:
    """BTC/USDT -> BTC/USDT:USDT (линейный перп в нотации ccxt)."""
    return symbol if ":" in symbol else symbol + ":USDT"


class LiveFeed:
    def __init__(self, exchange_id: str, symbol: str):
        self.exchange_id = exchange_id
        self.symbol = to_swap_symbol(symbol)
        self._stop = False
        self.exchange = None
        self.tick_size: float | None = None

    async def _open(self):
        import ccxt.pro as ccxtpro  # ленивый импорт
        cls_name, opts = EXCHANGE_MAP.get(self.exchange_id, (self.exchange_id, {}))
        cls = getattr(ccxtpro, cls_name)
        cfg = {"enableRateLimit": True, "newUpdates": True, **opts}
        self.exchange = cls(cfg)
        await self.exchange.load_markets()
        market = self.exchange.market(self.symbol)
        prec = (market.get("precision") or {}).get("price")
        if prec and prec < 1:
            self.tick_size = float(prec)
        log.info("live feed %s %s tick=%s", self.exchange_id, self.symbol, self.tick_size)

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
                        _put(queue, {
                            "type": "tick",
                            "ts": t.get("timestamp") or self.exchange.milliseconds(),
                            "price": float(t["price"]),
                            "qty": float(t.get("amount") or 0.0),
                        })
                except Exception as e:
                    if not self._stop:
                        _put(queue, {"type": "error", "text": f"trades: {e}"})
                        await asyncio.sleep(3)

        async def book_loop():
            while not self._stop:
                try:
                    ob = await self.exchange.watch_order_book(self.symbol, 25)
                    bids = [[float(p), float(q)] for p, q in (ob.get("bids") or [])[:25]]
                    asks = [[float(p), float(q)] for p, q in (ob.get("asks") or [])[:25]]
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
            yield {"type": "error", "text": f"Не удалось подключиться к {self.exchange_id}: {e}"}
            return

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
