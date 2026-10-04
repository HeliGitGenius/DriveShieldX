"""Repeat-offender policy, enforced end to end.

Every new e-challan (detected or manual) is processed exactly once:

  1. Offence number = earlier challans of the same licence holder (all their registered
     vehicles; the plate if the owner is unknown) inside DSX_STRIKE_WINDOW_DAYS (default
     365), not counting waived or disputed ones, plus one.
  2. Statutory fine from the schedule (database.db_manager.FINE_SCHEDULE):
        1st offence  Rs 500   (Rs 1000 if overdue)  warning
        2nd offence  Rs 1000  (Rs 2000 if overdue)
        3rd+ offence Rs 3000  (Rs 5000 if overdue)  + licence suspension
     A different amount typed by an officer is replaced and noted on the challan
     (fines follow the schedule, not the person issuing them).
  3. 3rd+ offence: a LICENCE_ACTION suspension is recorded for the owner, starting today,
     3 months for the 3rd offence and one more month for each further one. An existing
     active suspension is extended, never shortened.
  4. A new offence while the licence is suspended is flagged "driving while suspended".

Suspensions expire automatically on their end date; an officer can lift one early with a
reason (audit trail kept). Once a suspension has ended (served or lifted) the strike count
starts again from zero, but nothing is deleted: every challan and every suspension stays
on the driver's record, which officers can pull up at any time (Notice Desk, or for the
vehicles involved in an accident on the Road Safety page). Challans that existed before this module first ran are marked
'preexisting' and left untouched, but they still count as history.
"""
from __future__ import annotations

import calendar
import os
import re
import shutil
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.logger import get_logger
from database import db_manager

logger = get_logger("enforcement")

