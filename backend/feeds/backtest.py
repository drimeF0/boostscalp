"""Бэктест: загрузка исторических данных с data.binance.vision и реплей.

Источники:
  - klines 1m  : /data/spot/monthly|daily/klines/{SYM}/1m/{SYM}-1m-YYYY-MM[-DD].zip
  - aggTrades  : /data/spot/monthly|daily/aggTrades/{SYM}/{SYM}-aggTrades-YYYY-MM[-DD].zip

Реплей по свечам: каждая свеча проигрывается как O -> (H/L в порядке направления) -> C
с синтетическими тиками; поверх генерируется фейковый стакан.
Реплей по aggTrades: реальные тики с компрессией времени.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import random
import zipfile
from datetime import date, timedelta
from typing import AsyncGenerator, Dict, List, Optional

import httpx

from ..config import (BACKTEST_INTERVAL, BINANCE_DATA_BASE, DATA_DIR,
                      SYNTH_TICKS_PER_CANDLE, WARMUP_CANDLES)
from ..fakebook import gen_book

log = logging.getLogger("backtest")


def _sym(symbol: str) -> str:
    return symbol.replace("/", "").upper()


def _parse_ts(v: str) -> int:
    """Binance с 2025 пишет микросекунды — нормализуем в миллисекунды."""
    ts = int(float(v))
    if ts > 10**14:
        ts //= 1000
    return ts


# -------------------- загрузка --------------------

def _cache_path(kind: str, symbol: str, name: str) -> str:
    d = os.path.join(DATA_DIR, kind, _sym(symbol))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name + ".csv")


async def _download_one(client: httpx.AsyncClient, url: str, dest_csv: str) -> bool:
    if os.path.exists(dest_csv):
        return True
    tmp = dest_csv + ".tmp"
    try:
        r = await client.get(url, timeout=300.0)
        if r.status_code != 200:
            return False
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            with z.open(z.namelist()[0]) as fin, open(tmp, "wb") as fout:
                while True:
                    chunk = fin.read(1 << 20)
                    if not chunk:
                        break
                    fout.write(chunk)
        os.replace(tmp, dest_csv)  # атомарно: без битых файлов в кеше
        return True
    except Exception:
        log.exception("download failed %s", url)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


async def ensure_data(symbol: str, start: date, end: date, data_type: str = "klines",
                      interval: str = BACKTEST_INTERVAL) -> List[str]:
    """Скачивает недостающие файлы, возвращает пути к CSV.

    klines: месячные архивы (+ месяц до start для warmup) + дневные для пробелов.
    aggTrades: только дневные (месячные архивы — гигабайты), warmup не нужен.
    """
    sym = _sym(symbol)
    kind = "klines" if data_type == "klines" else "aggTrades"
    files: List[str] = []
    async with httpx.AsyncClient() as client:
        covered = set()
        if kind == "klines":
            months = []
            y, m = start.year, start.month - 1
            if m == 0:
                y, m = y - 1, 12
            while (y, m) <= (end.year, end.month):
                months.append((y, m))
                m += 1
                if m > 12:
                    y, m = y + 1, 1
            for (y, m) in months:
                tag = f"{y}-{m:02d}"
                name = f"{sym}-{interval}-{tag}"
                dest = _cache_path(kind, symbol, name)
                url = f"{BINANCE_DATA_BASE}/monthly/klines/{sym}/{interval}/{name}.zip"
                if await _download_one(client, url, dest):
                    files.append(dest)
                    covered.add((y, m))
        # дневные файлы для месяцев без месячного архива (или все — для aggTrades)
        d = start
        while d <= end:
            if (d.year, d.month) in covered:
                d += timedelta(days=1)
                continue
            tag = d.isoformat()
            name = f"{sym}-{interval}-{tag}" if kind == "klines" else f"{sym}-aggTrades-{tag}"
            dest = _cache_path(kind, symbol, name)
            if os.path.exists(dest):
                files.append(dest)
            else:
                url = f"{BINANCE_DATA_BASE}/daily/{kind}/{sym}/{interval}/{name}.zip" \
                    if kind == "klines" else \
                    f"{BINANCE_DATA_BASE}/daily/aggTrades/{sym}/{name}.zip"
                if await _download_one(client, url, dest):
                    files.append(dest)
            d += timedelta(days=1)
    return files


# -------------------- парсинг --------------------

def load_klines(files: List[str], start_ms: int, end_ms: int,
                warmup: int = WARMUP_CANDLES) -> (List[dict], List[dict]):
    """Возвращает (warmup_candles, replay_candles)."""
    candles: Dict[int, dict] = {}
    for path in files:
        with open(path, newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue  # заголовок
                ts = _parse_ts(row[0])
                candles[ts] = {
                    "time": ts // 1000,
                    "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4]),
                    "volume": float(row[5]),
                }
    ordered = [candles[k] for k in sorted(candles)]
    warm = [c for c in ordered if c["time"] * 1000 < start_ms][-warmup:]
    replay = [c for c in ordered if start_ms <= c["time"] * 1000 <= end_ms]
    return warm, replay


def load_aggtrades(files: List[str], start_ms: int, end_ms: int) -> List[dict]:
    import pandas as pd
    frames = []
    for path in files:
        df = pd.read_csv(
            path, header=None, usecols=[1, 2, 5],
            names=["price", "qty", "ts"], dtype=float, skiprows=1, on_bad_lines="skip",
        )
        if df.empty:
            continue
        # нормализация микросекунд в миллисекунды
        if df["ts"].iloc[0] > 10**14:
            df["ts"] = df["ts"] / 1000.0
        df = df[(df["ts"] >= start_ms) & (df["ts"] <= end_ms)]
        frames.append(df)
    if not frames:
        return []
    df = pd.concat(frames).sort_values("ts")
    return [{"ts": int(t), "price": p, "qty": q}
            for t, p, q in zip(df["ts"].values, df["price"].values, df["qty"].values)]


# -------------------- реплей --------------------

class BacktestFeed:
    """Асинхронный генератор событий: tick / candle / book / progress / done."""

    def __init__(self, symbol: str, start: date, end: date, speed: float = 5.0,
                 data_type: str = "klines", tick_size: float = 0.01):
        self.symbol = symbol
        self.start = start
        self.end = end
        self.speed = max(0.1, min(speed, 1000.0))
        self.data_type = data_type
        self.tick_size = tick_size
        self.paused = asyncio.Event()
        self.paused.set()  # set = идём, clear = пауза
        self._stop = False
        self._rng = random.Random(42)

    def stop(self):
        self._stop = True
        self.paused.set()

    async def run(self) -> AsyncGenerator[dict, None]:
        start_ms = int(_to_ms(self.start))
        end_ms = int(_to_ms(self.end)) + 86_399_000

        yield {"type": "status", "text": "Загрузка исторических данных..."}
        try:
            files = await ensure_data(self.symbol, self.start, self.end, self.data_type)
        except Exception as e:
            yield {"type": "error", "text": f"Ошибка загрузки данных: {e}"}
            return
        if not files:
            yield {"type": "error", "text": "Нет данных за выбранный период (data.binance.vision)"}
            return

        if self.data_type == "klines":
            async for ev in self._run_klines(files, start_ms, end_ms):
                yield ev
        else:
            async for ev in self._run_trades(files, start_ms, end_ms):
                yield ev
        yield {"type": "done"}

    async def _run_klines(self, files, start_ms, end_ms):
        warm, replay = load_klines(files, start_ms, end_ms)
        if not replay:
            yield {"type": "error", "text": "Нет свечей в выбранном диапазоне"}
            return
        yield {"type": "warmup", "candles": warm}
        candle_delay = 60.0 / self.speed  # секунд на 1m свечу
        tick_delay = candle_delay / SYNTH_TICKS_PER_CANDLE
        total = len(replay)
        for i, c in enumerate(replay):
            if self._stop:
                return
            await self.paused.wait()
            # синтетический путь внутри свечи
            path = [c["open"]]
            if c["close"] >= c["open"]:
                path += [c["low"], c["high"]]
            else:
                path += [c["high"], c["low"]]
            path.append(c["close"])
            # добавляем промежуточные точки
            ticks: List[float] = []
            seg = SYNTH_TICKS_PER_CANDLE // (len(path) - 1) or 1
            for a, b in zip(path, path[1:]):
                for k in range(seg):
                    ticks.append(a + (b - a) * k / seg)
            ticks.append(c["close"])
            ts0 = c["time"] * 1000
            vol_per = c["volume"] / max(len(ticks), 1)
            for j, px in enumerate(ticks):
                if self._stop:
                    return
                await self.paused.wait()
                ts = ts0 + int(j * 60000 / len(ticks))
                yield {"type": "tick", "ts": ts, "price": px, "qty": vol_per}
                bids, asks = gen_book(px, self.tick_size, rng=self._rng)
                yield {"type": "book", "bids": bids, "asks": asks}
                await asyncio.sleep(tick_delay)
            yield {"type": "candle", "candle": c}
            yield {"type": "progress", "pct": (i + 1) / total * 100, "ts": ts0}

    async def _run_trades(self, files, start_ms, end_ms):
        trades = load_aggtrades(files, start_ms, end_ms)
        if not trades:
            yield {"type": "error", "text": "Нет тиков в выбранном диапазоне"}
            return
        total = len(trades)
        prev_ts = trades[0]["ts"]
        for i, t in enumerate(trades):
            if self._stop:
                return
            await self.paused.wait()
            dt = (t["ts"] - prev_ts) / 1000.0 / self.speed
            prev_ts = t["ts"]
            if dt > 0:
                await asyncio.sleep(min(dt, 1.0))
            yield {"type": "tick", "ts": t["ts"], "price": t["price"], "qty": t["qty"]}
            if i % 10 == 0:
                bids, asks = gen_book(t["price"], self.tick_size, rng=self._rng)
                yield {"type": "book", "bids": bids, "asks": asks}
                yield {"type": "progress", "pct": (i + 1) / total * 100, "ts": t["ts"]}


def _to_ms(d: date) -> int:
    import calendar
    return calendar.timegm(d.timetuple()) * 1000
