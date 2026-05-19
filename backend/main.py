"""
SnowLink FastAPI backend.

Serves the frontend SPA and exposes:
  GET  /                → frontend/index.html
  GET  /api/state       → current app state snapshot
  GET  /api/settings    → persisted settings
  POST /api/settings    → update settings
  POST /api/control/device → toggle a device
  WS   /ws/state        → live state broadcast at ~5 Hz
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.state import state_manager

log = logging.getLogger("snowlink")

# Paths are resolved relative to this file so the app works regardless of
# which directory uvicorn is launched from.
_ROOT = Path(__file__).parent.parent
FRONTEND_DIR = _ROOT / "frontend"
ASSETS_DIR = _ROOT / "assets"


# ── WebSocket connection registry ─────────────────────────────────────────────

_ws_clients: set[WebSocket] = set()


async def _broadcast_loop() -> None:
    """Send a full state snapshot to every connected WebSocket at ~5 Hz."""
    while True:
        await asyncio.sleep(0.2)
        if not _ws_clients:
            continue
        msg = json.dumps(state_manager.get_snapshot())
        dead: set[WebSocket] = set()
        for ws in list(_ws_clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        _ws_clients -= dead


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Import here to avoid circular imports at module level.
    from backend.services.data_service import DataService
    _data_service = DataService.get()

    task = asyncio.create_task(_broadcast_loop())
    log.info("SnowLink backend started")
    try:
        yield
    finally:
        task.cancel()
        _data_service.stop()
        log.info("SnowLink backend stopped")


app = FastAPI(title="SnowLink", version="1.0.0", lifespan=_lifespan)

# ── Static files ──────────────────────────────────────────────────────────────

app.mount("/src",    StaticFiles(directory=str(FRONTEND_DIR / "src")),    name="src")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)),              name="assets")

# ── API routes ────────────────────────────────────────────────────────────────

from backend.api.routes_state    import router as _state_router
from backend.api.routes_settings import router as _settings_router
from backend.api.routes_control  import router as _control_router

app.include_router(_state_router,    prefix="/api")
app.include_router(_settings_router, prefix="/api")
app.include_router(_control_router,  prefix="/api")


# ── SPA root ──────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.post("/api/exit")
async def exit_app():
    """Gracefully shut down the backend (and with it the kiosk script kills Chromium)."""
    os.kill(os.getpid(), signal.SIGTERM)
    return {"ok": True}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/state")
async def ws_state(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        # Send initial snapshot immediately so the client doesn't wait 200 ms.
        await ws.send_text(json.dumps(state_manager.get_snapshot()))
        # Keep the connection alive; ignore any client messages (ping frames).
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(ws)
