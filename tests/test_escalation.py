"""Unpaid-challan escalation: reminders, contest window and decisions, NOT TO BE TRANSACTED
hotlist with automatic clearing, live sightings, court referral, and the 5-per-year rule.
Runs on a throw-away copy of the database; no gateway, so SMS only go to the outbox."""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def db(monkeypatch):
    from backend import alerts, escalation
    from database import db_manager
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    shutil.copy(ROOT / "database" / "overspeed.db", tmp)
    c = sqlite3.connect(tmp)
    for t in ("ENFORCEMENT_LOG", "LICENCE_ACTION", "CHALLAN_SMS", "ESCALATION_EVENT", "VEHICLE_HOTLIST",
              "HOTLIST_SIGHTING", "CHALLAN_DISPUTE", "ESCALATION_STATE"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    vids = [r[0] for r in c.execute("SELECT violation_id FROM VIOLATION_NOTICE WHERE violation_id IS NOT NULL "
                                    "ORDER BY violation_id DESC LIMIT 6")]
    c.executemany("DELETE FROM VIOLATION_NOTICE WHERE violation_id=?", [(v,) for v in vids])
    oid = c.execute("INSERT INTO OWNER_ACCOUNT(name, email, password_hash, phone) VALUES "
                    "('Esc Owner','esc@test.local','x','9000000002')").lastrowid
    c.execute("INSERT INTO REGISTERED_VEHICLE(owner_id, plate_number) VALUES (?, 'MH98YY0002')", (oid,))
    uid = c.execute("SELECT user_id FROM USER LIMIT 1").fetchone()[0]
    c.commit(); c.close()
    monkeypatch.setattr(db_manager, "DB_PATH", tmp)
    monkeypatch.setattr(alerts, "OUTBOX_DIR", Path(tempfile.mkdtemp()))
    for k in ("DSX_SMS_PROVIDER", "RAZORPAY_KEY_ID_TEST", "DSX_ESCALATION_DAY_SECONDS", "DSX_STRIKE_WINDOW_DAYS"):
        monkeypatch.delenv(k, raising=False)
    from backend import enforcement
    enforcement.process_new()
    escalation.run_once(force=True)              # baseline pass over the existing data
    return tmp, oid, uid, vids


def issue(tmp, vid, oid, uid, days_ago=0):
    c = sqlite3.connect(tmp)
    nid = c.execute("""INSERT INTO VIOLATION_NOTICE(violation_id, owner_id, plate_number, issued_by, amount, due_date,
                       payment_status, issued_at) VALUES (?,?,?,?,500,'2099-01-01','pending',
                       datetime('now', ?))""", (vid, oid, "MH98YY0002", uid, f"-{days_ago} days")).lastrowid
    c.commit(); c.close()
    from backend import enforcement
    enforcement.process_new()
    return nid


def stages(tmp, nid):
    c = sqlite3.connect(tmp)
    s = {r[0] for r in c.execute("SELECT stage FROM ESCALATION_EVENT WHERE notice_id=?", (nid,))}
    c.close()
    return s


def outbox_texts():
    from backend import alerts
    return [f.read_text(encoding="utf-8") for f in alerts.OUTBOX_DIR.glob("sms_*.txt")]


def test_first_run_is_silent(monkeypatch):
    from backend import alerts, escalation
    from database import db_manager
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    shutil.copy(ROOT / "database" / "overspeed.db", tmp)
    c = sqlite3.connect(tmp)
    for t in ("ESCALATION_EVENT", "VEHICLE_HOTLIST", "HOTLIST_SIGHTING", "CHALLAN_DISPUTE", "ESCALATION_STATE"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.execute("UPDATE VIOLATION_NOTICE SET issued_at=datetime('now','-100 days') WHERE payment_status IN ('pending','overdue')")
    c.commit(); c.close()
    monkeypatch.setattr(db_manager, "DB_PATH", tmp)
    monkeypatch.setattr(alerts, "OUTBOX_DIR", Path(tempfile.mkdtemp()))
    monkeypatch.delenv("DSX_ESCALATION_DAY_SECONDS", raising=False)
    escalation.run_once(force=True)
    assert outbox_texts() == []                                        # nothing sent for old challans
    escalation.run_once(force=True)
    assert outbox_texts() == []                                        # and nothing later either


def test_reminders_once(db):
    from backend import escalation
    tmp, oid, uid, vids = db
    nid = issue(tmp, vids[0], oid, uid, days_ago=8)
    escalation.run_once(force=True)
    assert stages(tmp, nid) == {"reminder_7"}
    escalation.run_once(force=True)
    assert sum(f"#{nid} " in t and "Reminder" in t for t in outbox_texts()) == 1


def test_hotlist_court_and_clearing(db):
    from backend import escalation
    tmp, oid, uid, vids = db
    nid = issue(tmp, vids[0], oid, uid, days_ago=91)
    escalation.run_once(force=True)
    assert {"ntbt", "court"} <= stages(tmp, nid)
    h = escalation.is_hotlisted("MH 98 YY 0002")
    assert h and str(nid) in h["notice_ids"]
    assert any("NOT TO BE TRANSACTED" in t for t in outbox_texts())
    assert any(r["notice_id"] == nid for r in escalation.court_referrals())
    c = sqlite3.connect(tmp)
    c.execute("UPDATE VIOLATION_NOTICE SET payment_status='paid' WHERE notice_id=?", (nid,)); c.commit(); c.close()
    escalation.run_once(force=True)
    assert escalation.is_hotlisted("MH98YY0002") is None
    assert not any(r["notice_id"] == nid for r in escalation.court_referrals())


def test_live_sighting_of_hotlisted_plate(db):
    from backend import escalation
    tmp, oid, uid, vids = db
    issue(tmp, vids[0], oid, uid, days_ago=80)
    escalation.run_once(force=True)
    c = sqlite3.connect(tmp)
    sess = c.execute("SELECT session_id FROM VIDEO_SESSION LIMIT 1").fetchone()[0]
    c.execute("""INSERT INTO VEHICLE(tracker_id, vehicle_type, plate_text, session_id, last_seen_time)
                 VALUES ('h1','car','MH98YY0002',?, strftime('%Y-%m-%dT%H:%M:%f','now','localtime','+1 minute'))""",
              (sess,))
    c.commit(); c.close()
    assert escalation.run_once(force=True)["sightings"] == 1
    assert escalation.run_once(force=True)["sightings"] == 0           # one alert per sighting
    s = escalation.sightings(unacknowledged_only=True)
    assert s[0]["plate_key"] == "MH98YY0002"
    escalation.acknowledge_sighting(s[0]["sighting_id"], "officer")
    assert escalation.sightings(unacknowledged_only=True) == []


def test_contest_pauses_clock_and_decisions(db):
    from backend import enforcement, escalation
    tmp, oid, uid, vids = db
    nid = issue(tmp, vids[0], oid, uid, days_ago=10)
    with pytest.raises(ValueError):
        escalation.file_dispute(nid, oid, "short")
    did = escalation.file_dispute(nid, oid, "That is not my vehicle, plate misread")
    with pytest.raises(ValueError):
        escalation.file_dispute(nid, oid, "Second attempt at contesting this")
    escalation.run_once(force=True)
    assert stages(tmp, nid) == set()                                 # clock stopped while under review
    escalation.decide_dispute(did, False, "officer", "Evidence shows the vehicle clearly")
    c = sqlite3.connect(tmp)
    assert c.execute("SELECT payment_status FROM VIOLATION_NOTICE WHERE notice_id=?", (nid,)).fetchone()[0] == "pending"
    assert any("REJECTED" in t for t in outbox_texts())
    n2 = issue(tmp, vids[1], oid, uid, days_ago=1)
    d2 = escalation.file_dispute(n2, oid, "Medical emergency, documents attached")
    escalation.decide_dispute(d2, True, "officer", "Emergency verified")
    assert c.execute("SELECT payment_status FROM VIOLATION_NOTICE WHERE notice_id=?", (n2,)).fetchone()[0] == "waived"
    c.close()
    assert enforcement.standing(oid)["offences"] == 1                # cancelled challan does not count


def test_contest_window_closes(db):
    from backend import escalation
    tmp, oid, uid, vids = db
    nid = issue(tmp, vids[0], oid, uid, days_ago=50)
    with pytest.raises(ValueError, match="45-day"):
        escalation.file_dispute(nid, oid, "Too late to contest this one")


def test_five_challans_in_a_year_suspend(db):
    from backend import enforcement
    tmp, oid, uid, vids = db
    for v in vids[:3]:
        issue(tmp, v, oid, uid)
    act = enforcement.active_suspension(oid)
    c = sqlite3.connect(tmp)
    c.execute("UPDATE VIOLATION_NOTICE SET issued_at=datetime('now','-1 day') WHERE owner_id=?", (oid,))
    c.commit(); c.close()
    enforcement.lift(act["action_id"], "officer", "appeal accepted")   # strike cycle restarts
    issue(tmp, vids[3], oid, uid)                                     # 4th in the year: 1st of new cycle
    assert enforcement.active_suspension(oid) is None
    n5 = issue(tmp, vids[4], oid, uid)                                # 5th in the year
    act = enforcement.active_suspension(oid)
    assert act and "5 challans within one year" in act["reason"]
    c = sqlite3.connect(tmp)
    assert c.execute("SELECT offense_count, suspension_months FROM VIOLATION_NOTICE WHERE notice_id=?",
                     (n5,)).fetchone() == (2, 3)


def test_demo_time_only_speeds_up_new_challans(db, monkeypatch):
    from backend import escalation
    tmp, oid, uid, vids = db
    old = issue(tmp, vids[0], oid, uid, days_ago=3)                 # real record, 3 real days old
    monkeypatch.setenv("DSX_ESCALATION_DAY_SECONDS", "1")           # 1 demo "day" per second
    escalation.run_once(force=True)                                 # demo clock starts now
    assert stages(tmp, old) == set()                                # still only 3 real days old
    c = sqlite3.connect(tmp)
    new = c.execute("""INSERT INTO VIOLATION_NOTICE(violation_id, owner_id, plate_number, issued_by, amount, due_date,
                       payment_status, issued_at) VALUES (?,?,?,?,500,'2099-01-01','pending',
                       datetime('now','+2 seconds'))""", (vids[1], oid, "MH98YY0002", uid)).lastrowid
    c.commit(); c.close()
    from datetime import datetime, timedelta
    later = datetime.utcnow() + timedelta(seconds=80)
    assert escalation.days_since(sqlite3.connect(tmp).execute(
        "SELECT issued_at FROM VIOLATION_NOTICE WHERE notice_id=?", (new,)).fetchone()[0], later) > 75
    assert escalation.days_since(sqlite3.connect(tmp).execute(
        "SELECT issued_at FROM VIOLATION_NOTICE WHERE notice_id=?", (old,)).fetchone()[0], later) < 4
    monkeypatch.delenv("DSX_ESCALATION_DAY_SECONDS")
    escalation.run_once(force=True)                                 # back to real time: demo clock removed
    assert escalation._demo_since is None
