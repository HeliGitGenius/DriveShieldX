"""e-Challan SMS to the registered owner, the way Indian e-challan systems notify:
one SMS per challan with the challan number, offence, fine, due date and a payment link.

Exactly once per challan (CHALLAN_SMS table). Challans that existed before this module
first ran are recorded as 'preexisting' and never texted, so switching SMS on does not
spam old records. Delivery goes through backend.alerts.deliver_sms, so the outbox,
the gateway choice and the DSX_SMS_ALLOWLIST safety rule all apply.

Covers speed e-challans (VIOLATION_NOTICE, incl. manual ones) and helmet / triple-riding /
seat-belt fines (RULE_VIOLATION) once an officer has issued them (status 'issued') and the
plate matches a registered vehicle with an owner phone.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from backend.alerts import deliver_sms, normalise_in_phone
from backend.logger import get_logger
from database import db_manager

logger = get_logger("challan_sms")

DDL = """
CREATE TABLE IF NOT EXISTS CHALLAN_SMS (
    kind TEXT NOT NULL CHECK(kind IN ('notice','rule')),
    ref_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    phone_masked TEXT,
    alert_id INTEGER,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (kind, ref_id)
);
"""
RULE_LABEL = {"no_helmet": "Riding without helmet", "three_seater": "Triple riding",
              "no_seatbelt": "Driving without seat belt"}
_last_run = 0.0


def _conn():
    c = db_manager.get_connection()
    from backend.enforcement import ensure_schema
    ensure_schema(c)
    fresh = c.execute("SELECT name FROM sqlite_master WHERE name='CHALLAN_SMS'").fetchone() is None
    c.executescript(DDL)
    if fresh:  # baseline: everything that already exists is never texted
        c.execute("""INSERT OR IGNORE INTO CHALLAN_SMS(kind, ref_id, status)
                     SELECT 'notice', notice_id, 'preexisting' FROM VIOLATION_NOTICE""")
        c.execute("""INSERT OR IGNORE INTO CHALLAN_SMS(kind, ref_id, status)
                     SELECT 'rule', rule_violation_id, 'preexisting' FROM RULE_VIOLATION""")
    c.commit()
    return c


def mask_phone(p: Optional[str]) -> Optional[str]:
    n = normalise_in_phone(p)
    return f"+91XXXXXX{n[-4:]}" if n else None


def _pay_link(notice: Dict[str, Any], amount: float, owner: Dict[str, Any]) -> str:
    base = os.environ.get("DSX_PUBLIC_BASE_URL", "http://localhost:8501").rstrip("/")
    try:
        from backend import razorpay_gateway

        if razorpay_gateway.enabled():
            link = razorpay_gateway.create_payment_link(notice, amount, owner, notify=False)
            if link and link.get("short_url"):
                return link["short_url"]
    except Exception as exc:  # live-mode cap, network, ... -> owner portal link
        logger.info("No gateway link for challan %s (%s); using portal link", notice.get("notice_id"), exc)
    return base


def _pending(c) -> List[Dict[str, Any]]:
    notices = c.execute("""
        SELECT 'notice' AS kind, n.notice_id AS ref_id, n.notice_id, n.plate_number, n.amount, n.late_fee,
               n.due_date, n.issued_at, n.offense_count, n.offense_tier, n.suspension_months, n.violation_kind,
               o.owner_id, o.name, o.email, o.phone,
               sr.speed_value, sr.speed_limit, cam.location
        FROM VIOLATION_NOTICE n
        JOIN OWNER_ACCOUNT o ON o.owner_id = n.owner_id
        LEFT JOIN VIOLATION v ON v.violation_id = n.violation_id
        LEFT JOIN SPEED_RECORD sr ON sr.speed_id = v.speed_id
        LEFT JOIN VEHICLE ve ON ve.vehicle_id = sr.vehicle_id
        LEFT JOIN VIDEO_SESSION vs ON vs.session_id = ve.session_id
        LEFT JOIN CAMERA cam ON cam.camera_id = vs.camera_id
        WHERE n.payment_status IN ('pending','overdue')
          AND NOT EXISTS (SELECT 1 FROM CHALLAN_SMS s WHERE s.kind='notice' AND s.ref_id=n.notice_id)
        ORDER BY n.notice_id LIMIT 50""").fetchall()
    rules = c.execute("""
        SELECT 'rule' AS kind, r.rule_violation_id AS ref_id, r.rule_type, rv.plate_number, r.fine_amount AS amount,
               r.detected_at, o.owner_id, o.name, o.email, o.phone
        FROM RULE_VIOLATION r
        JOIN REGISTERED_VEHICLE rv ON rv.reg_vehicle_id = r.reg_vehicle_id
        JOIN OWNER_ACCOUNT o ON o.owner_id = rv.owner_id
        WHERE r.status = 'issued'
          AND NOT EXISTS (SELECT 1 FROM VIOLATION_NOTICE vn WHERE vn.rule_violation_id = r.rule_violation_id)
          AND NOT EXISTS (SELECT 1 FROM CHALLAN_SMS s WHERE s.kind='rule' AND s.ref_id=r.rule_violation_id)
        ORDER BY r.rule_violation_id LIMIT 50""").fetchall()
    return [dict(r) for r in notices] + [dict(r) for r in rules]


def _fmt_date(v: Any) -> str:
    from datetime import datetime

    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%d-%b-%Y")
    except (TypeError, ValueError):
        return str(v or "-")


def compose(row: Dict[str, Any], link: str) -> str:
    """Owner SMS. Always signed DriveShieldX (never presented as a government message)."""
    if row["kind"] == "notice":
        amount = float(row.get("amount") or 0) + float(row.get("late_fee") or 0)
        from backend.enforcement import RULE_POLICY
        rule = RULE_POLICY.get(row.get("violation_kind") or "")
        if rule:
            offence = f"{rule['label']} ({rule['law']})"
        elif row.get("speed_value") is not None:
            offence = f"Over-speeding ({float(row['speed_value']):.0f} km/h in a {float(row['speed_limit']):.0f} km/h zone)"
        else:
            offence = "Traffic violation"
        where = f" at {row['location']}" if row.get("location") else ""
        strike = ""
        if row.get("offense_count"):
            from backend.enforcement import ordinal
            strike = f"This is your {ordinal(int(row['offense_count']))} offence. "
        if row.get("suspended_until"):
            strike += f"Your driving licence is suspended until {_fmt_date(row['suspended_until'])}. "
        return (f"DriveShieldX: e-Challan #{row['ref_id']} has been issued for vehicle {row['plate_number']} - "
                f"{offence}{where}. Fine: Rs {amount:,.0f}. Due by: {_fmt_date(row.get('due_date'))}. "
                f"{strike}Please log in to your DriveShieldX dashboard to view the evidence and pay your e-Challan, "
                f"or pay securely here: {link} . Ignore if already paid.")
    return (f"DriveShieldX: e-Challan R{row['ref_id']} has been issued for vehicle {row['plate_number']} - "
            f"{RULE_LABEL.get(row['rule_type'], row['rule_type'])}. Fine: Rs {float(row.get('amount') or 0):,.0f}. "
            f"Please log in to your DriveShieldX dashboard to view the evidence and pay your e-Challan: {link} . "
            f"Ignore if already paid.")


def notify_pending(min_interval_s: float = 15.0) -> Dict[str, int]:
    """Send the SMS for every new challan not yet notified. Cheap to call often."""
    global _last_run
    if time.time() - _last_run < min_interval_s:
        return {}
    _last_run = time.time()
    counts = {"delivered": 0, "outbox": 0, "failed": 0, "no_phone": 0}
    try:  # helmet / triple-riding / seat-belt detections with a verified plate become challans
        from backend import auto_issue
        auto_issue.run_once()
    except Exception:
        logger.exception("Automatic rule-challan step failed")
    try:  # fine schedule + licence suspension first, so the SMS states the final amount
        from backend import enforcement
        enforcement.process_new()
    except Exception:
        logger.exception("Repeat-offender policy step failed")
    c = _conn()
    try:
        rows = _pending(c)
    finally:
        c.close()
    base = os.environ.get("DSX_PUBLIC_BASE_URL", "http://localhost:8501").rstrip("/")
    for row in rows:
        phone = normalise_in_phone(row.get("phone"))
        if not phone:
            _record(row, "no_phone", None, None, "owner has no valid mobile number")
            counts["no_phone"] += 1
            continue
        owner = {"name": row.get("name"), "email": row.get("email"), "phone": phone}
        if row["kind"] == "notice":
            try:
                from backend import enforcement
                susp = enforcement.suspension_for_notice(row["notice_id"]) if row.get("suspension_months") else None
                row["suspended_until"] = susp["ends_on"] if susp else None
            except Exception:
                row["suspended_until"] = None
            amount = float(row.get("amount") or 0) + float(row.get("late_fee") or 0)
            link = _pay_link({"notice_id": row["notice_id"], "plate_number": row["plate_number"]}, amount, owner)
        else:
            link = base
        alert_id, outcome = deliver_sms(phone, compose(row, link))
        key = "failed" if outcome.startswith("failed") else outcome
        _record(row, key, phone, alert_id, outcome)
        counts[key] += 1
    if rows:
        logger.info("Challan SMS: %s", counts)
    return counts


def _record(row, status, phone, alert_id, detail) -> None:
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO CHALLAN_SMS(kind, ref_id, status, phone_masked, alert_id, detail) "
                  "VALUES (?,?,?,?,?,?)", (row["kind"], row["ref_id"], status, mask_phone(phone), alert_id, detail))
        c.commit()
    finally:
        c.close()


def recent(limit: int = 100) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM CHALLAN_SMS WHERE status!='preexisting' "
                                           "ORDER BY created_at DESC LIMIT ?", (limit,))]
    finally:
        c.close()
