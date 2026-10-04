"""
Card payment module for DriveShieldX.

IMPORTANT — HONEST SCOPE (read this before wiring up a real gateway):
Collecting a raw card number (PAN), CVV and expiry on your own server and sending it to a
bank yourself is not something you are legally allowed to do without PCI-DSS certification.
Every real product routes card entry through a gateway's own hosted field / SDK (Razorpay
Checkout, Stripe Elements, PayU Bolt, etc.) so the raw card number never touches your
backend — you only ever see a tokenised `payment_id` that you verify server-side.

So, exactly like backend/payments.py does for UPI, this module models the *shape* of that
flow rather than pretending to move money:
  1. `validate_card()` — client-side sanity checks only (Luhn checksum, expiry, CVV length).
     This is the same validation a checkout form does before it ever calls the gateway.
  2. `mask_card()` — never store/display/log a full PAN. Only the brand + last 4 digits.
  3. `gateway_simulate_charge()` — stands in for the gateway's `charge.create()` /
     `payment.capture()` call and returns a gateway-style payment id + auth code, the same
     shape Razorpay/Stripe would hand back in their webhook/response.

Swapping in a real gateway later means: replace card entry with the gateway's hosted
fields/SDK, replace `gateway_simulate_charge()` with the gateway's server-side capture +
signature verification, and keep everything downstream (mark_paid / DB update / receipt /
notification) exactly as-is — that part already matches what a real webhook handler does.
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime
from typing import Optional, Tuple

# A handful of well-known test PAN prefixes, purely so the demo can label a brand.
_BRAND_PREFIXES = (
    ("Visa", re.compile(r"^4")),
    ("Mastercard", re.compile(r"^(5[1-5]|2[2-7])")),
    ("RuPay", re.compile(r"^(60|65|81|82|508)")),
    ("Amex", re.compile(r"^3[47]")),
)


def detect_brand(card_number: str) -> str:
    digits = re.sub(r"\D", "", card_number)
    for brand, pattern in _BRAND_PREFIXES:
        if pattern.match(digits):
            return brand
    return "Card"


def _luhn_ok(digits: str) -> bool:
    total = 0
    reversed_digits = digits[::-1]
    for i, ch in enumerate(reversed_digits):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def validate_card(card_number: str, expiry: str, cvv: str, holder_name: str) -> Tuple[bool, str]:
    """Client-side-style validation only — the same checks a checkout form runs before
    it ever talks to a gateway. Returns (is_valid, error_message)."""
    digits = re.sub(r"\D", "", card_number or "")
    if not holder_name or not holder_name.strip():
        return False, "Enter the name on the card."
    if len(digits) < 12 or len(digits) > 19:
        return False, "Card number looks too short/long."
    if not _luhn_ok(digits):
        return False, "That card number doesn't check out — re-check the digits."
    m = re.match(r"^(0[1-9]|1[0-2])\s*/\s*(\d{2}|\d{4})$", (expiry or "").strip())
    if not m:
        return False, "Expiry must be in MM/YY format."
    month = int(m.group(1))
    year_raw = m.group(2)
    year = int(year_raw) if len(year_raw) == 4 else 2000 + int(year_raw)
    now = datetime.now()
    if (year, month) < (now.year, now.month):
        return False, "That card has expired."
    cvv_len = 4 if detect_brand(card_number) == "Amex" else 3
    if not re.match(rf"^\d{{{cvv_len}}}$", (cvv or "").strip()):
        return False, f"CVV must be {cvv_len} digits."
    return True, ""


def mask_card(card_number: str) -> str:
    digits = re.sub(r"\D", "", card_number or "")
    brand = detect_brand(card_number)
    last4 = digits[-4:] if len(digits) >= 4 else digits
    return f"{brand} •••• {last4}"


def new_txn_ref(notice_id: int) -> str:
    return f"DSXCARD{datetime.now():%Y%m%d}{notice_id:05d}{random.randint(100, 999)}"


def gateway_simulate_charge(amount: float, masked_card: str, txn_ref: str) -> Tuple[bool, str]:
    """Stand-in for a payment gateway's server-side charge/capture call. A real
    integration would call e.g. Razorpay's `orders.create()` then verify the signature
    on the client's `payment_id` here. We simulate a successful capture and return a
    gateway-style payment id + auth code, the same shape a real capture response has."""
    time.sleep(0.5)  # mimic network round-trip to the card network
    gateway_payment_id = f"pay_card_{random.randint(10**11, 10**12 - 1)}"
    auth_code = f"{random.randint(100000, 999999)}"
    return True, gateway_payment_id + f"/auth{auth_code}"