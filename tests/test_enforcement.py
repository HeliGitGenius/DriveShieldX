"""Repeat-offender policy end to end: schedule fines, strike counting per licence holder,
licence suspension (create / extend / expire / lift), and the SMS wording.
Runs on a throw-away copy of the database."""
import shutil
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def db(monkeypatch):
    from database import db_manager
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    shutil.copy(ROOT / "database" / "overspeed.db", tmp)
    c = sqlite3.connect(tmp)
    for t in ("ENFORCEMENT_LOG", "LICENCE_ACTION", "CHALLAN_SMS"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    # free six violations (every shipped violation already has a challan) -- temp copy only
    vids = [r[0] for r in c.execute("SELECT violation_id FROM VIOLATION_NOTICE WHERE violation_id IS NOT NULL "
                                    "ORDER BY violation_id DESC LIMIT 6")]
    c.executemany("DELETE FROM VIOLATION_NOTICE WHERE violation_id=?", [(v,) for v in vids])
    oid = c.execute("INSERT INTO OWNER_ACCOUNT(name, email, password_hash, phone) VALUES "
                    "('Test Owner','enf@test.local','x','9000000001')").lastrowid
    c.execute("INSERT INTO REGISTERED_VEHICLE(owner_id, plate_number) VALUES (?, 'MH99ZZ0001')", (oid,))
    uid = c.execute("SELECT user_id FROM USER LIMIT 1").fetchone()[0]
    c.commit(); c.close()
    monkeypatch.setattr(db_manager, "DB_PATH", tmp)
    monkeypatch.delenv("DSX_STRIKE_WINDOW_DAYS", raising=False)
    from backend import enforcement
    enforcement.process_new()                      # baseline: existing challans become 'preexisting'
    return tmp, oid, uid, vids


def issue(tmp, vid, oid, uid, amount=200, status="pending"):
    c = sqlite3.connect(tmp)
    nid = c.execute("""INSERT INTO VIOLATION_NOTICE(violation_id, owner_id, plate_number, issued_by, amount,
                       due_date, payment_status) VALUES (?,?,?,?,?,?,?)""",
                    (vid, oid, "MH99ZZ0001", uid, amount, "2026-12-31", status)).lastrowid
    c.commit(); c.close()
    return nid


def row(tmp, nid):
    c = sqlite3.connect(tmp); c.row_factory = sqlite3.Row
    r = dict(c.execute("SELECT * FROM VIOLATION_NOTICE WHERE notice_id=?", (nid,)).fetchone()); c.close()
    return r


def test_schedule_tiers_and_suspension(db):
    from backend import enforcement
    tmp, oid, uid, vids = db
    assert enforcement.process_new() == []          # nothing new; old challans untouched

    n1 = issue(tmp, vids[0], oid, uid, amount=200); enforcement.process_new()
    r1 = row(tmp, n1)
    assert (r1["offense_count"], r1["offense_tier"], r1["amount"], r1["overdue_amount"]) == (1, "first", 500, 1000)
    assert "amount entered was Rs 200" in r1["notes"]

    n2 = issue(tmp, vids[1], oid, uid); enforcement.process_new()
    assert (row(tmp, n2)["offense_count"], row(tmp, n2)["amount"]) == (2, 1000)
    assert enforcement.standing(oid)["next_suspends"] is True

    n3 = issue(tmp, vids[2], oid, uid); res = enforcement.process_new()[0]
    r3 = row(tmp, n3)
    assert (r3["offense_tier"], r3["amount"], r3["action_taken"], r3["suspension_months"]) == \
        ("third", 3000, "license_suspension", 3)
    assert res["suspended_until"] == enforcement.add_months(date.today(), 3).isoformat()
    s = enforcement.standing(oid)
    assert s["suspension"]["status"] == "active" and s["offences"] == 3

    n4 = issue(tmp, vids[3], oid, uid); res4 = enforcement.process_new()[0]
    assert res4["while_suspended"] and row(tmp, n4)["suspension_months"] == 4
    assert "while the driving licence is suspended" in row(tmp, n4)["notes"]
    acts = enforcement.all_actions()
    assert len([a for a in acts if a["owner_id"] == oid]) == 1           # extended, not duplicated
    assert acts[0]["ends_on"] == enforcement.add_months(date.today(), 4).isoformat()
    assert acts[0]["months"] == 4                                         # real length, not 3 + 4
    assert enforcement.process_new() == []                                # each challan processed once


def test_waived_does_not_count_and_paid_is_skipped(db):
    from backend import enforcement
    tmp, oid, uid, vids = db
    issue(tmp, vids[0], oid, uid, status="waived")
    enforcement.process_new()
    n = issue(tmp, vids[1], oid, uid); enforcement.process_new()
    assert row(tmp, n)["offense_count"] == 1
    p = issue(tmp, vids[2], oid, uid, amount=777, status="paid"); enforcement.process_new()
    assert row(tmp, p)["amount"] == 777                                   # paid challans never re-priced


def test_expiry_and_lift(db):
    from backend import enforcement
    tmp, oid, uid, vids = db
    for v in vids[:3]:
        issue(tmp, v, oid, uid); enforcement.process_new()
    act = enforcement.active_suspension(oid)
    with pytest.raises(ValueError):
        enforcement.lift(act["action_id"], "officer", "  ")
    c = sqlite3.connect(tmp)                     # earlier challans were issued before the lift
    c.execute("UPDATE VIOLATION_NOTICE SET issued_at=datetime('now','-1 day') WHERE owner_id=?", (oid,))
    c.commit(); c.close()
    assert enforcement.lift(act["action_id"], "officer", "appeal accepted")
    assert enforcement.active_suspension(oid) is None
    n4 = issue(tmp, vids[3], oid, uid); enforcement.process_new()        # count restarts after a lift
    assert row(tmp, n4)["offense_count"] == 1 and row(tmp, n4)["amount"] == 500


def test_expiry_resets_strikes_but_keeps_the_record(db):
    from backend import enforcement
    tmp, oid, uid, vids = db
    for v in vids[:3]:
        issue(tmp, v, oid, uid); enforcement.process_new()
    act = enforcement.active_suspension(oid)
    c = sqlite3.connect(tmp)                     # pretend the suspension was served long ago
    c.execute("UPDATE LICENCE_ACTION SET starts_on='1999-10-01', ends_on='2000-01-01' WHERE action_id=?",
              (act["action_id"],))
    c.execute("UPDATE VIOLATION_NOTICE SET issued_at='1999-09-30 10:00:00' WHERE owner_id=?", (oid,))
    c.commit(); c.close()
    monkeypatch_window = 100000                  # keep the old challans inside the window for this test
    import os
    os.environ["DSX_STRIKE_WINDOW_DAYS"] = str(monkeypatch_window)
    try:
        assert enforcement.active_suspension(oid) is None                 # expired automatically
        assert enforcement.standing(oid)["offences"] == 0                 # fresh start after serving it
        n4 = issue(tmp, vids[3], oid, uid); enforcement.process_new()
        assert row(tmp, n4)["offense_count"] == 1 and row(tmp, n4)["amount"] == 500
        rec = enforcement.driver_record(plate="MH99 ZZ 0001")
        assert rec["registered"] and rec["lifetime_challans"] == 4 and rec["current_strikes"] == 1
        assert rec["licence"] == "VALID" and [a["status"] for a in rec["suspensions"]] == ["expired"]
        assert rec["owner"]["name"] == "T*** O****"                       # masked for officers too
    finally:
        os.environ.pop("DSX_STRIKE_WINDOW_DAYS", None)


def test_sms_states_offence_and_suspension(db, monkeypatch):
    from backend import alerts, challan_sms, enforcement
    tmp, oid, uid, vids = db
    monkeypatch.setattr(alerts, "OUTBOX_DIR", Path(tempfile.mkdtemp()))
    monkeypatch.delenv("DSX_SMS_PROVIDER", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_ID_TEST", raising=False)
    challan_sms.notify_pending(0)                                          # SMS baseline
    for v in vids[:3]:
        issue(tmp, v, oid, uid)
    challan_sms.notify_pending(0)
    texts = sorted(f.read_text(encoding="utf-8") for f in alerts.OUTBOX_DIR.glob("sms_*.txt"))
    assert any("This is your 1st offence" in t and "Rs 500" in t for t in texts)
    assert any("This is your 3rd offence" in t and "Rs 3,000" in t and "licence is suspended until" in t for t in texts)


def _rule(tmp, rule_type, plate="MH99ZZ0001", status="detected"):
    c = sqlite3.connect(tmp)
    rid = c.execute("""INSERT INTO RULE_VIOLATION(session_id, tracker_id, rule_type, plate_text, fine_amount, status)
                       VALUES (?, 't', ?, ?, 500, ?)""", (90000 + hash(rule_type) % 1000, rule_type, plate, status)).lastrowid
    c.commit(); c.close()
    return rid


def test_schema_migration_keeps_every_challan(monkeypatch):
    """Built on a small legacy-shaped database, so it does not depend on whether the real
    database has already been migrated."""
    from backend import enforcement
    from database import db_manager
    tmp = Path(tempfile.mkdtemp()) / "legacy.db"
    c = sqlite3.connect(tmp)
    c.executescript("""
        CREATE TABLE VIOLATION_NOTICE (
            notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
            violation_id INTEGER UNIQUE NOT NULL,
            owner_id INTEGER,
            plate_number TEXT,
            amount REAL NOT NULL,
            payment_status TEXT DEFAULT 'pending');
        CREATE INDEX idx_notice_owner ON VIOLATION_NOTICE(owner_id);
        INSERT INTO VIOLATION_NOTICE(violation_id, owner_id, plate_number, amount) VALUES (1, 7, 'MH01AA0001', 500);
        INSERT INTO VIOLATION_NOTICE(violation_id, owner_id, plate_number, amount, payment_status)
            VALUES (2, 8, 'MH01AA0002', 1000, 'paid');""")
    c.commit(); c.close()
    monkeypatch.setattr(db_manager, "DB_PATH", tmp)
    c = sqlite3.connect(tmp)
    enforcement.ensure_schema(c)
    cols = {r[1]: r for r in c.execute("PRAGMA table_info(VIOLATION_NOTICE)")}
    assert cols["violation_id"][3] == 0 and "violation_kind" in cols and "rule_violation_id" in cols
    assert c.execute("SELECT notice_id, violation_id, plate_number, amount, payment_status, violation_kind "
                     "FROM VIOLATION_NOTICE ORDER BY notice_id").fetchall() == \
        [(1, 1, "MH01AA0001", 500.0, "pending", "overspeed"), (2, 2, "MH01AA0002", 1000.0, "paid", "overspeed")]
    assert c.execute("SELECT name FROM sqlite_master WHERE name='idx_notice_owner'").fetchone()   # index kept
    c.execute("INSERT INTO VIOLATION_NOTICE(violation_id, plate_number, amount) VALUES (NULL, 'X', 1)")  # now allowed
    assert list(tmp.parent.glob("legacy_backup_before_rule_challans_*.db"))                  # backed up first
    enforcement.ensure_schema(c)                                                              # second run: no-op
    assert len(list(tmp.parent.glob("legacy_backup_before_rule_challans_*.db"))) == 1
    c.close()


def test_helmet_challan_statutory_fine_and_disqualification(db, monkeypatch):
    from backend import alerts, challan_sms, enforcement
    tmp, oid, uid, vids = db
    monkeypatch.setattr(alerts, "OUTBOX_DIR", Path(tempfile.mkdtemp()))
    monkeypatch.delenv("DSX_SMS_PROVIDER", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_ID_TEST", raising=False)
    challan_sms.notify_pending(0)                                      # SMS baseline
    rid = _rule(tmp, "no_helmet", plate="mh 99 zz 0001")
    nid = enforcement.issue_rule_challan(rid, uid)
    assert enforcement.issue_rule_challan(rid, uid) == nid            # idempotent
    r = row(tmp, nid)
    assert (r["violation_kind"], r["amount"], r["offense_count"], r["action_taken"], r["suspension_months"]) == \
        ("no_helmet", 1000, 1, "license_suspension", 3)
    assert r["owner_id"] == oid and r["violation_id"] is None
    act = enforcement.active_suspension(oid)
    assert "s.194D" in act["reason"]
    seat = enforcement.issue_rule_challan(_rule(tmp, "no_seatbelt"), uid)
    rs = row(tmp, seat)
    assert (rs["amount"], rs["offense_count"]) == (1000, 2)              # same strike count as speed challans
    third = enforcement.issue_rule_challan(_rule(tmp, "three_seater"), uid)
    assert row(tmp, third)["amount"] == 3000                            # 3rd offence escalates above Rs 1000
    challan_sms.notify_pending(0)
    texts = [f.read_text(encoding="utf-8") for f in alerts.OUTBOX_DIR.glob("sms_*.txt")]
    assert len(texts) == 3                                               # one SMS per challan, no duplicates
    assert any("Riding without helmet (MV Act s.194D)" in t and "licence is suspended until" in t for t in texts)
    c = sqlite3.connect(tmp)
    assert c.execute("SELECT status FROM RULE_VIOLATION WHERE rule_violation_id=?", (rid,)).fetchone()[0] == "issued"


def test_rule_challan_needs_a_plate(db):
    from backend import enforcement
    tmp, oid, uid, vids = db
    rid = _rule(tmp, "no_helmet", plate=None)
    with pytest.raises(ValueError):
        enforcement.issue_rule_challan(rid, uid)
    nid = enforcement.issue_rule_challan(rid, uid, plate="MH99ZZ0001")
    assert row(tmp, nid)["owner_id"] == oid


def test_implausible_speed_is_held(db):
    from backend import enforcement
    tmp, oid, uid, vids = db
    c = sqlite3.connect(tmp)
    c.execute("""UPDATE SPEED_RECORD SET speed_value=367.6 WHERE speed_id=(SELECT speed_id FROM VIOLATION
                 WHERE violation_id=?)""", (vids[0],))
    c.commit(); c.close()
    nid = issue(tmp, vids[0], oid, uid)
    res = enforcement.process_new()[0]
    assert res["status"] == "held" and row(tmp, nid)["payment_status"] == "disputed"
    assert "plausibility limit" in row(tmp, nid)["notes"]
    assert enforcement.active_suspension(oid) is None and enforcement.standing(oid)["offences"] == 0


def test_accident_sms_names_hospital_and_police():
    import inspect
    from backend import dispatch
    assert '"Nearest police"' in inspect.getsource(dispatch.dispatch_accident)
