"""Движок демо-торговли: ордера, позиции, стоп/тейк, комиссии, журнал сделок."""
from __future__ import annotations

import itertools
import logging
from typing import Callable, Dict, List, Optional

from .market import MarketState

log = logging.getLogger("broker")


class Order:
    __slots__ = ("id", "side", "type", "price", "qty", "status", "ts", "features")

    def __init__(self, oid, side, otype, price, qty, ts, features=None):
        self.id = oid
        self.side = side          # "buy" | "sell"
        self.type = otype         # "market" | "limit"
        self.price = price
        self.qty = qty
        self.status = "open"
        self.ts = ts
        self.features = features  # фичи на момент входа (для мета-модели)

    def to_dict(self):
        return {
            "id": self.id, "side": self.side, "type": self.type,
            "price": self.price, "qty": self.qty, "status": self.status, "ts": self.ts,
        }


class Position:
    __slots__ = ("qty", "entry", "sl", "tp", "open_ts", "entry_features", "fees_paid")

    def __init__(self):
        self.qty = 0.0            # знаковое количество базовой валюты
        self.entry = 0.0          # средняя цена входа
        self.sl: Optional[float] = None
        self.tp: Optional[float] = None
        self.open_ts: int = 0
        self.entry_features: Optional[dict] = None
        self.fees_paid: float = 0.0

    def to_dict(self, last_price: float):
        upnl = (last_price - self.entry) * self.qty if self.qty != 0 else 0.0
        return {
            "qty": self.qty, "entry": self.entry, "sl": self.sl, "tp": self.tp,
            "upnl": upnl, "openTs": self.open_ts,
        }


