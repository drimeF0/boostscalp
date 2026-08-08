"""FastAPI: раздача фронтенда + WebSocket endpoint /ws."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR
from .db import init_db
from .session import Session

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="Trading Trainer")


@app.on_event("startup")
async def _startup():
    init_db()

    # прогрев тяжёлых импортов в треде: потом они мгновенные (кеш sys.modules)
    def _warm():
        try:
            import catboost  # noqa: F401
            import sklearn.metrics  # noqa: F401
        except Exception:
            pass
    await asyncio.to_thread(_warm)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
    session = Session(queue)
    log.info("client connected")

    async def writer():
        while True:
            msg = await queue.get()
            await ws.send_text(json.dumps(msg))

    wtask = asyncio.create_task(writer())
    session.send_state()
    # модель подгружаем асинхронно — статус придёт отдельным сообщением
    await asyncio.to_thread(session.model.load)
    session.send_model_status()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await session.handle(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws error")
    finally:
        wtask.cancel()
        await session.close()
        log.info("client disconnected")


@app.get("/")
def index():
    return FileResponse(f"{FRONTEND_DIR}/index.html")


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
