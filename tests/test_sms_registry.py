"""Real-SMS plumbing (phone normalisation, allow-list safety, Twilio / Fast2SMS,
one e-challan SMS per challan) and the configurable RC-verification provider.
All HTTP is mocked and everything runs on a throw-away copy of the database."""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import alerts  # noqa: E402

OWNER_PHONE = "9000000004"


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d = status, data or {}
        self.text = json.dumps(self._d)

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture()
def env(monkeypatch):
    from database import db_manager
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    shutil.copy(ROOT / "database" / "overspeed.db", tmp)
    c = sqlite3.connect(tmp)             # the real DB may already have SMS history; start each test
    for t in ("CHALLAN_SMS", "ENFORCEMENT_LOG", "LICENCE_ACTION"):   # from a fresh baseline on the copy
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.commit(); c.close()
    monkeypatch.setattr(db_manager, "DB_PATH", tmp)
    monkeypatch.setattr(alerts, "OUTBOX_DIR", Path(tempfile.mkdtemp()))
    for k in ("DSX_SMS_PROVIDER", "DSX_SMS_ALLOWLIST", "DSX_SMS_ALLOW_ALL", "RAZORPAY_KEY_ID_TEST",
              "RAZORPAY_KEY_SECRET_TEST", "DSX_PAYMENT_MODE"):
        monkeypatch.delenv(k, raising=False)
    calls = []
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: calls.append((url, kw)) or Resp(201, {"sid": "SM1", "return": True}))
    return tmp, calls, monkeypatch


@pytest.mark.parametrize("raw,norm", [("98765 43210", "+919876543210"), ("+91-9876543210", "+919876543210"),
                                      ("09876543210", "+919876543210"), ("919876543210", "+919876543210"),
                                      ("12345", None), ("MH12AB1234", None), ("5876543210", None)])
def test_normalise(raw, norm):
    assert alerts.normalise_in_phone(raw) == norm


def test_no_gateway_is_outbox_only(env):
    tmp, calls, _ = env
    assert alerts.deliver_sms(OWNER_PHONE, "x")[1] == "outbox" and not calls