DDL = """
CREATE TABLE IF NOT EXISTS LICENCE_ACTION (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    notice_id INTEGER,
    months INTEGER NOT NULL,
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','lifted','expired')),
    reason TEXT,
    lifted_by TEXT,
    lifted_at TIMESTAMP,
    lift_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_licence_owner ON LICENCE_ACTION(owner_id);
CREATE TABLE IF NOT EXISTS ENFORCEMENT_LOG (
    notice_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    offense_no INTEGER,
    tier TEXT,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}
# Statutory penalties, Motor Vehicles Act 1988 as amended in 2019 (applied from the 1st offence).
# Repeat offences escalate further through the graduated schedule (the higher value wins).
RULE_POLICY = {
    "no_helmet":    {"label": "Riding without helmet", "fine": 1000.0, "months": 3, "law": "MV Act s.194D"},
    "three_seater": {"label": "Triple riding",         "fine": 1000.0, "months": 3, "law": "MV Act s.194C"},
    "no_seatbelt":  {"label": "Driving without seat belt", "fine": 1000.0, "months": 0, "law": "MV Act s.194B"},
}


def max_plausible_kmh() -> float:
    try:
        return float(os.environ.get("DSX_MAX_PLAUSIBLE_KMH", "200"))
    except ValueError:
        return 200.0


def plate_key(p: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (p or "").upper())


def _backup_db(tag: str) -> None:
    try:
        src = db_manager.DB_PATH
        dst = src.with_name(f"{src.stem}_backup_before_{tag}_{datetime.now():%Y%m%d_%H%M%S}{src.suffix}")
        shutil.copy2(src, dst)
        logger.info("Database backed up to %s", dst)
    except Exception:
        logger.exception("Backup before schema change failed")
        raise


def ensure_schema(c) -> None:
    """One-time, backed-up migration so helmet / triple-riding / seat-belt fines can be real
    e-challans: VIOLATION_NOTICE.violation_id becomes optional (it only exists for speed
    violations) and two columns are added (violation_kind, rule_violation_id)."""
    info = {r[1]: r for r in c.execute("PRAGMA table_info(VIOLATION_NOTICE)")}
    if not info:
        return
    if "violation_id" in info and info["violation_id"][3] == 1:
        sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='VIOLATION_NOTICE'").fetchone()[0]
        new_sql, k = re.subn(r"(violation_id\s+INTEGER\s+UNIQUE)\s+NOT\s+NULL", r"\1", sql, count=1, flags=re.I)
        if k == 0:
            new_sql, k = re.subn(r"(violation_id\s+INTEGER[^,\n]*?)\s+NOT\s+NULL", r"\1", sql, count=1, flags=re.I)
        if k:
            c.commit()
            _backup_db("rule_challans")
            idx = [r[0] for r in c.execute("SELECT sql FROM sqlite_master WHERE type='index' "
                                           "AND tbl_name='VIOLATION_NOTICE' AND sql IS NOT NULL")]
            iso = c.isolation_level
            c.isolation_level = None
            try:
                c.execute("PRAGMA foreign_keys=OFF")
                c.execute("BEGIN")
                c.execute(new_sql.replace("VIOLATION_NOTICE", "VIOLATION_NOTICE__new", 1))
                c.execute("INSERT INTO VIOLATION_NOTICE__new SELECT * FROM VIOLATION_NOTICE")
                c.execute("DROP TABLE VIOLATION_NOTICE")
                c.execute("ALTER TABLE VIOLATION_NOTICE__new RENAME TO VIOLATION_NOTICE")
                for stmt in idx:
                    c.execute(stmt)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            finally:
                c.execute("PRAGMA foreign_keys=ON")
                c.isolation_level = iso
            logger.warning("VIOLATION_NOTICE migrated: violation_id is now optional")
    cols = {r[1] for r in c.execute("PRAGMA table_info(VIOLATION_NOTICE)")}
    if "violation_kind" not in cols:
        c.execute("ALTER TABLE VIOLATION_NOTICE ADD COLUMN violation_kind TEXT DEFAULT 'overspeed'")
    if "rule_violation_id" not in cols:
        c.execute("ALTER TABLE VIOLATION_NOTICE ADD COLUMN rule_violation_id INTEGER")
    c.commit()


def ordinal(n: int) -> str:
    return ORDINAL.get(n, f"{n}th")


def window_days() -> int:
    try:
        return int(os.environ.get("DSX_STRIKE_WINDOW_DAYS", "365"))
    except ValueError:
        return 365


def months_between(start: Any, end: date) -> int:
    """Whole months covered from start to end (the suspension's real length)."""
    s = datetime.strptime(str(start)[:10], "%Y-%m-%d").date()
    m = (end.year - s.year) * 12 + (end.month - s.month)
    return max(1, m + (1 if end.day > s.day else 0))


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _conn():
    c = db_manager.get_connection()
    ensure_schema(c)
    fresh = c.execute("SELECT name FROM sqlite_master WHERE name='ENFORCEMENT_LOG'").fetchone() is None
    c.executescript(DDL)
    if fresh:  # existing challans keep their amounts; they still count as history
        c.execute("INSERT OR IGNORE INTO ENFORCEMENT_LOG(notice_id, status) "
                  "SELECT notice_id, 'preexisting' FROM VIOLATION_NOTICE")
    c.commit()
    return c


def tier_for(offense_no: int) -> Dict[str, Any]:
    tier = "first" if offense_no <= 1 else "second" if offense_no == 2 else "third"
    sched = db_manager.FINE_SCHEDULE[tier]
    months = 3 + max(0, offense_no - 3) if tier == "third" else 0
    action = {"first": "warning_fine", "second": "escalated_fine", "third": "license_suspension"}[tier]
    return {"tier": tier, "amount": float(sched["on_time"]), "overdue_amount": float(sched["overdue"]),
            "action": action, "months": months}


def cycle_start(c, owner_id: Optional[int]) -> Optional[str]:
    """End of the most recent suspension that is over; strikes count only after it."""
    if not owner_id:
        return None
    r = c.execute("""SELECT MAX(CASE WHEN status='expired' THEN ends_on || ' 23:59:59'
                                     WHEN status='lifted' THEN lifted_at END)
                     FROM LICENCE_ACTION WHERE owner_id=? AND status!='active'""", (owner_id,)).fetchone()
    return r[0] if r and r[0] else None


def annual_limit() -> int:
    try:
        return int(os.environ.get("DSX_ANNUAL_SUSPENSION_COUNT", "5"))
    except ValueError:
        return 5


def annual_months() -> int:
    try:
        return int(os.environ.get("DSX_ANNUAL_SUSPENSION_MONTHS", "3"))
    except ValueError:
        return 3


def annual_count(c, notice: Dict[str, Any]) -> int:
    """Challans of this licence holder in the last 365 days, including this one (no cycle reset)."""
    if not notice.get("owner_id"):
        return 0
    since = (datetime.utcnow() - timedelta(days=365)).isoformat(sep=" ", timespec="seconds")
    r = c.execute("""SELECT COUNT(*) FROM VIOLATION_NOTICE WHERE owner_id=? AND notice_id<=?
                     AND COALESCE(payment_status,'') NOT IN ('waived','disputed')
                     AND COALESCE(issued_at,'9999') >= ?""", (notice["owner_id"], notice["notice_id"], since)).fetchone()
    return int(r[0] or 0)


def prior_offences(c, notice: Dict[str, Any]) -> int:
    since = (datetime.now() - timedelta(days=window_days())).isoformat(sep=" ", timespec="seconds")
    reset = cycle_start(c, notice.get("owner_id"))
    if reset and reset > since:
        since = reset
    if notice.get("owner_id"):
        key_sql, key = "owner_id = ?", notice["owner_id"]
    else:
        key_sql, key = "UPPER(COALESCE(plate_number,'')) = UPPER(?)", notice.get("plate_number") or ""
    r = c.execute(f"""SELECT COUNT(*) FROM VIOLATION_NOTICE WHERE {key_sql} AND notice_id < ?
                      AND COALESCE(payment_status,'') NOT IN ('waived','disputed')
                      AND COALESCE(issued_at, '9999') >= ?""", (key, notice["notice_id"], since)).fetchone()
    return int(r[0] or 0)


def refresh_statuses(c=None) -> int:
    own = c is None
    c = c or _conn()
    try:
        n = c.execute("UPDATE LICENCE_ACTION SET status='expired' WHERE status='active' AND ends_on < ?",
                      (date.today().isoformat(),)).rowcount
        c.commit()
        return n
    finally:
        if own:
            c.close()


def active_suspension(owner_id: Optional[int], c=None) -> Optional[Dict[str, Any]]:
    if not owner_id:
        return None
    own = c is None
    c = c or _conn()
    try:
        refresh_statuses(c)
        r = c.execute("""SELECT * FROM LICENCE_ACTION WHERE owner_id=? AND status='active'
                         ORDER BY ends_on DESC LIMIT 1""", (owner_id,)).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            c.close()


def process_new() -> List[Dict[str, Any]]:
    """Apply the policy to every challan not processed yet. Cheap; call it often."""
    c = _conn()
    out = []
    try:
        refresh_statuses(c)
        rows = c.execute("""SELECT n.* FROM VIOLATION_NOTICE n
                            WHERE NOT EXISTS (SELECT 1 FROM ENFORCEMENT_LOG e WHERE e.notice_id=n.notice_id)
                            ORDER BY n.notice_id""").fetchall()
        for row in rows:
            out.append(_apply(c, dict(row)))
        c.commit()
    finally:
        c.close()
    for r in out:  # audit events after commit (a second connection must not wait on our write lock)
        if r.get("status") != "applied":
            continue
        try:
            db_manager.log_system_event(
                "enforcement", f"Challan #{r['notice_id']}: {ordinal(r['offense_no'])} offence, Rs {r['amount']:.0f}"
                + (f", licence suspended until {r['suspended_until']}" if r.get("suspended_until") else ""),
                level="warning" if r["tier"] != "first" else "info")
        except Exception:
            pass
    return out


def _apply(c, n: Dict[str, Any]) -> Dict[str, Any]:
    nid = n["notice_id"]
    if n.get("payment_status") not in ("pending", "overdue", None):
        c.execute("INSERT OR IGNORE INTO ENFORCEMENT_LOG(notice_id, status, detail) VALUES (?, 'skipped', ?)",
                  (nid, f"payment_status={n.get('payment_status')}"))
        return {"notice_id": nid, "status": "skipped"}
    kind = n.get("violation_kind") or "overspeed"
    if kind == "overspeed" and n.get("violation_id"):
        sp = c.execute("""SELECT sr.speed_value FROM VIOLATION v JOIN SPEED_RECORD sr ON sr.speed_id=v.speed_id
                          WHERE v.violation_id=?""", (n["violation_id"],)).fetchone()
        if sp and sp[0] is not None and float(sp[0]) > max_plausible_kmh():
            note = (f"{n.get('notes') or ''} Held: recorded speed {float(sp[0]):.1f} km/h is above the plausibility "
                    f"limit ({max_plausible_kmh():.0f} km/h), so no fine or licence action; officer review needed.").strip()
            c.execute("UPDATE VIOLATION_NOTICE SET payment_status='disputed', notes=? WHERE notice_id=?", (note, nid))
            c.execute("INSERT OR IGNORE INTO ENFORCEMENT_LOG(notice_id, status, detail) VALUES (?, 'held', ?)",
                      (nid, f"implausible speed {float(sp[0]):.1f} km/h"))
            return {"notice_id": nid, "status": "held", "speed": float(sp[0])}
    offense_no = prior_offences(c, n) + 1
    pol = dict(tier_for(offense_no))
    rule = RULE_POLICY.get(kind)
    statutory = False
    if rule:  # statutory minimum from the 1st offence; repeat offences escalate further
        statutory = rule["months"] > pol["months"]
        pol["amount"] = max(rule["fine"], pol["amount"])
        pol["overdue_amount"] = max(2 * rule["fine"], pol["overdue_amount"])
        pol["months"] = max(rule["months"], pol["months"])
        if pol["months"]:
            pol["action"] = "license_suspension"
    annual = annual_count(c, n)
    annual_hit = n.get("owner_id") and annual >= annual_limit() and annual_months() > pol["months"]
    if annual_hit:  # 5 or more challans in a year suspend the licence regardless of the strike cycle
        pol["months"] = annual_months()
        pol["action"] = "license_suspension"
    notes = [n.get("notes") or ""]
    old = float(n.get("amount") or 0)
    if abs(old - pol["amount"]) > 0.005:
        notes.append(f"Fine set by schedule: Rs {pol['amount']:.0f} ({ordinal(offense_no)} offence"
                     f"{', ' + rule['law'] if rule else ''}); amount entered was Rs {old:.0f}.")
    current = active_suspension(n.get("owner_id"), c)
    if current:
        notes.append(f"Offence committed while the driving licence is suspended (until {current['ends_on']}).")
    action_row = None
    if pol["months"] and n.get("owner_id"):
        start = date.today()
        end = add_months(start, pol["months"])
        if current and current["ends_on"] >= end.isoformat():
            action_row = current
        elif current:
            total = months_between(current["starts_on"], end)
            c.execute("UPDATE LICENCE_ACTION SET ends_on=?, months=?, reason=COALESCE(reason,'')||? "
                      "WHERE action_id=?", (end.isoformat(), total,
                                            f" | extended to {end.isoformat()} by challan #{nid}",
                                            current["action_id"]))
            action_row = dict(current, ends_on=end.isoformat())
        else:
            cur = c.execute("""INSERT INTO LICENCE_ACTION(owner_id, notice_id, months, starts_on, ends_on, reason)
                               VALUES (?,?,?,?,?,?)""",
                            (n["owner_id"], nid, pol["months"], start.isoformat(), end.isoformat(),
                             (f"{annual} challans within one year (challan #{nid})" if annual_hit else
                              f"{rule['label']}: licence disqualification under {rule['law']} (challan #{nid})"
                              if statutory else
                              f"{ordinal(offense_no)} offence within {window_days()} days (challan #{nid})")))
            action_row = {"action_id": cur.lastrowid, "starts_on": start.isoformat(), "ends_on": end.isoformat()}
        notes.append(f"Driving licence suspended for {pol['months']} month(s), until {action_row['ends_on']}.")
    elif pol["months"]:
        notes.append(f"Licence suspension of {pol['months']} month(s) due; owner not identified yet.")
    c.execute("""UPDATE VIOLATION_NOTICE SET base_amount=?, amount=?, overdue_amount=?, offense_count=?,
                 offense_tier=?, action_taken=?, suspension_months=?, notes=? WHERE notice_id=?""",
              (pol["amount"], pol["amount"], pol["overdue_amount"], offense_no, pol["tier"], pol["action"],
               pol["months"], " ".join(x for x in notes if x).strip(), nid))
    c.execute("INSERT OR IGNORE INTO ENFORCEMENT_LOG(notice_id, status, offense_no, tier, detail) VALUES (?,?,?,?,?)",
              (nid, "applied", offense_no, pol["tier"],
               f"Rs {pol['amount']:.0f}" + (f", suspended until {action_row['ends_on']}" if action_row else "")))
    logger.info("Challan %s: offence %s, tier %s", nid, offense_no, pol["tier"])
    return {"notice_id": nid, "status": "applied", "offense_no": offense_no, "kind": kind, **pol,
            "suspended_until": action_row["ends_on"] if action_row else None,
            "while_suspended": bool(current)}


def lift(action_id: int, officer: str, reason: str) -> bool:
    if not reason.strip():
        raise ValueError("A reason is required to lift a suspension.")
    c = _conn()
    try:
        n = c.execute("""UPDATE LICENCE_ACTION SET status='lifted', lifted_by=?, lifted_at=CURRENT_TIMESTAMP,
                         lift_reason=? WHERE action_id=? AND status='active'""",
                      (officer, reason.strip(), action_id)).rowcount
        c.commit()
    finally:
        c.close()
    if n:
        db_manager.log_system_event("enforcement", f"Licence suspension {action_id} lifted by {officer}: {reason}")
    return n == 1


def standing(owner_id: int) -> Dict[str, Any]:
    """What the owner page shows: offences in the window, next tier, licence status."""
    c = _conn()
    try:
        refresh_statuses(c)
        since = (datetime.now() - timedelta(days=window_days())).isoformat(sep=" ", timespec="seconds")
        reset = cycle_start(c, owner_id)
        if reset and reset > since:
            since = reset
        count = int(c.execute("""SELECT COUNT(*) FROM VIOLATION_NOTICE WHERE owner_id=?
                                 AND COALESCE(payment_status,'') NOT IN ('waived','disputed')
                                 AND COALESCE(issued_at,'9999') >= ?""", (owner_id, since)).fetchone()[0])
        susp = active_suspension(owner_id, c)
        history = [dict(r) for r in c.execute("SELECT * FROM LICENCE_ACTION WHERE owner_id=? ORDER BY created_at DESC",
                                              (owner_id,))]
    finally:
        c.close()
    nxt = tier_for(count + 1)
    return {"offences": count, "window_days": window_days(), "suspension": susp, "history": history,
            "counting_since": reset,
            "next_amount": nxt["amount"], "next_suspends": bool(nxt["months"]), "next_months": nxt["months"]}


def all_actions(limit: int = 200) -> List[Dict[str, Any]]:
    c = _conn()
    try:
        refresh_statuses(c)
        return [dict(r) for r in c.execute("""SELECT a.*, o.name AS owner_name FROM LICENCE_ACTION a
                                              LEFT JOIN OWNER_ACCOUNT o ON o.owner_id=a.owner_id
                                              ORDER BY a.created_at DESC LIMIT ?""", (limit,))]
    finally:
        c.close()


def suspension_for_notice(notice_id: int) -> Optional[Dict[str, Any]]:
    c = _conn()
    try:
        r = c.execute("""SELECT a.* FROM LICENCE_ACTION a JOIN VIOLATION_NOTICE n ON n.owner_id=a.owner_id
                         WHERE n.notice_id=? AND a.status='active' ORDER BY a.ends_on DESC LIMIT 1""",
                      (notice_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def driver_record(plate: Optional[str] = None, owner_id: Optional[int] = None) -> Dict[str, Any]:
    """Full, permanent record for an officer: every challan ever, every suspension, the
    current strike count and licence status. Owner name masked (DPDP data minimisation)."""
    from backend.registry import mask_name

    c = _conn()
    try:
        refresh_statuses(c)
        plate_u = plate_key(plate)
        owner = None
        if owner_id is None and plate_u:
            r = c.execute("""SELECT rv.owner_id FROM REGISTERED_VEHICLE rv
                             WHERE REPLACE(REPLACE(REPLACE(UPPER(rv.plate_number),' ',''),'-',''),'.','')=?""",
                          (plate_u,)).fetchone()
            owner_id = r[0] if r else None
        if owner_id:
            o = c.execute("SELECT owner_id, name FROM OWNER_ACCOUNT WHERE owner_id=?", (owner_id,)).fetchone()
            owner = {"owner_id": o[0], "name": mask_name(o[1])} if o else None
            vehicles = [r[0] for r in c.execute("SELECT plate_number FROM REGISTERED_VEHICLE WHERE owner_id=?",
                                                (owner_id,))]
            challans = c.execute("""SELECT notice_id, plate_number, violation_kind, issued_at, offense_count,
                                    offense_tier, amount, payment_status, action_taken, suspension_months
                                    FROM VIOLATION_NOTICE WHERE owner_id=? ORDER BY issued_at""",
                                 (owner_id,)).fetchall()
            actions = c.execute("SELECT * FROM LICENCE_ACTION WHERE owner_id=? ORDER BY created_at",
                                (owner_id,)).fetchall()
        else:
            vehicles = [plate_u] if plate_u else []
            challans = c.execute("""SELECT notice_id, plate_number, violation_kind, issued_at, offense_count,
                                    offense_tier, amount, payment_status, action_taken, suspension_months
                                    FROM VIOLATION_NOTICE WHERE REPLACE(REPLACE(REPLACE(UPPER(COALESCE(plate_number,'')),
                                    ' ',''),'-',''),'.','')=? ORDER BY issued_at""", (plate_u,)).fetchall()
            actions = []
        rules = []
        if vehicles:
            q = ",".join("?" * len(vehicles))
            rules = c.execute(f"""SELECT rule_violation_id, rule_type, plate_text, fine_amount, status, detected_at
                                  FROM RULE_VIOLATION WHERE REPLACE(UPPER(COALESCE(plate_text,'')),' ','') IN ({q})
                                  ORDER BY detected_at""", [v.replace(" ", "").upper() for v in vehicles]).fetchall()
    finally:
        c.close()
    st = standing(owner_id) if owner_id else None
    return {"plate": plate_u, "registered": bool(owner_id), "owner": owner, "vehicles": vehicles,
            "licence": ("SUSPENDED until " + st["suspension"]["ends_on"]) if st and st["suspension"]
            else ("VALID" if owner_id else "unknown (vehicle not registered)"),
            "current_strikes": st["offences"] if st else None,
            "counting_since": st.get("counting_since") if st else None,
            "lifetime_challans": len(challans),
            "challans": [dict(r) for r in challans],
            "rule_violations": [dict(r) for r in rules],
            "suspensions": [dict(r) for r in actions]}


def issue_rule_challan(rule_violation_id: int, issued_by: int, plate: Optional[str] = None,
                       due_days: int = 14) -> int:
    """Turn a detected helmet / triple-riding / seat-belt record into a real e-challan.
    The repeat-offender and statutory policy, SMS, Razorpay link and auto-settlement then
    apply exactly as for speed challans. Returns the notice id (idempotent)."""
    c = _conn()
    try:
        rv = c.execute("SELECT * FROM RULE_VIOLATION WHERE rule_violation_id=?", (rule_violation_id,)).fetchone()
        if rv is None:
            raise ValueError("Unknown rule violation.")
        rv = dict(rv)
        if rv["rule_type"] not in RULE_POLICY:
            raise ValueError(f"Unsupported rule {rv['rule_type']}.")
        done = c.execute("SELECT notice_id FROM VIOLATION_NOTICE WHERE rule_violation_id=?",
                         (rule_violation_id,)).fetchone()
        if done:
            return int(done[0])
        p = plate_key(plate or rv.get("plate_text"))
        if not p:
            raise ValueError("No number plate was read for this record. Enter the plate to issue the e-challan.")
        reg = c.execute("""SELECT reg_vehicle_id, owner_id FROM REGISTERED_VEHICLE WHERE
                           REPLACE(REPLACE(REPLACE(UPPER(plate_number),' ',''),'-',''),'.','')=?""", (p,)).fetchone()
        due = (date.today() + timedelta(days=due_days)).isoformat()
        rule = RULE_POLICY[rv["rule_type"]]
        cur = c.execute("""INSERT INTO VIOLATION_NOTICE(violation_id, reg_vehicle_id, owner_id, plate_number, issued_by,
                           base_amount, amount, due_date, payment_status, notes, violation_kind, rule_violation_id)
                           VALUES (NULL,?,?,?,?,?,?,?,'pending',?,?,?)""",
                        (reg[0] if reg else None, reg[1] if reg else None, p, issued_by, rule["fine"], rule["fine"],
                         due, f"{rule['label']} detected {rv.get('detected_at')} (camera {rv.get('camera_id')}, "
                              f"track {rv.get('tracker_id')}).", rv["rule_type"], rule_violation_id))
        nid = int(cur.lastrowid)
        c.execute("""UPDATE RULE_VIOLATION SET status='issued', plate_text=COALESCE(NULLIF(plate_text,''), ?),
                     reg_vehicle_id=COALESCE(reg_vehicle_id, ?) WHERE rule_violation_id=?""",
                  (p, reg[0] if reg else None, rule_violation_id))
        c.commit()
    finally:
        c.close()
    process_new()
    return nid
