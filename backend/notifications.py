"""
Payment-confirmation notifications for DriveShieldX.

Reuses the existing alert channel (backend/alerts.py) that the rest of the app already
uses for challan-issued notices — same email/SMS plumbing, just a different message.

Email: if OVERSPEED_SMTP_* env vars are configured, a real email is sent via SMTP.
Otherwise (default, no setup needed) it's written to logs/alert_outbox/email_<id>.txt
so you can see exactly what would have been sent.

SMS: no SMS gateway is configured by default (a real one — Twilio, MSG91, etc. — needs a
paid account + sender ID approval), so this always writes to
logs/alert_outbox/sms_<id>.txt in the same way. Wiring a real provider is a small,
isolated change — see send_sms_alert() in backend/alerts.py for exactly where to add it.
"""
from __future__ import annotations

from typing import Optional

from backend.alerts import send_email_alert, send_sms_alert
from backend.logger import get_logger

logger = get_logger("notifications")


def send_payment_confirmation(
    notice_id: int,
    plate_number: str,
    amount: float,
    method: str,
    txn_ref: str,
    owner_email: Optional[str] = None,
    owner_phone: Optional[str] = None,
) -> dict:
    """Send a payment-success email + SMS. Safe to call even if email/phone are missing —
    it just skips that channel. Returns which channels were actually sent to."""
    subject = f"DriveShieldX · Payment received for challan #{notice_id}"
    body = (
        f"Your payment of ₹{amount:,.0f} for e-challan #{notice_id} "
        f"(vehicle {plate_number}) was successful.\n"
        f"Payment method: {method}\n"
        f"Transaction reference: {txn_ref}\n\n"
        f"This challan is now marked PAID and will no longer accrue late fees. "
        f"You can view/download your receipt any time from the DriveShieldX dashboard "
        f"under Payment history."
    )
    sms_body = (
        f"DriveShieldX: Payment of Rs.{amount:,.0f} received for challan #{notice_id} "
        f"({plate_number}). Ref: {txn_ref}. Status: PAID."
    )

    sent = {"email": False, "sms": False}
    if owner_email:
        try:
            send_email_alert(owner_email, subject, body)
            sent["email"] = True
        except Exception:
            logger.exception("Payment confirmation email failed for notice %s", notice_id)
    if owner_phone:
        try:
            send_sms_alert(owner_phone, sms_body)
            sent["sms"] = True
        except Exception:
            logger.exception("Payment confirmation SMS failed for notice %s", notice_id)
    return sent