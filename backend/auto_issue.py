"""Automatic e-challans for helmet / triple-riding / seat-belt detections.

detected (gate agreed) -> plate read -> plate checked against the registry ->
  verified    : e-challan issued (MV Act fine, repeat-offender policy, SMS, Razorpay link)
  otherwise   : stays with an officer, with the reason recorded

Registry = REGISTERED_VEHICLE in our database, or an RC/Vahan API when one is
configured (backend/registry.py). An unknown owner is never fined automatically.

Records that existed before this module first ran are marked 'preexisting' and
left to officers, so switching it on never mass-issues old detections.
Turn off with DSX_AUTO_ISSUE_RULES=0.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from backend.logger import get_logger
from database import db_manager

logger = get_logger("auto_issue")

DDL = """CREATE TABLE IF NOT EXISTS AUTO_ISSUE (
    rule_violation_id INTEGER PRIMARY KEY,
    outcome TEXT NOT NULL,            -- issued | no_plate | invalid_plate | not_registered | error | preexisting
    plate TEXT, provider TEXT, flags TEXT, notice_id INTEGER, reason TEXT,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""

REASONS = {
    "no_plate": "No number plate was read. An officer must enter it from the snapshot.",
    "invalid_plate": "The plate read is not a valid Indian registration (probably misread). Officer to confirm.",
    "not_registered": "Valid plate, but not found in the vehicle registry. Officer to confirm (needs Vahan).",
}


def enabled() -> bool:
    return os.environ.get("DSX_AUTO_ISSUE_RULES", "1").strip() not in ("0", "false", "no", "off")


def _conn():
    c = db_manager.get_connection()
    fresh = c.execute("SELECT name FROM sqlite_master WHERE name='AUTO_ISSUE'").fetchone() is None
    c.execute(DDL)
    if fresh:
        c.execute("""INSERT OR IGNORE INTO AUTO_ISSUE(rule_violation_id, outcome, reason)
                     SELECT rule_violation_id, 'preexisting', 'Detected before automatic issuing was enabled.'
                     FROM RULE_VIOLATION""")
    c.commit()
    return c


def _issuer(c) -> int:
    env = os.environ.get("DSX_AUTO_ISSUER_ID", "").strip()
    if env.isdigit():
        return int(env)
    r = c.execute("SELECT user_id FROM USER WHERE role='traffic_authority' ORDER BY user_id LIMIT 1").fetchone() \
        or c.execute("SELECT user_id FROM USER ORDER BY user_id LIMIT 1").fetchone()
    return int(r[0]) if r else 1


def _record(rule_violation_id: int, outcome: str, plate: Optional[str] = None, provider: Optional[str] = None,
            flags: Optional[List[str]] = None, notice_id: Optional[int] = None, reason: Optional[str] = None) -> None:
    c = _conn()
    try:
        c.execute("""INSERT OR REPLACE INTO AUTO_ISSUE(rule_violation_id, outcome, plate, provider, flags, notice_id, reason)
                     VALUES (?,?,?,?,?,?,?)""",
                  (rule_violation_id, outcome, plate, provider, ",".join(flags or []) or None, notice_id,
                   reason or REASONS.get(outcome)))
        c.commit()
    finally:
        c.close()


def run_once(registry=None) -> Dict[str, int]:
    """Check every new detected rule violation once. Safe to call often."""
    counts: Dict[str, int] = {}
    if not enabled():
        return counts
    c = _conn()
    try:
        rows = [dict(r) for r in c.execute(
            """SELECT rv.rule_violation_id, rv.plate_text FROM RULE_VIOLATION rv
               LEFT JOIN AUTO_ISSUE a ON a.rule_violation_id = rv.rule_violation_id
               WHERE a.rule_violation_id IS NULL AND rv.status = 'detected'
               ORDER BY rv.rule_violation_id""").fetchall()]
        issuer = _issuer(c)
    finally:
        c.close()  # never hold a connection while issuing (it writes on its own)
    if not rows:
        return counts
    if registry is None:
        from backend.registry import REGISTRY as registry
    from backend import enforcement
    for r in rows:
        rid = r["rule_violation_id"]
        plate = enforcement.plate_key(r.get("plate_text"))
        try:
            if not plate:
                outcome, info = "no_plate", {}
            else:
                info = registry.lookup(plate)
                if not info.get("valid_format"):
                    outcome = "invalid_plate"
                elif not info.get("found"):
                    outcome = "not_registered"
                else:
                    nid = enforcement.issue_rule_challan(rid, issuer, plate=plate)
                    flags = info.get("flags") or []
                    _record(rid, "issued", plate, info.get("provider"), flags, nid,
                            "Plate verified in the registry ({}){}.".format(
                                info.get("provider"), f"; also flagged: {', '.join(flags)}" if flags else ""))
                    counts["issued"] = counts.get("issued", 0) + 1
                    continue
            _record(rid, outcome, plate or None, info.get("provider"), info.get("flags"))
        except Exception as exc:  # one bad record never stops the rest
            logger.exception("Automatic issue failed for rule violation %s", rid)
            _record(rid, "error", plate or None, reason=f"{type(exc).__name__}: {exc}")
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    if counts:
        logger.info("Automatic rule challans: %s", counts)
    return counts


def recent(limit: int = 200) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        return [dict(r) for r in c.execute(
            """SELECT a.rule_violation_id, rv.rule_type, a.outcome, a.plate, a.provider, a.flags, a.notice_id,
                      a.reason, a.checked_at
               FROM AUTO_ISSUE a JOIN RULE_VIOLATION rv ON rv.rule_violation_id = a.rule_violation_id
               WHERE a.outcome != 'preexisting' ORDER BY a.checked_at DESC, a.rule_violation_id DESC LIMIT ?""",
            (limit,)).fetchall()]
    finally:
        c.close()
