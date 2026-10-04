from __future__ import annotations

import asyncio

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from backend.env_loader import load_env

load_env()

from backend.event_bus import BUS, serialize_event
from backend.health import health_snapshot
from database.db_manager import get_all_cameras, get_system_events, init_database

app = FastAPI(title="DriveShieldX Local Backend", version="1.0.0")
init_database()


@app.on_event("startup")
def _start_payment_worker() -> None:
    try:
        from backend import payment_worker

        payment_worker.start_background()
    except Exception:
        pass


@app.get("/healthz")
def healthz():
    return health_snapshot()


@app.get("/cameras")
def cameras():
    return get_all_cameras()


@app.get("/events")
def events():
    return get_system_events(100)


@app.post("/razorpay/webhook")
async def razorpay_webhook(request: Request):
    """Razorpay -> DriveShieldX server-to-server confirmation (event payment_link.paid).
    The raw body is verified against X-Razorpay-Signature with RAZORPAY_WEBHOOK_SECRET
    before anything is trusted. 2xx tells Razorpay not to retry."""
    from backend import razorpay_gateway

    body = await request.body()
    ok, msg = razorpay_gateway.handle_webhook(body, request.headers.get("X-Razorpay-Signature", ""))
    return JSONResponse({"ok": ok, "detail": msg}, status_code=200 if ok else 400)


@app.get("/razorpay/status")
def razorpay_status():
    from backend import razorpay_gateway

    cfg = razorpay_gateway.config()
    return {"enabled": cfg is not None, "mode": cfg.mode if cfg else None,
            "webhook_secret_set": bool(cfg and cfg.webhook_secret)}


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    queue = await BUS.subscribe()
    try:
        for event in BUS.history()[-25:]:
            await websocket.send_text(serialize_event(event))
        while True:
            event = await queue.get()
            await websocket.send_text(serialize_event(event))
    except WebSocketDisconnect:
        BUS.unsubscribe(queue)
    except asyncio.CancelledError:
        BUS.unsubscribe(queue)
        raise
    except Exception:
        BUS.unsubscribe(queue)
        await websocket.close()
