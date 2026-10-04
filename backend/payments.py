"""
Payment module for DriveShieldX.

IMPORTANT — HONEST SCOPE:
This module builds a REAL UPI payment request (the `upi://pay?...` deep link that
Google Pay / PhonePe / Paytm actually understand) and renders it as a scannable QR.
If you configure a genuine payee UPI ID (PAYEE_VPA below) and scan the QR with a real
UPI app, the app WILL open a real payment screen to that ID.

What it does NOT do (and legally cannot, without a registered business + payment-gateway
merchant account + server-side secret keys): automatically confirm that money was received.
Confirming receipt requires a payment gateway webhook (Razorpay/Paytm/PhonePe), which needs
KYC and a company. So this module models the settlement step as a verifiable transaction
record (PENDING -> SUCCESS) with a transaction id and receipt, the same shape a real gateway
callback would give you. Swapping in a real gateway later means replacing `mark_paid()` with
the gateway's verify call — everything else stays the same.
"""
from __future__ import annotations

import io
import random
import time
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import quote

import os

from backend.env_loader import load_env

load_env()

# Payee details come ONLY from .env (UPI_ID / UPI_PAYEE_NAME / CONTACT_PHONE); no personal
# UPI ID or phone number is hard-coded in the source.
PAYEE_VPA = os.environ.get("UPI_ID", "")
PAYEE_NAME = os.environ.get("UPI_PAYEE_NAME", "DriveShieldX Traffic Authority")
CONTACT_PHONE = os.environ.get("CONTACT_PHONE", "")


def build_upi_uri(amount: float, note: str, txn_ref: str) -> str:
    """Construct a standards-compliant UPI deep link (NPCI UPI URI spec).
    Real UPI apps parse exactly this format from a QR code."""
    params = (
        f"pa={quote(PAYEE_VPA)}"
        f"&pn={quote(PAYEE_NAME)}"
        f"&am={amount:.2f}"
        f"&cu=INR"
        f"&tn={quote(note)}"
        f"&tr={quote(txn_ref)}"
    )
    return f"upi://pay?{params}"


def new_txn_ref(notice_id: int) -> str:
    """A unique transaction reference, like a real order id."""
    return f"DSX{datetime.now():%Y%m%d}{notice_id:05d}{random.randint(100, 999)}"


def qr_png_bytes(data: str, scale: int = 8) -> Optional[bytes]:
    """Render `data` as a QR PNG. Tries the `qrcode` library; returns None if it
    isn't installed so the UI can fall back to showing the UPI string/text."""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
        qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=scale, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0b0b0d", back_color="#FFD27D")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def gateway_simulate_verify(txn_ref: str) -> Tuple[bool, str]:
    """Stand-in for a payment-gateway verification call. A real integration would
    call e.g. Razorpay's `payment.fetch(payment_id)` here and check status == 'captured'.
    We simulate a successful capture and return a gateway-style payment id."""
    time.sleep(0.4)  # mimic network round-trip
    gateway_payment_id = f"pay_{random.randint(10**11, 10**12 - 1)}"
    return True, gateway_payment_id
