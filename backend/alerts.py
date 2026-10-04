from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, Tuple

from backend.logger import get_logger
from database.db_manager import mark_alert_sent, queue_alert

logger = get_logger("alerts")
BASE_DIR = Path(__file__).resolve().parents[1]
OUTBOX_DIR = BASE_DIR / "logs" / "alert_outbox"
OUTBOX_DIR.mkdir(parents=True, exist_ok=True)


def _smtp_settings() -> Optional[dict]:
    host = os.getenv("OVERSPEED_SMTP_HOST")
    port = os.getenv("OVERSPEED_SMTP_PORT")
    user = os.getenv("OVERSPEED_SMTP_USER")
    password = os.getenv("OVERSPEED_SMTP_PASSWORD")
    sender = os.getenv("OVERSPEED_SMTP_SENDER") or user
    if not (host and port and sender):
        return None
    return {"host": host, "port": int(port), "user": user, "password": password, "sender": sender}


def send_email_alert(recipient: str, subject: str, body: str) -> int:
    alert_id = queue_alert("email", recipient, subject, body)
    cfg = _smtp_settings()
    if not cfg:
        file_path = OUTBOX_DIR / f"email_{alert_id}.txt"
        file_path.write_text(f"TO: {recipient}\nSUBJECT: {subject}\n\n{body}", encoding="utf-8")
        mark_alert_sent(alert_id, "sent", None)
        logger.info("Email alert written locally to %s", file_path)
        return alert_id

    try:
        msg = EmailMessage()
        msg["From"] = cfg["sender"]
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            server.starttls()
            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        mark_alert_sent(alert_id, "sent")
        logger.info("Email alert sent to %s", recipient)
    except Exception as exc:
        mark_alert_sent(alert_id, "failed", str(exc))
        logger.exception("Email alert failed")
    return alert_id


def normalise_in_phone(value: Optional[str]) -> Optional[str]:
    """'+91 98765-43210', '09876543210', '9876543210' -> '+919876543210'. None if not an Indian mobile."""
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


def _allowlist() -> Optional[set]:
    raw = os.getenv("DSX_SMS_ALLOWLIST", "")
    nums = {normalise_in_phone(x) for x in raw.split(",") if x.strip()}
    nums.discard(None)
    return nums


def sms_gate(recipient: str) -> Optional[str]:
    """Why a real SMS must NOT go to this recipient (None = allowed).

    Safety for a student deployment: a real gateway only texts numbers listed in
    DSX_SMS_ALLOWLIST (your team's phones), unless DSX_SMS_ALLOW_ALL=1 is set
    deliberately. Everything is still written to the outbox either way."""
    phone = normalise_in_phone(recipient)
    if phone is None:
        return "not a phone number"
    if os.getenv("DSX_SMS_ALLOW_ALL", "").strip() == "1":
        return None
    if phone not in _allowlist():
        return "not in DSX_SMS_ALLOWLIST"
    return None


