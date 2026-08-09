"""Сессия клиента: связывает фид данных, брокера и мета-модель, гоняет WS-протокол."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
from typing import Optional

from .broker import PaperBroker
from .config import (DEFAULT_MAKER_FEE, DEFAULT_START_BALANCE,
                     DEFAULT_TAKER_FEE, DEFAULT_TICK_SIZE, DEFAULT_TICK_SIZES,
                     DEFAULT_THRESHOLD)
from .db import count_trades, fetch_trades, insert_trade
from .feeds.backtest import BacktestFeed
from .feeds.live import LiveFeed
from .market import MAX_CANDLES, MarketState
from .ml.features import compute_features
from .ml.meta import MetaModel

log = logging.getLogger("session")


class Session:
    def __init__(self, send_queue: asyncio.Queue):
        self.q = send_queue
        self.state = MarketState()
        self.broker = PaperBroker(self.state, DEFAULT_START_BALANCE,
                                  DEFAULT_TAKER_FEE, DEFAULT_MAKER_FEE)
        self.broker.subscribe(self._on_broker_event)
        self.model = MetaModel()
        self.meta_enabled = False
        self.meta_mode = "filter"          # "filter" | "advisory"
        self.meta_threshold = DEFAULT_THRESHOLD
        self.mode = None                   # "live" | "backtest"
        self.symbol = "BTC/USDT"
        self.feed = None
        self.feed_task: Optional[asyncio.Task] = None
        self.bt_status = {"running": False, "paused": False, "pct": 0.0,
                          "speed": 5.0, "ts": None}

    # ==================== отправка ====================

    def send(self, msg: dict):
        try:
            self.q.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    # ==================== брокер -> клиент ====================

    def _on_broker_event(self, kind: str, payload: dict):
        if kind == "trade_closed":
            insert_trade(payload, mode=self.mode or "")
            self.send({"type": "trade_closed", "trade": _public_trade(payload)})
            self.send({"type": "trades_count", "count": count_trades()})
        else:
            self.send({"type": kind, "data": payload})

    # ==================== входящие сообщения ====================

    async def handle(self, msg: dict):
        t = msg.get("type")
        try:
            if t == "start_live":
                await self.start_live(msg.get("exchange", "binance"),
                                      msg.get("symbol", "BTC/USDT"),
                                      msg.get("historyLimit", 500))
            elif t == "start_backtest":
                await self.start_backtest(msg)
            elif t == "bt_control":
                await self.bt_control(msg)
            elif t == "order":
                self.place_order(msg)
            elif t == "cancel":
                self.broker.cancel(int(msg.get("orderId", 0)))
            elif t == "cancel_all":
                self.broker.cancel_all()
            elif t == "close_position":
                self.broker.close_position(self.state.last_ts or _now_ms())
            elif t == "set_sl_tp":
                r = self.broker.set_sl_tp(float(msg.get("price", 0)))
                if r is None:
                    self.notify("warn", "Нет открытой позиции — стоп/тейк не установлен")
                else:
                    self.notify("ok", f"{'Стоп' if r['kind']=='sl' else 'Тейк'} @ {r['price']:g}")
            elif t == "cancel_sl_tp":
                self.broker.cancel_sl_tp()
                self.notify("info", "Стоп/тейк отменены")
            elif t == "train_model":
                await self.train_model()
            elif t == "model_settings":
                self.meta_enabled = bool(msg.get("enabled", self.meta_enabled))
                self.meta_mode = msg.get("mode", self.meta_mode)
                if self.meta_mode not in ("filter", "advisory"):
                    self.meta_mode = "filter"
                self.meta_threshold = float(msg.get("threshold", self.meta_threshold))
                self.send_model_status()
            elif t == "apply_settings":
                self.apply_settings(msg)
            elif t == "get_trades":
                self.send({"type": "trades_list",
                           "trades": [_db_trade_public(x) for x in fetch_trades(300)]})
        except Exception as e:
            log.exception("handle error")
            self.notify("error", str(e))

    # ==================== режимы ====================

    async def _stop_feed(self):
        if self.feed is not None:
            try:
                if isinstance(self.feed, LiveFeed):
                    await self.feed.stop()
                else:
                    self.feed.stop()
            except Exception:
                pass
        if self.feed_task is not None:
            self.feed_task.cancel()
            try:
                await self.feed_task
            except (asyncio.CancelledError, Exception):
                pass
        self.feed = None
        self.feed_task = None

    async def _switch(self, symbol: str):
        """Остановить текущий фид, закрыть позицию/ордера, сбросить состояние."""
        await self._stop_feed()
        if self.broker.position.qty != 0:
            self.broker.close_position(self.state.last_ts or _now_ms())
        self.broker.cancel_all()
        self.symbol = symbol
        tick = DEFAULT_TICK_SIZES.get(symbol, DEFAULT_TICK_SIZE)
        self.state.reset(symbol, tick)
        self.bt_status.update({"running": False, "paused": False, "pct": 0.0})

    async def start_live(self, exchange: str, symbol: str, history_limit: int = 500):
        await self._switch(symbol)
        self.mode = "live"
        self.feed = LiveFeed(exchange, symbol, history_limit=history_limit)
        self.feed_task = asyncio.create_task(self._feed_loop())
        self.send({"type": "mode", "mode": "live", "exchange": exchange, "symbol": symbol,
                   "historyLimit": self.feed.history_limit})
        self.notify("info", f"Live: {exchange} {symbol} — подключение...")

    async def start_backtest(self, msg: dict):
        symbol = msg.get("symbol", "BTC/USDT")
        await self._switch(symbol)
        self.mode = "backtest"
        try:
            start = dt.date.fromisoformat(msg.get("start"))
            end = dt.date.fromisoformat(msg.get("end"))
        except Exception:
            self.notify("error", "Некорректные даты бэктеста")
            return
        if msg.get("tickSize"):
            self.state.tick_size = float(msg["tickSize"])
        speed = float(msg.get("speed", 5.0))
        data_type = msg.get("dataType", "klines")
        self.feed = BacktestFeed(symbol, start, end, speed=speed,
                                 data_type=data_type,
                                 tick_size=self.state.tick_size)
        self.bt_status.update({"running": True, "paused": False, "pct": 0.0, "speed": speed})
        self.feed_task = asyncio.create_task(self._feed_loop())
        self.send({"type": "mode", "mode": "backtest", "symbol": symbol,
                   "start": str(start), "end": str(end)})
        self.send_bt_status()

    async def bt_control(self, msg: dict):
        action = msg.get("action")
        if not isinstance(self.feed, BacktestFeed):
            return
        if action == "pause":
            self.feed.paused.clear()
            self.bt_status["paused"] = True
        elif action == "resume":
            self.feed.paused.set()
            self.bt_status["paused"] = False
        elif action == "stop":
            await self._stop_feed()
            self.bt_status["running"] = False
        elif action == "speed":
            sp = float(msg.get("speed", self.bt_status["speed"]))
            self.feed.speed = max(0.1, min(sp, 200.0))
            self.bt_status["speed"] = self.feed.speed
        self.send_bt_status()

    # ==================== цикл фида ====================

    async def _feed_loop(self):
        assert self.feed is not None
        feed = self.feed
        try:
            async for ev in feed.run():
                et = ev["type"]
                if et == "tick":
                    self._on_tick(ev)
                elif et == "book":
                    self.state.on_book(ev["bids"], ev["asks"])
                    if isinstance(feed, LiveFeed) and feed.tick_size:
                        self.state.tick_size = feed.tick_size
                    self.send({"type": "book", **self.state.book_snapshot()})
                elif et == "metrics":
                    self.state.deriv.on_oi(ev["ts"], ev["oi"], ev.get("taker", 0.0))
                    self.send_deriv()
                elif et == "funding":
                    self.state.deriv.on_funding(ev["ts"], ev["rate"])
                    self.send_deriv()
                elif et == "warmup":
                    for c in ev["candles"]:
                        self.state.candles.append(c)
                    if ev["candles"]:
                        self.state.last_price = ev["candles"][-1]["close"]
                    self.send({"type": "history", "candles": self.state.candle_history(MAX_CANDLES)})
                elif et == "candle":
                    # реальная закрытая свеча из файла — правим объём последней
                    if self.state.candles:
                        self.state.candles[-1].update(ev["candle"])
                elif et == "progress":
                    self.bt_status["pct"] = ev["pct"]
                    self.bt_status["ts"] = ev["ts"]
                    self.send_bt_status()
                elif et == "status":
                    self.notify("info", ev["text"])
                elif et == "error":
                    self.notify("error", ev["text"])
                elif et == "done":
                    self.bt_status["running"] = False
                    self.send_bt_status()
                    self.notify("info", "Бэктест завершён")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.exception("feed loop error")
            self.notify("error", f"Поток данных прерван: {e}")

    def _on_tick(self, ev: dict):
        ts, price, qty = ev["ts"], ev["price"], ev.get("qty", 0.0)
        if (not isinstance(ts, (int, float)) or ts <= 0
                or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0
                or not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty < 0
                or (self.state.last_ts and ts < self.state.last_ts)):
            log.warning("ignored invalid/out-of-order tick: %r", ev)
            return
        self.state.on_tick(price, qty, ts)
        self.broker.on_tick(ts, price)
        cur = self.state.builder.current
        self.send({"type": "tick", "ts": ts, "price": price,
                   "candle": cur})

    # ==================== ордера + мета-модель ====================

    def place_order(self, msg: dict):
        side = msg.get("side")
        otype = msg.get("orderType", "market")
        size_usd = float(msg.get("sizeUsd", 0))
        price = msg.get("price")
        if side not in ("buy", "sell") or size_usd <= 0:
            self.notify("error", "Некорректный ордер")
            return
        if self.state.last_price <= 0:
            self.notify("warn", "Нет рыночных данных — дождитесь потока")
            return
        qty = size_usd / (float(price) if (otype == "limit" and price) else self.state.last_price)
        ts = self.state.last_ts or _now_ms()

        features = compute_features(self.state, side)

        # --- мета-модель ---
        if self.meta_enabled and self.model.trained and features:
            proba = self.model.predict_proba(features)
            if proba is not None:
                ok = proba >= self.meta_threshold
                if self.meta_mode == "filter" and not ok:
                    self.send({"type": "meta_verdict", "mode": "filter",
                               "accepted": False, "proba": proba,
                               "threshold": self.meta_threshold, "side": side})
                    self.notify("warn",
                                f"Мета-модель ОТКЛОНИЛА сделку {side.upper()} "
                                f"(p={proba:.2f} < {self.meta_threshold:.2f})")
                    return
                # filter-accept или advisory: исполняем
                result = self._execute(side, otype, qty, price, ts, features)
                if self.meta_mode == "advisory":
                    self.send({"type": "meta_verdict", "mode": "advisory",
                               "accepted": ok, "proba": proba,
                               "threshold": self.meta_threshold, "side": side})
                    if ok:
                        self.notify("ok", f"Мета-модель одобряет {side.upper()} (p={proba:.2f})")
                    else:
                        self.notify("warn",
                                    f"Мета-модель считает сделку {side.upper()} ПЛОХОЙ "
                                    f"(p={proba:.2f}) — рекомендуется закрыть")
                else:
                    self.send({"type": "meta_verdict", "mode": "filter",
                               "accepted": True, "proba": proba,
                               "threshold": self.meta_threshold, "side": side})
                return result
        self._execute(side, otype, qty, price, ts, features)

    def _execute(self, side, otype, qty, price, ts, features):
        if otype == "limit" and price:
            return self.broker.place_limit(side, qty, float(price), ts, features)
        return self.broker.place_market(side, qty, ts, features)

    # ==================== модель ====================

    async def train_model(self):
        trades = fetch_trades(10_000)
        loop = asyncio.get_running_loop()
        try:
            metrics = await loop.run_in_executor(None, self.model.train, trades)
            self.notify("ok", f"Модель обучена на {self.model.n_samples} сделках. "
                              f"AUC={metrics.get('auc')}, acc={metrics.get('accuracy'):.3f}")
        except ValueError as e:
            self.notify("warn", str(e))
        except Exception as e:
            log.exception("train failed")
            self.notify("error", f"Ошибка обучения: {e}")
        self.send_model_status()

    def send_model_status(self):
        st = self.model.status()
        st.update({"enabled": self.meta_enabled, "mode": self.meta_mode,
                   "threshold": self.meta_threshold, "tradesCount": count_trades()})
        self.send({"type": "model_status", **st})

    # ==================== настройки ====================

    def apply_settings(self, msg: dict):
        if "takerFee" in msg or "makerFee" in msg:
            self.broker.set_fees(float(msg.get("takerFee", self.broker.taker_fee)),
                                 float(msg.get("makerFee", self.broker.maker_fee)))
        if msg.get("resetBalance"):
            self.broker.reset_account(float(msg.get("startBalance", DEFAULT_START_BALANCE)))
            self.notify("info", "Счёт сброшен")
        self.send_state()

    # ==================== снимки ====================

    def notify(self, level: str, text: str):
        self.send({"type": "notification", "level": level, "text": text})

    def send_deriv(self):
        d = self.state.deriv
        self.send({"type": "deriv", "funding": d.funding_rate,
                   "oi": d.oi_now(), "oiChg1h": d.oi_chg(60, self.state.last_ts)})

    def send_bt_status(self):
        self.send({"type": "bt_status", **self.bt_status})

    def send_state(self):
        self.send({
            "type": "state",
            "mode": self.mode,
            "symbol": self.symbol,
            "account": self.broker.account_dict(),
            "position": self.broker.position.to_dict(self.state.last_price),
            "orders": self.broker.orders_list(),
            "tradesCount": count_trades(),
            "bt": self.bt_status,
        })
        self.send_model_status()
        if self.state.candles:
            self.send({"type": "history", "candles": self.state.candle_history(MAX_CANDLES)})

    async def close(self):
        await self._stop_feed()


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _public_trade(t: dict) -> dict:
    return {k: v for k, v in t.items() if k != "features"}


def _db_trade_public(t: dict) -> dict:
    return {
        "id": t["id"], "symbol": t["symbol"], "side": t["side"], "qty": t["qty"],
        "entryPrice": t["entry_price"], "exitPrice": t["exit_price"],
        "entryTs": t["entry_ts"], "exitTs": t["exit_ts"], "pnl": t["pnl"],
        "fee": t["fee"], "label": t["label"], "mode": t.get("mode", ""),
    }
