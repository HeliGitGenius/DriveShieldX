"""Automatic payment settlement without a public URL (and the unpaid-challan escalation pass).

A background loop asks Razorpay every DSX_PAYMENT_POLL_SECONDS (default 30) about every
open payment link and settles the ones that are paid (same idempotent `settle` as the
redirect and webhook paths, so the challan turns PAID once and the confirmation SMS goes
out once). It starts automatically with the dashboard and with the API server, so a
challan paid from a phone is recorded within about half a minute even when the browser
never returns and no webhook is configured.

  python -m backend.payment_worker          # run it on its own (e.g. on a server)
  DSX_PAYMENT_POLL_SECONDS=0                # disable
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from backend.logger import get_logger

logger = get_logger("payment_worker")
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_lock = threading.Lock()


def interval() -> float:
    try:
        return float(os.environ.get("DSX_PAYMENT_POLL_SECONDS", "30"))
    except ValueError:
        return 30.0


def run_once() -> int:
    """One reconciliation pass. Returns how many links were newly found paid."""
    from backend import razorpay_gateway

    if not razorpay_gateway.enabled():
        return 0
    n = razorpay_gateway.sync_open_links()
    if n:
        logger.info("Auto-settled %d paid challan(s)", n)
    return n


def _loop(every: float) -> None:
    logger.info("Payment auto-settlement running every %.0f s", every)
    while not _stop.is_set():
        try:
            run_once()
        except Exception:
            logger.exception("Payment auto-settlement pass failed")
        try:  # detections with a verified plate become challans even with no dashboard open
            from backend import auto_issue
            auto_issue.run_once()
        except Exception:
            logger.exception("Automatic rule-challan pass failed")
        try:  # unpaid-challan escalation: reminders, hotlist, court referral, live sightings
            from backend import escalation
            escalation.run_once()
        except Exception:
            logger.exception("Escalation pass failed")
        _stop.wait(every)


def start_background() -> bool:
    """Start the loop once per process. Safe to call on every Streamlit rerun."""
    global _thread
    every = interval()
    if every <= 0:
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(every,), name="dsx-payment-worker", daemon=True)
        _thread.start()
        return True


def stop_background() -> None:
    _stop.set()


if __name__ == "__main__":
    from backend.env_loader import load_env

    load_env()
    _loop(interval() if interval() > 0 else 30.0)