def _send_sms_via_provider(recipient: str, body: str) -> Optional[str]:
    """Deliver through a real gateway if one is configured. Returns None on
    success, an error string on failure, or "not_configured".

    DSX_SMS_PROVIDER=twilio   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM
    DSX_SMS_PROVIDER=smsgate  SMSGATE_USER, SMSGATE_PASSWORD (+ optional SMSGATE_URL for local mode):
                              free open-source Android app (sms-gate.app), SMS from the phone's own SIM
    DSX_SMS_PROVIDER=textbee  TEXTBEE_API_KEY (+ optional TEXTBEE_DEVICE_ID): your Android phone sends
                              the SMS from its own SIM via the textbee.dev app (free tier)
    DSX_SMS_PROVIDER=fast2sms FAST2SMS_API_KEY (Indian gateway; now requires DLT registration)
    DSX_SMS_PROVIDER=webhook  DSX_SMS_WEBHOOK_URL (+ optional DSX_SMS_WEBHOOK_TOKEN):
                              POSTs {"to": ..., "message": ...} -- use this to plug in
                              any other gateway (MSG91, a DLT-registered sender, ...).
    """
    provider = (os.getenv("DSX_SMS_PROVIDER") or "").strip().lower()
    if not provider:
        return "not_configured"
    phone = normalise_in_phone(recipient) or recipient
    try:
        import requests

        if provider == "twilio":
            sid, token, sender = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"), os.getenv("TWILIO_FROM")
            if not (sid and token and sender):
                return "twilio credentials missing"
            r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                              data={"To": phone, "From": sender, "Body": body[:1500]}, auth=(sid, token), timeout=15)
        elif provider == "fast2sms":
            key = os.getenv("FAST2SMS_API_KEY")
            if not key:
                return "FAST2SMS_API_KEY missing"
            r = requests.post("https://www.fast2sms.com/dev/bulkV2",
                              json={"route": "q", "message": body[:700], "numbers": phone[-10:], "flash": "0"},
                              headers={"authorization": key}, timeout=15)
            if r.status_code < 300:
                try:
                    data = r.json()
                except ValueError:
                    data = {}
                if data.get("return") is False:
                    return f"fast2sms: {data.get('message')}"
        elif provider == "smsgate":
            # SMSGate (sms-gate.app): free, open-source Android app; the phone sends a normal SMS from
            # its own SIM. Cloud mode (default URL) or local mode: http://<phone-ip>:8080/message
            user, pwd = os.getenv("SMSGATE_USER"), os.getenv("SMSGATE_PASSWORD")
            if not (user and pwd):
                return "SMSGATE_USER / SMSGATE_PASSWORD missing"
            url = os.getenv("SMSGATE_URL", "https://api.sms-gate.app/3rdparty/v1/messages").strip()
            r = requests.post(url, json={"textMessage": {"text": body[:700]}, "phoneNumbers": [phone]},
                              auth=(user, pwd), timeout=20)
        elif provider == "textbee":
            # Android phone as the gateway (textbee.dev app): real SMS from your own SIM, no DLT needed
            # for a few person-to-person messages. Free tier: 50/day, 300/month.
            key = os.getenv("TEXTBEE_API_KEY")
            if not key:
                return "TEXTBEE_API_KEY missing"
            device = (os.getenv("TEXTBEE_DEVICE_ID") or "").strip()
            url = (f"https://api.textbee.dev/api/v1/gateway/devices/{device}/send-sms" if device
                   else "https://api.textbee.dev/api/v1/gateway/send-sms")
            r = requests.post(url, json={"recipients": [phone], "message": body[:700]},
                              headers={"x-api-key": key}, timeout=20)
        elif provider == "webhook":
            url = os.getenv("DSX_SMS_WEBHOOK_URL")
            if not url:
                return "DSX_SMS_WEBHOOK_URL missing"
            headers = {"Authorization": f"Bearer {os.getenv('DSX_SMS_WEBHOOK_TOKEN')}"} if os.getenv("DSX_SMS_WEBHOOK_TOKEN") else {}
            r = requests.post(url, json={"to": phone, "message": body}, headers=headers, timeout=15)
        else:
            return f"unknown provider {provider}"
        return None if r.status_code < 300 else f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def deliver_sms(recipient: str, body: str) -> Tuple[int, str]:
    """Outbox + (if allowed) real delivery. Returns (alert_id, outcome) where outcome is
    'delivered', 'outbox' (no gateway, not a phone, or not allow-listed) or 'failed: <reason>'."""
    alert_id = queue_alert("sms", recipient, "SMS Alert", body)
    file_path = OUTBOX_DIR / f"sms_{alert_id}.txt"
    file_path.write_text(f"TO: {recipient}\n\n{body}", encoding="utf-8")
    provider = (os.getenv("DSX_SMS_PROVIDER") or "").strip()
    blocked = sms_gate(recipient) if provider else "no gateway configured"
    if blocked:
        mark_alert_sent(alert_id, "sent", None if not provider else f"outbox only: {blocked}")
        logger.info("SMS to %s written locally (%s)", recipient, blocked)
        return alert_id, "outbox"
    err = _send_sms_via_provider(recipient, body)
    if err is None:
        mark_alert_sent(alert_id, "sent")
        logger.info("SMS delivered via %s to %s", provider, recipient)
        return alert_id, "delivered"
    mark_alert_sent(alert_id, "failed", err)
    logger.warning("SMS gateway failed for %s: %s (kept in outbox %s)", recipient, err, file_path)
    return alert_id, f"failed: {err}"


def send_sms_alert(recipient: str, body: str) -> int:
    """Always writes the SMS to the local outbox (audit trail). If a gateway is
    configured (DSX_SMS_PROVIDER) and the number is allowed, it is also delivered
    for real; otherwise the outbox file is the zero-cost fallback, exactly as before."""
    return deliver_sms(recipient, body)[0]