class PaperBroker:
    """Симулятор исполнения. События отдаёт подписчикам (session)."""

    def __init__(self, state: MarketState, balance: float = 10_000.0,
                 taker_fee: float = 0.001, maker_fee: float = 0.0002):
        self.state = state
        self.balance = balance
        self.start_balance = balance
        self.taker_fee = taker_fee
        self.maker_fee = maker_fee
        self.position = Position()
        self.orders: Dict[int, Order] = {}
        self._oid = itertools.count(1)
        self.realized = 0.0
        self.listeners: List[Callable[[str, dict], None]] = []
        self.trade_seq = itertools.count(1)

    # -------------------- события --------------------

    def subscribe(self, fn: Callable[[str, dict], None]):
        self.listeners.append(fn)

    def emit(self, kind: str, payload: dict):
        for fn in self.listeners:
            try:
                fn(kind, payload)
            except Exception:
                log.exception("listener error")

    # -------------------- сервис --------------------

    def reset_account(self, balance: float):
        self.balance = balance
        self.start_balance = balance
        self.realized = 0.0
        self.position = Position()
        self.orders.clear()
        self.emit("account", self.account_dict())
        self.emit("position", self.position.to_dict(self.state.last_price))
        self.emit("orders", self.orders_list())

    def set_fees(self, taker: float, maker: float):
        self.taker_fee = max(0.0, taker)
        self.maker_fee = max(0.0, maker)

    def equity(self) -> float:
        p = self.position
        upnl = (self.state.last_price - p.entry) * p.qty if p.qty != 0 else 0.0
        return self.balance + upnl

    def account_dict(self) -> dict:
        return {
            "balance": self.balance,
            "equity": self.equity(),
            "realized": self.realized,
            "startBalance": self.start_balance,
        }

    def orders_list(self) -> List[dict]:
        return [o.to_dict() for o in self.orders.values()]

    # -------------------- исполнение --------------------

    def _exec_price(self, side: str, limit_price: Optional[float] = None) -> float:
        """Цена исполнения: по стакану, если есть; иначе по последней цене."""
        st = self.state
        if limit_price is not None:
            return limit_price
        if side == "buy":
            return st.best_ask() or st.last_price
        return st.best_bid() or st.last_price

    def _fill(self, side: str, qty: float, price: float, ts: int,
              fee_rate: float, features: Optional[dict], order_id: Optional[int]):
        st = self.state
        signed = qty if side == "buy" else -qty
        fee = qty * price * fee_rate
        p = self.position

        # --- обновление позиции ---
        closing_trade = None
        if p.qty == 0 or (p.qty > 0) == (signed > 0):
            # открытие / увеличение
            new_qty = p.qty + signed
            if p.qty == 0:
                p.entry = price
                p.open_ts = ts
                p.entry_features = features
                p.fees_paid = 0.0
            else:
                p.entry = (p.entry * abs(p.qty) + price * abs(signed)) / abs(new_qty)
            p.qty = new_qty
            p.fees_paid += fee
        else:
            # закрытие / разворот
            close_qty = min(abs(signed), abs(p.qty))
            pnl = (price - p.entry) * close_qty * (1 if p.qty > 0 else -1)
            pnl -= p.fees_paid * (close_qty / abs(p.qty)) + fee
            self.realized += pnl
            self.balance += pnl
            remaining = signed + p.qty  # что осталось после закрытия
            closing_trade = {
                "seq": next(self.trade_seq),
                "symbol": st.symbol,
                "side": "buy" if p.qty > 0 else "sell",
                "qty": close_qty,
                "entryPrice": p.entry,
                "exitPrice": price,
                "entryTs": p.open_ts,
                "exitTs": ts,
                "pnl": pnl,
                "fee": p.fees_paid * (close_qty / abs(p.qty)) + fee,
                "features": p.entry_features,
                "label": 1 if pnl > 0 else 0,
            }
            p.fees_paid *= max(0.0, 1 - close_qty / abs(p.qty))
            if abs(remaining) < 1e-12:
                p.qty = 0.0
                p.entry = 0.0
                p.sl = p.tp = None
                p.entry_features = None
            else:
                # разворот
                p.qty = remaining
                p.entry = price
                p.open_ts = ts
                p.entry_features = features
                p.sl = p.tp = None

        self.emit("fill", {
            "orderId": order_id, "side": side, "qty": qty, "price": price,
            "fee": fee, "ts": ts,
        })
        self.emit("position", self.position.to_dict(st.last_price))
        self.emit("account", self.account_dict())
        if closing_trade:
            self.emit("trade_closed", closing_trade)

    def place_market(self, side: str, qty: float, ts: int,
                     features: Optional[dict] = None) -> dict:
        price = self._exec_price(side)
        oid = next(self._oid)
        self._fill(side, qty, price, ts, self.taker_fee, features, oid)
        return {"id": oid, "price": price, "status": "filled"}

    def place_limit(self, side: str, qty: float, price: float, ts: int,
                    features: Optional[dict] = None) -> dict:
        # если цена уже рыночная — исполняем сразу
        st = self.state
        if side == "buy" and st.best_ask() and price >= st.best_ask():
            return self.place_market(side, qty, ts, features)
        if side == "sell" and st.best_bid() and price <= st.best_bid():
            return self.place_market(side, qty, ts, features)
        oid = next(self._oid)
        order = Order(oid, side, "limit", price, qty, ts, features)
        self.orders[oid] = order
        self.emit("orders", self.orders_list())
        return {"id": oid, "price": price, "status": "open"}

    def cancel(self, order_id: int) -> bool:
        if order_id in self.orders:
            del self.orders[order_id]
            self.emit("orders", self.orders_list())
            return True
        return False

    def cancel_all(self):
        self.orders.clear()
        self.emit("orders", self.orders_list())

    def close_position(self, ts: int):
        p = self.position
        if p.qty == 0:
            return
        side = "sell" if p.qty > 0 else "buy"
        self.place_market(side, abs(p.qty), ts)

    # -------------------- стоп / тейк --------------------

    def set_sl_tp(self, price: float) -> Optional[dict]:
        """Авто-определение: ниже рынка при лонге — стоп, выше — тейк (и наоборот для шорта)."""
        p = self.position
        if p.qty == 0:
            return None
        last = self.state.last_price
        if p.qty > 0:
            kind = "sl" if price < last else "tp"
        else:
            kind = "sl" if price > last else "tp"
        setattr(p, kind, price)
        self.emit("position", p.to_dict(last))
        return {"kind": kind, "price": price}

    def cancel_sl_tp(self):
        p = self.position
        p.sl = p.tp = None
        self.emit("position", p.to_dict(self.state.last_price))

    # -------------------- тиковый матчинг --------------------

    def on_tick(self, ts: int, price: float):
        # 1) лимитные ордера
        filled = []
        for oid, o in self.orders.items():
            if o.side == "buy" and price <= o.price:
                filled.append(oid)
            elif o.side == "sell" and price >= o.price:
                filled.append(oid)
        for oid in filled:
            o = self.orders.pop(oid)
            self._fill(o.side, o.qty, o.price, ts, self.maker_fee, o.features, oid)
            self.emit("orders", self.orders_list())

        # 2) стоп / тейк
        p = self.position
        if p.qty > 0:
            if p.sl is not None and price <= p.sl:
                self.place_market("sell", abs(p.qty), ts)
            elif p.tp is not None and price >= p.tp:
                self.place_market("sell", abs(p.qty), ts)
        elif p.qty < 0:
            if p.sl is not None and price >= p.sl:
                self.place_market("buy", abs(p.qty), ts)
            elif p.tp is not None and price <= p.tp:
                self.place_market("buy", abs(p.qty), ts)
