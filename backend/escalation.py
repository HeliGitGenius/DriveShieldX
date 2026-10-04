"""Unpaid e-challan escalation, following India's e-challan procedure.

Timeline per challan (all counted from the issue date; every value configurable):

  day 7, 13   reminder SMS with a fresh payment link
  due date    overdue: fine rises to the higher amount (db_manager.apply_overdue_penalties) + SMS
  day 0-45    the owner may CONTEST the challan online. While a contest is open the clock stops
              and the challan does not count as a strike.
                accepted  -> challan cancelled ('waived')
                rejected  -> pay within 30 days of the decision, or appeal in court
  day 75      (45 + 30) still unpaid -> vehicle marked NOT TO BE TRANSACTED (hotlist):
              RC / licence services blocked until all dues are cleared; owner told by SMS.
              After a rejected contest: decision + 30 days + 15 days grace.
  day 90      still unpaid -> listed for referral to the virtual court (report for the officer).
  any time    a hotlisted plate read by any DriveShieldX camera raises a live "wanted vehicle"
              alert for the officer (and an SMS to the control-room phone, if allow-listed).
  5 / year    five or more challans in a year suspend the licence (backend/enforcement.py).

Nothing is ever deducted from anyone's bank account: under Indian law that needs a statute or
the person's consent. Paying clears the hotlist automatically.

DSX_ESCALATION_DAY_SECONDS (default 86400) can be lowered for a demo (e.g. 60 = one "day" per
minute); the dashboard then shows a DEMO TIME banner so nobody mistakes it for real time.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backend.logger import get_logger
from database import db_manager

logger = get_logger("escalation")

DDL = """
CREATE TABLE IF NOT EXISTS ESCALATION_EVENT (
    notice_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (notice_id, stage)
);
CREATE TABLE IF NOT EXISTS VEHICLE_HOTLIST (
    plate_key TEXT PRIMARY KEY,
    owner_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cleared')),
    reason TEXT,
    notice_ids TEXT,
    amount_due REAL,
    listed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cleared_at TIMESTAMP,
    cleared_reason TEXT
);
CREATE TABLE IF NOT EXISTS HOTLIST_SIGHTING (
    sighting_id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate_key TEXT NOT NULL,
    camera_id INTEGER,
    session_id INTEGER,
    vehicle_id INTEGER UNIQUE,
    seen_at TEXT,
    acknowledged_by TEXT,
    acknowledged_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS CHALLAN_DISPUTE (
    dispute_id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,
    owner_id INTEGER,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','accepted','rejected')),
    filed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_by TEXT,
    decided_at TIMESTAMP,
    decision_note TEXT,
    prior_status TEXT
);
CREATE TABLE IF NOT EXISTS ESCALATION_STATE (k TEXT PRIMARY KEY, v TEXT);
"""
_last_run = 0.0


# ------------------------------------------------------------------ settings
def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def settings() -> Dict[str, float]:
    contest = _f("DSX_CONTEST_DAYS", 45)
    pay = _f("DSX_PAY_AFTER_CONTEST_DAYS", 30)
    return {"day_seconds": _f("DSX_ESCALATION_DAY_SECONDS", 86400), "contest_days": contest, "pay_days": pay,
            "grace_after_rejection": _f("DSX_GRACE_AFTER_REJECTION_DAYS", 15),
            "hotlist_after": contest + pay, "court_after": _f("DSX_COURT_AFTER_DAYS", contest + pay + 15),
            "reminders": [float(x) for x in os.environ.get("DSX_REMINDER_DAYS", "7,13").split(",") if x.strip()]}


def demo_mode() -> bool:
    return settings()["day_seconds"] < 86400


def _parse(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    s = str(ts).replace("T", " ")
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None


_demo_since: Optional[datetime] = None


def _sync_demo_clock(c) -> None:
    """Demo time only applies to challans issued after demo time was switched on; everything
    older keeps ageing in real days, so turning demo time on never escalates real records."""
    global _demo_since
    if demo_mode():
        r = c.execute("SELECT v FROM ESCALATION_STATE WHERE k='demo_since'").fetchone()
        if r is None:
            now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
            c.execute("INSERT INTO ESCALATION_STATE(k, v) VALUES ('demo_since', ?)", (now,))
            c.commit()
            r = (now,)
        _demo_since = _parse(r[0])
    else:
        c.execute("DELETE FROM ESCALATION_STATE WHERE k='demo_since'")
        c.commit()
        _demo_since = None


def days_since(ts: Any, now: Optional[datetime] = None) -> float:
    """issued_at / decided_at are stored by SQLite CURRENT_TIMESTAMP (UTC)."""
    t = _parse(ts)
    if t is None:
        return 0.0
    now = now or datetime.utcnow()
    if demo_mode() and (_demo_since is None or t < _demo_since):
        return (now - t).total_seconds() / 86400.0       # pre-demo records age in real days
    return (now - t).total_seconds() / settings()["day_seconds"]


# ------------------------------------------------------------------ db
def _conn():
    from backend.enforcement import ensure_schema

    c = db_manager.get_connection()
    ensure_schema(c)
    c.executescript(DDL)
    c.commit()
    return c


def _plate_key(p: Optional[str]) -> str:
    from backend.enforcement import plate_key

    return plate_key(p)


# ------------------------------------------------------------------ owner actions
def can_contest(notice: Dict[str, Any]) -> Tuple[bool, str]:
    if notice.get("payment_status") not in ("pending", "overdue"):
        return False, "Only unpaid challans can be contested."
    left = settings()["contest_days"] - days_since(notice.get("issued_at"))
    if left < 0:
        return False, "The 45-day contest period has ended."
    c = _conn()
    try:
        if c.execute("SELECT 1 FROM CHALLAN_DISPUTE WHERE notice_id=?", (notice["notice_id"],)).fetchone():
            return False, "This challan has already been contested."
    finally:
        c.close()
    return True, f"{left:.0f} day(s) left to contest."


def file_dispute(notice_id: int, owner_id: int, reason: str) -> int:
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValueError("Please describe the reason (at least 10 characters).")
    c = _conn()
    try:
        n = c.execute("SELECT * FROM VIOLATION_NOTICE WHERE notice_id=? AND owner_id=?", (notice_id, owner_id)).fetchone()
        if n is None:
            raise ValueError("Challan not found for this account.")
        n = dict(n)
    finally:
        c.close()
    c = _conn()
    try:
        _sync_demo_clock(c)
    finally:
        c.close()
    ok, why = can_contest(n)
    if not ok:
        raise ValueError(why)
    c = _conn()
    try:
        did = c.execute("INSERT INTO CHALLAN_DISPUTE(notice_id, owner_id, reason, prior_status) VALUES (?,?,?,?)",
                        (notice_id, owner_id, reason, n["payment_status"])).lastrowid
        c.execute("UPDATE VIOLATION_NOTICE SET payment_status='disputed' WHERE notice_id=?", (notice_id,))
        c.commit()
    finally:
        c.close()
    db_manager.log_system_event("dispute", f"Challan #{notice_id} contested by owner {owner_id}")
    return int(did)


def decide_dispute(dispute_id: int, accept: bool, officer: str, note: str) -> Dict[str, Any]:
    note = (note or "").strip()
    if not note:
        raise ValueError("A decision note is required.")
    c = _conn()
    try:
        d = c.execute("SELECT * FROM CHALLAN_DISPUTE WHERE dispute_id=? AND status='submitted'", (dispute_id,)).fetchone()
        if d is None:
            raise ValueError("Dispute not found or already decided.")
        d = dict(d)
        status = "accepted" if accept else "rejected"
        c.execute("""UPDATE CHALLAN_DISPUTE SET status=?, decided_by=?, decided_at=CURRENT_TIMESTAMP, decision_note=?
                     WHERE dispute_id=?""", (status, officer, note, dispute_id))
        new = "waived" if accept else (d.get("prior_status") or "pending")
        c.execute("UPDATE VIOLATION_NOTICE SET payment_status=? WHERE notice_id=?", (new, d["notice_id"]))
        c.commit()
    finally:
        c.close()
    if accept:  # a cancelled challan must not keep a licence suspension it triggered
        _undo_suspension_from(d["notice_id"], officer, f"Challan #{d['notice_id']} cancelled on contest: {note}")
    pay_by = (datetime.utcnow() + timedelta(seconds=settings()["pay_days"] * settings()["day_seconds"]))
    msg = (f"DriveShieldX: Your contest of e-Challan #{d['notice_id']} has been ACCEPTED and the challan is cancelled. "
           f"Officer's note: {note}") if accept else (
        f"DriveShieldX: Your contest of e-Challan #{d['notice_id']} was REJECTED ({note}). Please pay within "
        f"{settings()['pay_days']:.0f} days (by {pay_by:%d-%b-%Y}) or file an appeal in court; otherwise RC and "
        f"licence services will be blocked. Pay on your DriveShieldX dashboard.")
    _sms_owner(d["notice_id"], msg)
    db_manager.log_system_event("dispute", f"Dispute {dispute_id} on challan #{d['notice_id']} {status} by {officer}")
    return {"status": status, "notice_id": d["notice_id"]}


def _undo_suspension_from(notice_id: int, officer: str, reason: str) -> None:
    try:
        from backend import enforcement

        c = _conn()
        try:
            r = c.execute("SELECT action_id FROM LICENCE_ACTION WHERE notice_id=? AND status='active'",
                          (notice_id,)).fetchone()
        finally:
            c.close()
        if r:
            enforcement.lift(int(r[0]), officer, reason)
    except Exception:
        logger.exception("Could not lift suspension for cancelled challan %s", notice_id)


# ------------------------------------------------------------------ messaging
def _owner_phone(c, notice_id: int) -> Optional[str]:
    r = c.execute("""SELECT o.phone FROM VIOLATION_NOTICE n JOIN OWNER_ACCOUNT o ON o.owner_id=n.owner_id
                     WHERE n.notice_id=?""", (notice_id,)).fetchone()
    return r[0] if r else None


def _sms_owner(notice_id: int, text: str) -> None:
    from backend.alerts import deliver_sms, normalise_in_phone

    c = _conn()
    try:
        phone = normalise_in_phone(_owner_phone(c, notice_id))
    finally:
        c.close()
    if phone:
        deliver_sms(phone, text)


def _pay_link(n: Dict[str, Any]) -> str:
    base = os.environ.get("DSX_PUBLIC_BASE_URL", "http://localhost:8501").rstrip("/")
    try:
        from backend import razorpay_gateway

        if razorpay_gateway.enabled():
            amount = float(n.get("amount") or 0) + float(n.get("late_fee") or 0)
            link = razorpay_gateway.create_payment_link({"notice_id": n["notice_id"], "plate_number": n.get("plate_number")},
                                                        amount, None, notify=False)
            if link and link.get("short_url"):
                return link["short_url"]
    except Exception as exc:
        logger.info("No gateway link for challan %s (%s)", n.get("notice_id"), exc)
    return base


def _control_room(camera_id: Optional[int]) -> Optional[str]:
    try:
        from database import safety_db

        geo = safety_db.get_camera_geo(int(camera_id or 1)) or {}
        return geo.get("control_room_phone") or os.environ.get("DSX_CONTROL_ROOM_PHONE")
    except Exception:
        return os.environ.get("DSX_CONTROL_ROOM_PHONE")


# ------------------------------------------------------------------ the engine
def run_once(force: bool = False, min_interval_s: float = 20.0) -> Dict[str, int]:
    """One escalation pass. Idempotent: every stage happens once per challan."""
    global _last_run
    if not force and time.time() - _last_run < min_interval_s:
        return {}
    _last_run = time.time()
    try:
        db_manager.apply_overdue_penalties()
    except Exception:
        logger.exception("Overdue sweep failed")
    s = settings()
    counts = {"reminders": 0, "overdue": 0, "hotlisted": 0, "court": 0, "cleared": 0, "sightings": 0}
    outbox: List[Tuple[str, Any, str]] = []   # (kind, notice/plate/camera, text) sent after commit
    c = _conn()
    try:
        _sync_demo_clock(c)
        # First pass ever: record what is already due for existing challans WITHOUT messaging anyone,
        # so switching this on never sends a burst of old reminders.
        silent = c.execute("SELECT v FROM ESCALATION_STATE WHERE k='baselined'").fetchone() is None
        done = {(r[0], r[1]) for r in c.execute("SELECT notice_id, stage FROM ESCALATION_EVENT")}
        decided = {r[0]: r[1] for r in c.execute(
            "SELECT notice_id, decided_at FROM CHALLAN_DISPUTE WHERE status='rejected'")}
        unpaid = [dict(r) for r in c.execute(
            """SELECT * FROM VIOLATION_NOTICE WHERE payment_status IN ('pending','overdue')
               AND COALESCE(plate_number,'') != ''""")]   # unregistered plates are hotlisted too

        def mark(nid: int, stage: str, detail: str = "") -> bool:
            if (nid, stage) in done:
                return False
            c.execute("INSERT OR IGNORE INTO ESCALATION_EVENT(notice_id, stage, detail) VALUES (?,?,?)",
                      (nid, stage, detail))
            done.add((nid, stage))
            return True

        hot: Dict[str, Dict[str, Any]] = {}
        for n in unpaid:
            nid, age = n["notice_id"], days_since(n.get("issued_at"))
            amount = float(n.get("amount") or 0) + float(n.get("late_fee") or 0)
            for d in s["reminders"]:
                if age >= d and mark(nid, f"reminder_{d:g}"):
                    outbox.append(("owner", n, f"DriveShieldX: Reminder - e-Challan #{nid} for vehicle {n['plate_number']} "
                                               f"(Rs {amount:,.0f}) is unpaid. Due by: {n.get('due_date')}. Pay or contest it "
                                               f"on your DriveShieldX dashboard: {{link}}"))
                    counts["reminders"] += 1
            if n["payment_status"] == "overdue" and mark(nid, "overdue"):
                outbox.append(("owner", n, f"DriveShieldX: e-Challan #{nid} for vehicle {n['plate_number']} is overdue. "
                                           f"The fine is now Rs {amount:,.0f}. Pay now to avoid RC and licence services "
                                           f"being blocked: {{link}}"))
                counts["overdue"] += 1
            if nid in decided:
                limit = days_since(decided[nid]) - s["pay_days"] - s["grace_after_rejection"]
                ntbt_due, court_due = limit >= 0, limit >= (s["court_after"] - s["hotlist_after"])
            else:
                ntbt_due, court_due = age >= s["hotlist_after"], age >= s["court_after"]
            if ntbt_due:
                pk = _plate_key(n["plate_number"])
                h = hot.setdefault(pk, {"owner_id": n["owner_id"], "notices": [], "due": 0.0})
                h["notices"].append(nid)
                h["due"] += amount
                if mark(nid, "ntbt"):
                    outbox.append(("owner", n, f"DriveShieldX: e-Challan #{nid} (Rs {amount:,.0f}) is unpaid past the "
                                               f"legal period. Vehicle {n['plate_number']} is now marked NOT TO BE "
                                               f"TRANSACTED: RC and driving-licence services are blocked until all dues "
                                               f"are cleared. Pay: {{link}}"))
                    counts["hotlisted"] += 1
            if court_due and mark(nid, "court"):
                outbox.append(("owner", n, f"DriveShieldX: Unpaid e-Challan #{nid} (vehicle {n['plate_number']}) has been "
                                           f"referred to the virtual court. You can still pay online: {{link}}"))
                counts["court"] += 1

        for pk, h in hot.items():
            c.execute("""INSERT INTO VEHICLE_HOTLIST(plate_key, owner_id, status, reason, notice_ids, amount_due)
                         VALUES (?,?, 'active', ?, ?, ?)
                         ON CONFLICT(plate_key) DO UPDATE SET status='active', notice_ids=excluded.notice_ids,
                         amount_due=excluded.amount_due, reason=excluded.reason,
                         cleared_at=NULL, cleared_reason=NULL""",
                      (pk, h["owner_id"], "Unpaid e-challan(s) past the legal period",
                       ",".join(map(str, h["notices"])), h["due"]))
        for r in c.execute("SELECT plate_key FROM VEHICLE_HOTLIST WHERE status='active'").fetchall():
            if r[0] not in hot:
                c.execute("""UPDATE VEHICLE_HOTLIST SET status='cleared', cleared_at=CURRENT_TIMESTAMP,
                             cleared_reason='All dues paid or cancelled' WHERE plate_key=?""", (r[0],))
                counts["cleared"] += 1

        # live "wanted vehicle" alerts from plates read by any camera
        cursor = c.execute("SELECT v FROM ESCALATION_STATE WHERE k='sighting_cursor'").fetchone()
        cur_ts = cursor[0] if cursor else datetime.now().isoformat()
        active = {r[0]: dict(r) for r in c.execute("SELECT * FROM VEHICLE_HOTLIST WHERE status='active'")}
        newest = cur_ts
        if active:
            for v in c.execute("""SELECT ve.vehicle_id, ve.plate_text, ve.session_id, ve.last_seen_time, vs.camera_id
                                  FROM VEHICLE ve LEFT JOIN VIDEO_SESSION vs ON vs.session_id=ve.session_id
                                  WHERE ve.plate_text IS NOT NULL AND ve.last_seen_time > ?""", (cur_ts,)).fetchall():
                newest = max(newest, v[3] or newest)
                pk = _plate_key(v[1])
                if pk in active:
                    ins = c.execute("""INSERT OR IGNORE INTO HOTLIST_SIGHTING(plate_key, camera_id, session_id,
                                       vehicle_id, seen_at) VALUES (?,?,?,?,?)""", (pk, v[4], v[2], v[0], v[3]))
                    if ins.rowcount:
                        counts["sightings"] += 1
                        outbox.append(("control", v[4], f"DriveShieldX ALERT: hotlisted vehicle {pk} (Rs "
                                                        f"{active[pk]['amount_due']:,.0f} unpaid, NOT TO BE TRANSACTED) "
                                                        f"seen at camera {v[4]} at {str(v[3])[:19]}."))
        else:
            r = c.execute("SELECT MAX(last_seen_time) FROM VEHICLE").fetchone()
            newest = max(newest, r[0] or newest)
        c.execute("INSERT INTO ESCALATION_STATE(k, v) VALUES ('sighting_cursor', ?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (newest,))
        if silent:
            c.execute("INSERT OR IGNORE INTO ESCALATION_STATE(k, v) VALUES ('baselined', ?)",
                      (datetime.utcnow().isoformat(),))
            outbox = []
            logger.info("Escalation baseline recorded silently: %s", counts)
        c.commit()
    finally:
        c.close()

    from backend.alerts import deliver_sms
    for kind, ref, text in outbox:
        try:
            if kind == "owner":
                _sms_owner(ref["notice_id"], text.replace("{link}", _pay_link(ref)))
            else:
                phone = _control_room(ref)
                if phone:
                    deliver_sms(phone, text)
                db_manager.queue_alert("dashboard", "traffic-control-room", "Hotlisted vehicle sighted", text)
        except Exception:
            logger.exception("Escalation message failed")
    if any(counts.values()):
        logger.info("Escalation pass: %s", counts)
    return counts


# ------------------------------------------------------------------ queries for the dashboard
def _rows(sql: str, params=()) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        return [dict(r) for r in c.execute(sql, params)]
    finally:
        c.close()


def hotlist(active_only: bool = True) -> List[Dict[str, Any]]:
    return _rows("SELECT * FROM VEHICLE_HOTLIST" + (" WHERE status='active'" if active_only else "")
                 + " ORDER BY listed_at DESC")


def is_hotlisted(plate: Optional[str]) -> Optional[Dict[str, Any]]:
    r = _rows("SELECT * FROM VEHICLE_HOTLIST WHERE plate_key=? AND status='active'", (_plate_key(plate),))
    return r[0] if r else None


def sightings(unacknowledged_only: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    return _rows("SELECT * FROM HOTLIST_SIGHTING" + (" WHERE acknowledged_at IS NULL" if unacknowledged_only else "")
                 + " ORDER BY created_at DESC LIMIT ?", (limit,))


def acknowledge_sighting(sighting_id: int, officer: str) -> None:
    c = _conn()
    try:
        c.execute("UPDATE HOTLIST_SIGHTING SET acknowledged_by=?, acknowledged_at=CURRENT_TIMESTAMP "
                  "WHERE sighting_id=?", (officer, sighting_id))
        c.commit()
    finally:
        c.close()


def disputes(status: Optional[str] = None) -> List[Dict[str, Any]]:
    return _rows("""SELECT d.*, n.plate_number, n.amount, n.violation_kind FROM CHALLAN_DISPUTE d
                    JOIN VIOLATION_NOTICE n ON n.notice_id=d.notice_id"""
                 + (" WHERE d.status=?" if status else "") + " ORDER BY d.filed_at DESC", (status,) if status else ())


def court_referrals() -> List[Dict[str, Any]]:
    return _rows("""SELECT n.notice_id, n.plate_number, n.violation_kind, n.issued_at, n.due_date, n.amount,
                           n.late_fee, n.payment_status, o.name AS owner_name, o.phone AS owner_phone,
                           e.created_at AS referred_at
                    FROM ESCALATION_EVENT e JOIN VIOLATION_NOTICE n ON n.notice_id=e.notice_id
                    LEFT JOIN OWNER_ACCOUNT o ON o.owner_id=n.owner_id
                    WHERE e.stage='court' AND n.payment_status IN ('pending','overdue')
                    ORDER BY e.created_at DESC""")


def timeline(notice_id: int) -> List[Dict[str, Any]]:
    return _rows("SELECT stage, detail, created_at FROM ESCALATION_EVENT WHERE notice_id=? ORDER BY created_at",
                 (notice_id,))
