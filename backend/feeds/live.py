"""Лайв-фид: реальные данные с биржи через ccxt.pro (WebSocket)."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

log = logging.getLogger("live")

SUPPORTED = ["binance", "bybit", "okx", "kucoin"]


class LiveFeed:
    def __init__(self, exchange_id: str, symbol: str):
        self.exchange_id = exchange_id
        self.symbol = symbol
        self._stop = False
        self.exchange = None
        self.tick_size: float | None = None

    async def _open(self):
        import ccxt.pro as ccxtpro  # ленивый импорт
        cls = getattr(ccxtpro, self.exchange_id)
        self.exchange = cls({"enableRateLimit": True, "newUpdates": True})
        await self.exchange.load_markets()
        market = self.exchange.market(self.symbol)
        prec = (market.get("precision") or {}).get("price")
        if prec and prec < 1:
            self.tick_size = float(prec)
        limits = (market.get("limits") or {}).get("price") or {}
        log.info("live feed %s %s tick=%s", self.exchange_id, self.symbol, self.tick_size)

    async def stop(self):
        self._stop = True
        if self.exchange is not None:
            try:
                await self.exchange.close()
            except Exception:
                pass

    async def run(self) -> AsyncGenerator[dict, None]:
        """Мультиплексирует сделки и стакан в один поток событий."""
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

        try:
            await self._open()
        except Exception as e:
            yield {"type": "error", "text": f"Не удалось подключиться к {self.exchange_id}: {e}"}
            return

        tasks = [asyncio.create_task(trades_loop()), asyncio.create_task(book_loop())]
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
