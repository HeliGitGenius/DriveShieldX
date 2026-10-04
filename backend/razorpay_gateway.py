"""Razorpay integration for e-challan payments (real gateway, test or live mode).

Flow (the same three-layer confirmation real payment products use):

  1. create  : authority/owner clicks Pay -> a Razorpay Payment Link is created for the
               challan (amount, reference_id, customer). Razorpay hosts the checkout
               (UPI / cards / netbanking / wallets) and can SMS/e-mail the link itself.
  2. confirm : (a) REDIRECT  - after paying, Razorpay sends the browser back to
                               DSX_PUBLIC_BASE_URL with signed query parameters; the app
                               verifies the signature with the key secret and settles.
               (b) WEBHOOK   - Razorpay POSTs `payment_link.paid` to
                               /razorpay/webhook (backend/api_server.py); the signature is
                               verified with the webhook secret (needs a public URL, e.g.
                               ngrok, during development).
               (c) RECONCILE - "Sync" asks Razorpay for the link status (no public URL
                               needed); also runs when the owner opens their challans.
  3. settle  : idempotent -- whichever path arrives first marks the challan PAID once
               (pay_notice), stores the Razorpay payment id, and sends the confirmation
               SMS/e-mail; the others become no-ops.
  Refunds    : POST /payments/{id}/refund; the challan returns to 'pending'.

Configuration (.env; keys are NEVER committed):
  DSX_PAYMENT_MODE            test (default) | live
  RAZORPAY_KEY_ID_TEST / RAZORPAY_KEY_SECRET_TEST
  RAZORPAY_KEY_ID_LIVE / RAZORPAY_KEY_SECRET_LIVE
  RAZORPAY_WEBHOOK_SECRET     the secret you typed when creating the webhook
  DSX_PUBLIC_BASE_URL         where Razorpay redirects after payment (default http://localhost:8501)
  DSX_LIVE_MAX_AMOUNT         safety cap for live mode in rupees (default 10). Real traffic
                              fines may only be collected by the authority; a student
                              deployment must never take real fines from the public.
If no keys are set, `enabled()` is False and the app keeps its built-in demo checkout.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from backend.env_loader import load_env
from backend.logger import get_logger
from database import payments_db

load_env()

logger = get_logger("razorpay")
API = "https://api.razorpay.com/v1"


@dataclass
class RazorpayConfig:
    mode: str
    key_id: str
    key_secret: str
    webhook_secret: str
    base_url: str
    live_max_amount: float


def config() -> Optional[RazorpayConfig]:
    mode = os.environ.get("DSX_PAYMENT_MODE", "test").strip().lower()
    mode = "live" if mode == "live" else "test"
    suffix = mode.upper()
    key_id = os.environ.get(f"RAZORPAY_KEY_ID_{suffix}", "").strip()
    secret = os.environ.get(f"RAZORPAY_KEY_SECRET_{suffix}", "").strip()
    if not (key_id and secret):
        return None
    if mode == "test" and not key_id.startswith("rzp_test_"):
        logger.error("DSX_PAYMENT_MODE=test but the key is not a rzp_test_ key; refusing to start")
        return None
    if mode == "live" and not key_id.startswith("rzp_live_"):
        logger.error("DSX_PAYMENT_MODE=live but the key is not a rzp_live_ key; refusing to start")
        return None
    return RazorpayConfig(mode=mode, key_id=key_id, key_secret=secret,
                          webhook_secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip(),
                          base_url=os.environ.get("DSX_PUBLIC_BASE_URL", "http://localhost:8501").rstrip("/"),
                          live_max_amount=float(os.environ.get("DSX_LIVE_MAX_AMOUNT", "10")))


def enabled() -> bool:
    return config() is not None


def _session(cfg: RazorpayConfig):
    import requests

    s = requests.Session()
    s.auth = (cfg.key_id, cfg.key_secret)
    s.headers.update({"Content-Type": "application/json"})
    return s


def _phone(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    digits = "".join(ch for ch in str(p) if ch.isdigit())
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return None


# ------------------------------------------------------------------ create
def create_payment_link(notice: Dict[str, Any], amount_rupees: float, owner: Optional[Dict[str, Any]] = None,
                        notify: bool = True) -> Dict[str, Any]:
    """Create (or reuse) a Razorpay Payment Link for a challan. Returns the stored row."""
    cfg = config()
    if cfg is None:
        raise RuntimeError("Razorpay is not configured (set RAZORPAY_KEY_ID_TEST / RAZORPAY_KEY_SECRET_TEST).")
    if cfg.mode == "live" and amount_rupees > cfg.live_max_amount:
        raise PermissionError(
            f"Live mode is capped at Rs {cfg.live_max_amount:.0f} (DSX_LIVE_MAX_AMOUNT). Real traffic fines can only "
            f"be collected by the traffic authority; use a Rs 1 demo challan for a live demonstration.")
    nid = int(notice["notice_id"])
    amount_paise = int(round(amount_rupees * 100))
    existing = payments_db.open_link_for(nid, cfg.mode, amount_paise)
    if existing:
        return existing
    ref = f"DSX-{nid}-{int(time.time())}"[:40]
    body: Dict[str, Any] = {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "reference_id": ref,
        "description": f"DriveShieldX e-challan #{nid} ({notice.get('plate_number') or 'vehicle'})",
        "notes": {"notice_id": str(nid), "plate": str(notice.get("plate_number") or "")},
        "reminder_enable": True,
        "callback_url": f"{cfg.base_url}/",
        "callback_method": "get",
        "expire_by": int(time.time()) + 7 * 24 * 3600,
    }
    customer = {}
    if owner:
        if owner.get("name"):
            customer["name"] = owner["name"]
        if owner.get("email"):
            customer["email"] = owner["email"]
        if _phone(owner.get("phone")):
            customer["contact"] = _phone(owner.get("phone"))
    if customer:
        body["customer"] = customer
        body["notify"] = {"sms": bool(notify and customer.get("contact")), "email": bool(notify and customer.get("email"))}
    sess = _session(cfg)
    r = sess.post(f"{API}/payment_links", data=json.dumps(body), timeout=20)
    if r.status_code == 400 and "callback" in r.text.lower():
        # Some accounts reject a non-public callback URL (e.g. localhost). The webhook and
        # the status check still confirm the payment, so create the link without it.
        logger.warning("Razorpay rejected callback_url %s; creating link without redirect", body["callback_url"])
        body.pop("callback_url"); body.pop("callback_method")
        r = sess.post(f"{API}/payment_links", data=json.dumps(body), timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Razorpay error {r.status_code}: {r.text[:300]}")
    link = r.json()
    payments_db.insert_link(nid, cfg.mode, link["id"], ref, link.get("short_url"), amount_paise,
                            link.get("status", "created"), link)
    logger.info("Payment link %s created for challan %s (%s mode)", link["id"], nid, cfg.mode)
    return payments_db.get_link(link["id"])


# ------------------------------------------------------------------ signatures
def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_callback_signature(params: Dict[str, str], key_secret: Optional[str] = None) -> bool:
    """Redirect check: HMAC_SHA256(link_id|reference_id|status|payment_id, key_secret)."""
    cfg = config()
    secret = key_secret or (cfg.key_secret if cfg else "")
    needed = ("razorpay_payment_link_id", "razorpay_payment_link_reference_id",
              "razorpay_payment_link_status", "razorpay_payment_id", "razorpay_signature")
    if not secret or any(not params.get(k) for k in needed):
        return False
    msg = "|".join(params[k] for k in needed[:4]).encode()
    return hmac.compare_digest(_hmac(secret, msg), params["razorpay_signature"])


def verify_webhook_signature(body: bytes, signature: str, webhook_secret: Optional[str] = None) -> bool:
    cfg = config()
    secret = webhook_secret or (cfg.webhook_secret if cfg else "")
    return bool(secret and signature) and hmac.compare_digest(_hmac(secret, body), signature)


# ------------------------------------------------------------------ settle (idempotent)
def settle(link_id: str, payment_id: str, method: Optional[str], source: str) -> bool:
    """Mark the challan paid exactly once. Returns True if this call settled it."""
    row = payments_db.get_link(link_id)
    if row is None:
        logger.warning("Settle for unknown link %s ignored", link_id)
        return False
    from database.db_manager import pay_notice

    if not payments_db.mark_link_paid(link_id, payment_id, method, source):
        # Already settled by another path. Repair the rare case where the link was marked
        # paid but the challan update did not complete (e.g. the app was closed mid-way).
        row = payments_db.get_link(link_id)
        if row and row["status"] == "paid" and _notice_status(int(row["notice_id"])) not in ("paid", None):
            pay_notice(int(row["notice_id"]), f"Razorpay {(row.get('method') or '').upper()}".strip(),
                       txn_ref=f"{row['reference_id']}/{row['payment_id']}")
            logger.warning("Challan %s re-settled from link %s", row["notice_id"], link_id)
        return False

    label = f"Razorpay {method.upper()}" if method else "Razorpay"
    pay_notice(int(row["notice_id"]), label, txn_ref=f"{row['reference_id']}/{payment_id}")
    _notify(int(row["notice_id"]), row["amount_paise"] / 100.0, label, payment_id)
    logger.info("Challan %s settled via %s (%s)", row["notice_id"], source, payment_id)
    return True


def _notice_status(notice_id: int) -> Optional[str]:
    from database.db_manager import get_connection

    c = get_connection()
    try:
        r = c.execute("SELECT payment_status FROM VIOLATION_NOTICE WHERE notice_id=?", (notice_id,)).fetchone()
        return r[0] if r else None
    finally:
        c.close()


def _notify(notice_id: int, amount: float, method: str, payment_id: str) -> None:
    try:
        from backend.notifications import send_payment_confirmation

        info = payments_db.notice_contact(notice_id)
        send_payment_confirmation(notice_id=notice_id, plate_number=info.get("plate_number") or "-",
                                  amount=amount, method=method, txn_ref=payment_id,
                                  owner_email=info.get("email"), owner_phone=info.get("phone"))
    except Exception:
        logger.exception("Payment confirmation failed for challan %s", notice_id)


def handle_callback(params: Dict[str, str]) -> Tuple[bool, str]:
    """Browser redirect after checkout. Returns (settled_or_already_paid, message)."""
    if not params.get("razorpay_payment_link_id"):
        return False, "not a Razorpay callback"
    if not verify_callback_signature(params):
        return False, "Signature check failed - payment NOT accepted."
    if params.get("razorpay_payment_link_status") != "paid":
        return False, f"Payment status: {params.get('razorpay_payment_link_status')}"
    method = _payment_method(params["razorpay_payment_id"])
    settle(params["razorpay_payment_link_id"], params["razorpay_payment_id"], method, "redirect")
    return True, f"Payment {params['razorpay_payment_id']} verified."


def handle_webhook(body: bytes, signature: str) -> Tuple[bool, str]:
    if not verify_webhook_signature(body, signature):
        return False, "bad signature"
    event = json.loads(body.decode("utf-8"))
    name = event.get("event", "")
    pl = (event.get("payload", {}).get("payment_link") or {}).get("entity") or {}
    if name in ("payment_link.expired", "payment_link.cancelled") and pl.get("id"):
        payments_db.update_link_status(pl["id"], name.split(".")[1])
        return True, name
    if name != "payment_link.paid":
        return True, f"ignored {name}"
    pay = (event["payload"].get("payment") or {}).get("entity") or {}
    row = payments_db.get_link(pl.get("id", ""))
    if row is None:
        return True, "unknown link (created by another system)"
    paid = int(pl.get("amount_paid") or pay.get("amount") or 0)
    if paid < int(row["amount_paise"]):
        logger.error("Link %s: paid %s paise < challan %s paise; not settling", pl["id"], paid, row["amount_paise"])
        return True, "amount mismatch"
    settle(pl["id"], pay.get("id", ""), pay.get("method"), "webhook")
    return True, "settled"


def _payment_method(payment_id: str) -> Optional[str]:
    cfg = config()
    if not cfg:
        return None
    try:
        r = _session(cfg).get(f"{API}/payments/{payment_id}", timeout=15)
        return r.json().get("method") if r.status_code < 300 else None
    except Exception:
        return None


# ------------------------------------------------------------------ reconcile
def sync_link(link_id: str) -> str:
    """Ask Razorpay for the link's status; settles if paid. Returns the status."""
    cfg = config()
    if cfg is None:
        return "not_configured"
    r = _session(cfg).get(f"{API}/payment_links/{link_id}", timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Razorpay error {r.status_code}: {r.text[:300]}")
    data = r.json()
    status = data.get("status", "unknown")
    payments_db.update_link_status(link_id, status)
    if status == "paid":
        pays = [p for p in (data.get("payments") or []) if p.get("status") in ("captured", None)]
        if pays:
            pid = pays[-1].get("payment_id") or pays[-1].get("id")
            settle(link_id, pid, pays[-1].get("method") or _payment_method(pid), "reconcile")
    return status


def sync_open_links(notice_ids: Optional[list] = None) -> int:
    n = 0
    for row in payments_db.open_links(notice_ids):
        try:
            if sync_link(row["link_id"]) == "paid":
                n += 1
        except Exception as exc:
            logger.warning("Sync of %s failed: %s", row["link_id"], exc)
    return n


# ------------------------------------------------------------------ refund
def refund(link_id: str, reason: str = "demo refund") -> Dict[str, Any]:
    cfg = config()
    row = payments_db.get_link(link_id)
    if cfg is None or row is None or not row.get("payment_id"):
        raise RuntimeError("Nothing to refund.")
    r = _session(cfg).post(f"{API}/payments/{row['payment_id']}/refund",
                           data=json.dumps({"amount": row["amount_paise"], "notes": {"reason": reason}}), timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Razorpay error {r.status_code}: {r.text[:300]}")
    rf = r.json()
    payments_db.mark_refunded(link_id, rf.get("id"), rf.get("status"))
    from database.db_manager import update_notice_payment_status, log_system_event

    update_notice_payment_status(int(row["notice_id"]), "pending")
    log_system_event("payment", f"Challan {row['notice_id']} refunded ({rf.get('id')}, {rf.get('status')})")
    return rf