def test_allowlist_blocks_unknown_numbers(env):
    tmp, calls, mp = env
    mp.setenv("DSX_SMS_PROVIDER", "twilio")
    for k, v in {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t", "TWILIO_FROM": "+15550001111"}.items():
        mp.setenv(k, v)
    assert alerts.deliver_sms("9999999999", "x")[1] == "outbox" and not calls          # empty allow-list
    assert alerts.deliver_sms("MH12AB1234", "x")[1] == "outbox" and not calls          # plate, not a phone
    mp.setenv("DSX_SMS_ALLOWLIST", f"+91 {OWNER_PHONE}, 9000000000")
    assert alerts.deliver_sms(OWNER_PHONE, "hello")[1] == "delivered"
    url, kw = calls[-1]
    assert "Accounts/AC1/Messages.json" in url and kw["data"]["To"] == "+91" + OWNER_PHONE
    assert alerts.deliver_sms("9999999999", "x")[1] == "outbox" and len(calls) == 1
    mp.setenv("DSX_SMS_ALLOW_ALL", "1")
    assert alerts.deliver_sms("9999999999", "x")[1] == "delivered"


def test_fast2sms(env):
    tmp, calls, mp = env
    mp.setenv("DSX_SMS_PROVIDER", "fast2sms")
    mp.setenv("FAST2SMS_API_KEY", "k")
    mp.setenv("DSX_SMS_ALLOWLIST", OWNER_PHONE)
    assert alerts.deliver_sms(OWNER_PHONE, "hi")[1] == "delivered"
    url, kw = calls[-1]
    assert url.endswith("/dev/bulkV2") and kw["json"]["numbers"] == OWNER_PHONE and kw["json"]["route"] == "q"
    assert kw["headers"]["authorization"] == "k"
    import requests
    mp.setattr(requests, "post", lambda url, **kw: Resp(200, {"return": False, "message": "Insufficient balance"}))
    assert alerts.deliver_sms(OWNER_PHONE, "hi")[1].startswith("failed: fast2sms: Insufficient")


def test_smsgate(env):
    tmp, calls, mp = env
    mp.setenv("DSX_SMS_PROVIDER", "smsgate")
    mp.setenv("SMSGATE_USER", "u")
    mp.setenv("SMSGATE_PASSWORD", "p")
    mp.setenv("DSX_SMS_ALLOWLIST", OWNER_PHONE)
    assert alerts.deliver_sms(OWNER_PHONE, "hi")[1] == "delivered"
    url, kw = calls[-1]
    assert url == "https://api.sms-gate.app/3rdparty/v1/messages" and kw["auth"] == ("u", "p")
    assert kw["json"] == {"textMessage": {"text": "hi"}, "phoneNumbers": ["+91" + OWNER_PHONE]}
    mp.setenv("SMSGATE_URL", "http://192.168.1.20:8080/message")
    alerts.deliver_sms(OWNER_PHONE, "hi")
    assert calls[-1][0] == "http://192.168.1.20:8080/message"


def test_textbee(env):
    tmp, calls, mp = env
    mp.setenv("DSX_SMS_PROVIDER", "textbee")
    mp.setenv("TEXTBEE_API_KEY", "tb")
    mp.setenv("DSX_SMS_ALLOWLIST", OWNER_PHONE)
    assert alerts.deliver_sms(OWNER_PHONE, "hi")[1] == "delivered"
    url, kw = calls[-1]
    assert url == "https://api.textbee.dev/api/v1/gateway/send-sms"
    assert kw["json"] == {"recipients": ["+91" + OWNER_PHONE], "message": "hi"} and kw["headers"]["x-api-key"] == "tb"
    mp.setenv("TEXTBEE_DEVICE_ID", "dev1")
    alerts.deliver_sms(OWNER_PHONE, "hi")
    assert calls[-1][0].endswith("/gateway/devices/dev1/send-sms")


def _free_violation(tmp):
    """Every violation in the shipped DB already has a notice; free one up (temp copy only)."""
    c = sqlite3.connect(tmp)
    vid = c.execute("SELECT MAX(violation_id) FROM VIOLATION_NOTICE").fetchone()[0]
    c.execute("DELETE FROM VIOLATION_NOTICE WHERE violation_id=?", (vid,))
    c.commit(); c.close()
    return vid


def _new_notice(tmp, vid, owner_id=1, plate="MH09695"):
    c = sqlite3.connect(tmp)
    uid = c.execute("SELECT user_id FROM USER LIMIT 1").fetchone()[0]
    cur = c.execute("""INSERT INTO VIOLATION_NOTICE(violation_id, owner_id, plate_number, issued_by, amount, due_date,
                       payment_status) VALUES (?,?,?,?,?,?, 'pending')""", (vid, owner_id, plate, uid, 1000, "2026-10-15"))
    c.commit(); nid = cur.lastrowid; c.close()
    return nid


def test_one_sms_per_new_challan(env):
    tmp, calls, mp = env
    from backend import challan_sms
    mp.setenv("DSX_SMS_PROVIDER", "twilio")
    for k, v in {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t", "TWILIO_FROM": "+15550001111",
                 "DSX_SMS_ALLOWLIST": OWNER_PHONE}.items():
        mp.setenv(k, v)
    vid = _free_violation(tmp)
    assert challan_sms.notify_pending(0) == {"delivered": 0, "outbox": 0, "failed": 0, "no_phone": 0}
    assert not calls                                   # existing challans are baseline, never texted
    nid = _new_notice(tmp, vid)
    res = challan_sms.notify_pending(0)
    assert res["delivered"] == 1 and len(calls) == 1
    body = calls[0][1]["data"]["Body"]
    fine = sqlite3.connect(tmp).execute("SELECT amount FROM VIOLATION_NOTICE WHERE notice_id=?", (nid,)).fetchone()[0]
    assert fine in (500, 1000, 3000)                                   # set by the repeat-offender schedule
    assert f"e-Challan #{nid}" in body and "MH09695" in body and f"Rs {fine:,.0f}" in body and "15-Oct-2026" in body
    assert "offence" in body
    assert "log in to your DriveShieldX dashboard" in body
    assert challan_sms.notify_pending(0)["delivered"] == 0 and len(calls) == 1   # exactly once
    log = challan_sms.recent()
    assert log[0]["status"] == "delivered" and log[0]["phone_masked"] == "+91XXXXXX" + OWNER_PHONE[-4:]


def test_issued_rule_violation_gets_sms(env):
    tmp, calls, mp = env
    from backend import challan_sms
    challan_sms.notify_pending(0)                      # baseline
    c = sqlite3.connect(tmp)
    rid = c.execute("""INSERT INTO RULE_VIOLATION(session_id, tracker_id, rule_type, plate_text, reg_vehicle_id,
                       fine_amount, status) VALUES (999, 't1', 'no_helmet', 'MH09695', 1, 1000, 'issued')""").lastrowid
    c.commit(); c.close()
    res = challan_sms.notify_pending(0)
    assert res["outbox"] == 1                          # no gateway configured -> outbox file
    files = list(alerts.OUTBOX_DIR.glob("sms_*.txt"))
    assert any(f"R{rid}" in f.read_text(encoding="utf-8") and "without helmet" in f.read_text(encoding="utf-8") for f in files)


def test_rc_provider_template_headers_and_fields(env, monkeypatch):
    tmp, calls, mp = env
    import requests
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return Resp(200, {"result": {"source_output": {"owner_name": "RAHUL SHARMA", "insurance_upto": "2020-01-01",
                                                       "maker_model": "HONDA ACTIVA"}}})
    mp.setattr(requests, "post", fake_post)
    for k, v in {"DSX_RC_API_URL": "https://rc.example/verify", "DSX_RC_API_KEY": "secret",
                 "DSX_RC_API_AUTH": "header:api-key", "DSX_RC_API_HEADERS": '{"account-id": "acc1"}',
                 "DSX_RC_API_BODY_TEMPLATE": '{"task_id": "{uuid}", "data": {"rc_number": "{plate}"}}',
                 "DSX_RC_API_ROOT": "result.source_output"}.items():
        mp.setenv(k, v)
    from backend.registry import Registry, _keys
    reg = Registry()
    res = reg.lookup("KA05MN6789")
    assert seen["json"]["data"]["rc_number"] == "KA05MN6789" and len(seen["json"]["task_id"]) == 36
    assert seen["headers"]["api-key"] == "secret" and seen["headers"]["account-id"] == "acc1"
    assert res["found"] and res["record"]["owner_name"] == "R**** S*****" and "insurance_expired" in res["flags"]
    assert "result.source_output.owner_name" in _keys(reg.http.raw("KA05MN6789"))


def test_rc_provider_get(env):
    tmp, calls, mp = env
    import requests
    got = {}
    mp.setattr(requests, "get", lambda url, headers=None, timeout=None: got.update(url=url) or Resp(200, {"data": {"owner_name": "A B"}}))
    mp.setenv("DSX_RC_API_URL", "https://rc.example/v1/{plate}")
    mp.setenv("DSX_RC_API_KEY", "k")
    mp.setenv("DSX_RC_API_METHOD", "GET")
    from backend.registry import Registry
    assert Registry().lookup("KA05MN6789")["found"] and got["url"].endswith("/KA05MN6789")
