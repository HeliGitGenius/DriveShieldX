"""Storage for online (Razorpay) payments. One row per payment link; the
`mark_link_paid` update is conditional, which is what makes settlement
idempotent across the redirect, webhook and reconcile paths."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from database import db_manager

DDL = """
CREATE TABLE IF NOT EXISTS PAYMENT_LINK (
    link_id TEXT PRIMARY KEY,
    notice_id INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('test','live')),
    reference_id TEXT NOT NULL,
    short_url TEXT,
    amount_paise INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    payment_id TEXT,
    method TEXT,
    settled_via TEXT,
    refund_id TEXT,
    refund_status TEXT,
    raw_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    paid_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_payment_link_notice ON PAYMENT_LINK(notice_id);
"""
_READY = set()


def conn():
    c = db_manager.get_connection()
    key = str(db_manager.DB_PATH)
    if key not in _READY:
        c.executescript(DDL)
        c.commit()
        _READY.add(key)
    return c


def insert_link(notice_id, mode, link_id, reference_id, short_url, amount_paise, status, raw) -> None:
    c = conn()
    try:
        c.execute("""INSERT OR IGNORE INTO PAYMENT_LINK(link_id, notice_id, mode, reference_id, short_url,
                     amount_paise, status, raw_json) VALUES (?,?,?,?,?,?,?,?)""",
                  (link_id, notice_id, mode, reference_id, short_url, amount_paise, status, json.dumps(raw)))
        c.commit()
    finally:
        c.close()


def get_link(link_id: str) -> Optional[Dict[str, Any]]:
    c = conn()
    try:
        r = c.execute("SELECT * FROM PAYMENT_LINK WHERE link_id=?", (link_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def open_link_for(notice_id: int, mode: str, amount_paise: int) -> Optional[Dict[str, Any]]:
    """Reuse an unpaid, unexpired link for the same challan and amount."""
    c = conn()
    try:
        r = c.execute("""SELECT * FROM PAYMENT_LINK WHERE notice_id=? AND mode=? AND amount_paise=?
                         AND status IN ('created','issued','partially_paid') ORDER BY created_at DESC LIMIT 1""",
                      (notice_id, mode, amount_paise)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def open_links(notice_ids: Optional[list] = None) -> List[Dict[str, Any]]:
    c = conn()
    try:
        rows = c.execute("SELECT * FROM PAYMENT_LINK WHERE status IN ('created','issued','partially_paid')").fetchall()
        rows = [dict(r) for r in rows]
        return [r for r in rows if notice_ids is None or r["notice_id"] in set(notice_ids)]
    finally:
        c.close()


def list_links(limit: int = 200) -> List[Dict[str, Any]]:
    c = conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM PAYMENT_LINK ORDER BY created_at DESC LIMIT ?", (limit,))]
    finally:
        c.close()


def update_link_status(link_id: str, status: str) -> None:
    c = conn()
    try:
        c.execute("UPDATE PAYMENT_LINK SET status=? WHERE link_id=? AND status!='paid'", (status, link_id))
        c.commit()
    finally:
        c.close()


def mark_link_paid(link_id: str, payment_id: str, method: Optional[str], source: str) -> bool:
    """Returns True only for the FIRST caller (conditional update)."""
    c = conn()
    try:
        cur = c.execute("""UPDATE PAYMENT_LINK SET status='paid', payment_id=?, method=?, settled_via=?,
                           paid_at=CURRENT_TIMESTAMP WHERE link_id=? AND payment_id IS NULL""",
                        (payment_id, method, source, link_id))
        c.commit()
        return cur.rowcount == 1
    finally:
        c.close()


def mark_refunded(link_id: str, refund_id: Optional[str], refund_status: Optional[str]) -> None:
    c = conn()
    try:
        c.execute("UPDATE PAYMENT_LINK SET status='refunded', refund_id=?, refund_status=? WHERE link_id=?",
                  (refund_id, refund_status, link_id))
        c.commit()
    finally:
        c.close()


def notice_contact(notice_id: int) -> Dict[str, Any]:
    c = conn()
    try:
        r = c.execute("""SELECT n.plate_number, o.email, o.phone FROM VIOLATION_NOTICE n
                         LEFT JOIN OWNER_ACCOUNT o ON o.owner_id = n.owner_id WHERE n.notice_id=?""",
                      (notice_id,)).fetchone()
        return dict(r) if r else {}
    finally:
        c.close()
