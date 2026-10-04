"""Automatic rule challans: detection -> plate -> registry -> e-challan, or officer review.
Builds its own empty database, so it needs no project data."""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def db(monkeypatch):
    from database import db_manager
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    monkeypatch.setattr(db_manager, "DB_PATH", tmp)
    for k in ("DSX_RC_API_URL", "DSX_AUTO_ISSUE_RULES", "DSX_AUTO_ISSUER_ID"):
        monkeypatch.delenv(k, raising=False)
    db_manager.init_database()
    c = sqlite3.connect(tmp)
    oid = c.execute("INSERT INTO OWNER_ACCOUNT(name, email, password_hash, phone) VALUES "
                    "('Auto Owner','auto@test.local','x','9000000002')").lastrowid
    c.execute("INSERT INTO REGISTERED_VEHICLE(owner_id, plate_number) VALUES (?, 'MH 02 AB 1234')", (oid,))
    # a detection that existed before the module ran: must be left alone
    c.execute("INSERT INTO RULE_VIOLATION(tracker_id, rule_type, plate_text) VALUES ('old','no_helmet','MH02AB1234')")
    c.commit(); c.close()
    from backend import auto_issue
    auto_issue.run_once()          # first run: baseline only
    return tmp, oid


def add(tmp, tid, rule, plate):
    c = sqlite3.connect(tmp)
    rid = c.execute("INSERT INTO RULE_VIOLATION(tracker_id, rule_type, plate_text) VALUES (?,?,?)",
                    (tid, rule, plate)).lastrowid
    c.commit(); c.close()
    return rid


def test_verified_plate_is_issued_others_go_to_officer(db):
    tmp, oid = db
    from backend import auto_issue
    ok = add(tmp, "t1", "no_helmet", "MH02AB1234")
    unknown = add(tmp, "t2", "three_seater", "KA05MN4321")
    misread = add(tmp, "t3", "no_seatbelt", "XX1")
    blank = add(tmp, "t4", "no_helmet", "")
    counts = auto_issue.run_once()
    assert counts == {"issued": 1, "not_registered": 1, "invalid_plate": 1, "no_plate": 1}
    c = sqlite3.connect(tmp)
    n = c.execute("SELECT owner_id, amount, violation_kind FROM VIOLATION_NOTICE WHERE rule_violation_id=?",
                  (ok,)).fetchone()
    assert n[0] == oid and n[1] >= 1000 and n[2] == "no_helmet"          # MV Act s.194D at least
    assert c.execute("SELECT COUNT(*) FROM VIOLATION_NOTICE WHERE rule_violation_id IN (?,?,?)",
                     (unknown, misread, blank)).fetchone()[0] == 0
    st = dict(c.execute("SELECT rule_violation_id, status FROM RULE_VIOLATION").fetchall())
    assert st[ok] == "issued" and st[unknown] == "detected"
    old = c.execute("SELECT outcome FROM AUTO_ISSUE a JOIN RULE_VIOLATION r USING(rule_violation_id) "
                    "WHERE r.tracker_id='old'").fetchone()[0]
    assert old == "preexisting"
    assert c.execute("SELECT COUNT(*) FROM VIOLATION_NOTICE").fetchone()[0] == 1
    c.close()
    assert auto_issue.run_once() == {}                                     # each detection checked once
    reasons = {r["outcome"]: r["reason"] for r in auto_issue.recent()}
    assert "registry" in reasons["not_registered"]


def test_switch_off(db, monkeypatch):
    tmp, _ = db
    from backend import auto_issue
    add(tmp, "t9", "no_helmet", "MH02AB1234")
    monkeypatch.setenv("DSX_AUTO_ISSUE_RULES", "0")
    assert auto_issue.run_once() == {}
