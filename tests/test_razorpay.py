"""Razorpay integration: link creation, both signature checks, idempotent settlement
across redirect + webhook + reconcile, amount check, live-mode cap and refund.
Razorpay's HTTP API is mocked; everything runs on a throw-away copy of the database."""
import hashlib
import hmac
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEY_ID, SECRET, WH_SECRET = "rzp_test_ABC123", "test_secret_xyz", "wh_secret_123"


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


class FakeRazorpay:
    """Minimal in-memory stand-in for the Razorpay REST endpoints the gateway calls."""

    def __init__(self):
        self.links, self.calls, self.n = {}, [], 0
        self.reject_callback = False
        self.auth, self.headers = None, {}

    def post(self, url, data=None, timeout=None):
        body = json.loads(data)
        self.calls.append(("POST", url, body))
        if url.endswith("/payment_links"):
            if self.reject_callback and "callback_url" in body:
                return FakeResp(400, {"error": {"description": "callback_url is invalid"}})
            self.n += 1
            lid = f"plink_TESTFIXTURE{self.n}"
            self.links[lid] = {"id": lid, "short_url": f"https://rzp.io/i/{lid}", "status": "created",
                               "amount": body["amount"], "reference_id": body["reference_id"], "payments": []}
            return FakeResp(200, self.links[lid])
        if url.endswith("/refund"):
            return FakeResp(200, {"id": "rfnd_T1", "status": "processed", "amount": body["amount"]})
        return FakeResp(404, {"error": "nope"})

    def get(self, url, timeout=None):
        self.calls.append(("GET", url, None))
        if "/payment_links/" in url:
            return FakeResp(200, self.links[url.rsplit("/", 1)[1]])
        if "/payments/" in url:
            return FakeResp(200, {"id": url.rsplit("/", 1)[1], "method": "upi", "status": "captured"})
        return FakeResp(404, {})

    def pay(self, lid, pid="pay_T1", method="upi"):
        self.links[lid]["status"] = "paid"
        self.links[lid]["payments"] = [{"payment_id": pid, "method": method, "status": "captured",
                                        "amount": self.links[lid]["amount"]}]


@pytest.fixture()
def rzp(monkeypatch):
    from database import db_manager
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    shutil.copy(ROOT / "database" / "overspeed.db", tmp)
    monkeypatch.setattr(db_manager, "DB_PATH", tmp)
    for k, v in {"DSX_PAYMENT_MODE": "test", "RAZORPAY_KEY_ID_TEST": KEY_ID, "RAZORPAY_KEY_SECRET_TEST": SECRET,
                 "RAZORPAY_WEBHOOK_SECRET": WH_SECRET, "DSX_PUBLIC_BASE_URL": "http://localhost:8501"}.items():
        monkeypatch.setenv(k, v)
    from backend import razorpay_gateway as g
    fake = FakeRazorpay()
    monkeypatch.setattr(g, "_session", lambda cfg: fake)
    sent = []
    import backend.notifications as notif
    monkeypatch.setattr(notif, "send_payment_confirmation", lambda **kw: sent.append(kw) or {"email": 0, "sms": 0})
    c = sqlite3.connect(tmp)
    nid = c.execute("SELECT notice_id FROM VIOLATION_NOTICE ORDER BY notice_id LIMIT 1").fetchone()[0]
    c.execute("UPDATE VIOLATION_NOTICE SET payment_status='pending', payment_method=NULL, paid_at=NULL WHERE notice_id=?", (nid,))
    c.commit(); c.close()
    return g, fake, sent, tmp, nid


def _notice(tmp, nid):
    c = sqlite3.connect(tmp); c.row_factory = sqlite3.Row
    r = dict(c.execute("SELECT * FROM VIOLATION_NOTICE WHERE notice_id=?", (nid,)).fetchone()); c.close()
    return r


def _callback(link, pid="pay_T1", status="paid", secret=SECRET):
    msg = f"{link['link_id']}|{link['reference_id']}|{status}|{pid}".encode()
    return {"razorpay_payment_id": pid, "razorpay_payment_link_id": link["link_id"],
            "razorpay_payment_link_reference_id": link["reference_id"], "razorpay_payment_link_status": status,
            "razorpay_signature": hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()}


def _webhook(link, amount_paise, pid="pay_T1"):
    body = json.dumps({"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": link["link_id"], "amount_paid": amount_paise, "status": "paid"}},
        "payment": {"entity": {"id": pid, "method": "upi", "amount": amount_paise, "status": "captured"}}}}).encode()
    return body, hmac.new(WH_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_not_enabled_without_keys(monkeypatch):
    from backend import razorpay_gateway as g
    monkeypatch.delenv("RAZORPAY_KEY_ID_TEST", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET_TEST", raising=False)
    monkeypatch.setenv("DSX_PAYMENT_MODE", "test")
    assert not g.enabled()


def test_key_must_match_mode(monkeypatch):
    from backend import razorpay_gateway as g
    monkeypatch.setenv("DSX_PAYMENT_MODE", "test")
    monkeypatch.setenv("RAZORPAY_KEY_ID_TEST", "rzp_live_oops")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET_TEST", "x")
    assert g.config() is None


def test_create_link_and_reuse(rzp):
    g, fake, _, tmp, nid = rzp
    owner = {"name": "Test Owner", "email": "o@example.org", "phone": "98765 43210"}
    link = g.create_payment_link({"notice_id": nid, "plate_number": "MH12AB1234"}, 500, owner)
    body = fake.calls[0][2]
    assert body["amount"] == 50000 and body["currency"] == "INR"
    assert body["customer"]["contact"] == "+919876543210" and body["notify"] == {"sms": True, "email": True}
    assert body["callback_url"] == "http://localhost:8501/" and body["callback_method"] == "get"
    again = g.create_payment_link({"notice_id": nid}, 500, owner)
    assert again["link_id"] == link["link_id"] and len(fake.calls) == 1   # open link reused


def test_redirect_then_webhook_settles_once(rzp):
    g, fake, sent, tmp, nid = rzp
    link = g.create_payment_link({"notice_id": nid}, 500)
    fake.pay(link["link_id"])
    ok, _ = g.handle_callback(_callback(link))
    assert ok and _notice(tmp, nid)["payment_status"] == "paid"
    n = _notice(tmp, nid)
    assert n["payment_method"] == "Razorpay UPI" and n["external_reference"].endswith("/pay_T1")
    body, sig = _webhook(link, 50000)
    assert g.handle_webhook(body, sig)[0]
    assert g.sync_link(link["link_id"]) == "paid"
    assert len(sent) == 1                       # one confirmation, not three
    from database import payments_db
    assert payments_db.get_link(link["link_id"])["settled_via"] == "redirect"


def test_link_without_callback_when_rejected(rzp):
    g, fake, sent, tmp, nid = rzp
    fake.reject_callback = True
    link = g.create_payment_link({"notice_id": nid}, 500)
    assert link["link_id"] and "callback_url" not in fake.calls[-1][2]


def test_forged_redirect_rejected(rzp):
    g, fake, sent, tmp, nid = rzp
    link = g.create_payment_link({"notice_id": nid}, 500)
    ok, msg = g.handle_callback(_callback(link, secret="wrong"))
    assert not ok and "Signature" in msg
    assert _notice(tmp, nid)["payment_status"] == "pending" and not sent


def test_webhook_signature_and_amount(rzp):
    g, fake, sent, tmp, nid = rzp
    link = g.create_payment_link({"notice_id": nid}, 500)
    body, sig = _webhook(link, 50000)
    assert g.handle_webhook(body, "bad")[0] is False
    short, ssig = _webhook(link, 100)
    assert g.handle_webhook(short, ssig) == (True, "amount mismatch")
    assert _notice(tmp, nid)["payment_status"] == "pending"
    assert g.handle_webhook(body, sig) == (True, "settled")
    assert _notice(tmp, nid)["payment_status"] == "paid" and len(sent) == 1


def test_reconcile_without_public_url(rzp):
    g, fake, sent, tmp, nid = rzp
    link = g.create_payment_link({"notice_id": nid}, 500)
    assert g.sync_open_links([nid]) == 0
    fake.pay(link["link_id"], pid="pay_T9", method="card")
    assert g.sync_open_links([nid]) == 1
    n = _notice(tmp, nid)
    assert n["payment_status"] == "paid" and n["payment_method"] == "Razorpay CARD"


def test_background_worker_settles_without_any_click(rzp, monkeypatch):
    g, fake, sent, tmp, nid = rzp
    from backend import payment_worker
    link = g.create_payment_link({"notice_id": nid}, 500)
    assert payment_worker.run_once() == 0 and _notice(tmp, nid)["payment_status"] == "pending"
    fake.pay(link["link_id"], pid="pay_AUTO")
    assert payment_worker.run_once() == 1
    assert _notice(tmp, nid)["payment_status"] == "paid" and len(sent) == 1
    assert payment_worker.run_once() == 0 and len(sent) == 1          # nothing twice
    from database import payments_db
    assert payments_db.get_link(link["link_id"])["settled_via"] == "reconcile"


def test_background_worker_starts_once(monkeypatch):
    from backend import payment_worker
    monkeypatch.setenv("DSX_PAYMENT_POLL_SECONDS", "3600")
    monkeypatch.setattr(payment_worker, "run_once", lambda: 0)
    try:
        assert payment_worker.start_background() is True
        assert payment_worker.start_background() is False
    finally:
        payment_worker.stop_background()
        payment_worker._thread.join(timeout=2)
    monkeypatch.setenv("DSX_PAYMENT_POLL_SECONDS", "0")
    assert payment_worker.start_background() is False


def test_live_cap(rzp, monkeypatch):
    g, fake, *_ , nid = rzp
    monkeypatch.setenv("DSX_PAYMENT_MODE", "live")
    monkeypatch.setenv("RAZORPAY_KEY_ID_LIVE", "rzp_live_X")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET_LIVE", "s")
    with pytest.raises(PermissionError):
        g.create_payment_link({"notice_id": nid}, 500)
    assert g.create_payment_link({"notice_id": nid}, 1)["mode"] == "live" and not fake.calls[0][2].get("notify")


def test_refund_reopens_challan(rzp):
    g, fake, sent, tmp, nid = rzp
    link = g.create_payment_link({"notice_id": nid}, 500)
    fake.pay(link["link_id"])
    g.sync_link(link["link_id"])
    rf = g.refund(link["link_id"])
    assert rf["id"] == "rfnd_T1" and fake.calls[-1][2]["amount"] == 50000
    assert _notice(tmp, nid)["payment_status"] == "pending"
    from database import payments_db
    assert payments_db.get_link(link["link_id"])["status"] == "refunded"


def test_real_db_untouched():
    """Test fixtures (plink_TESTFIXTURE*) must never leak into the real database. Real payments may exist."""
    c = sqlite3.connect(ROOT / "database" / "overspeed.db")
    if c.execute("SELECT name FROM sqlite_master WHERE name='PAYMENT_LINK'").fetchone() is None:
        return
    assert c.execute("SELECT COUNT(*) FROM PAYMENT_LINK WHERE link_id LIKE 'plink_TESTFIXTURE%'").fetchone()[0] == 0


def test_webhook_endpoint(rzp):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from backend.api_server import app
    g, fake, sent, tmp, nid = rzp
    link = g.create_payment_link({"notice_id": nid}, 500)
    body, sig = _webhook(link, 50000)
    client = TestClient(app)
    assert client.post("/razorpay/webhook", content=body, headers={"X-Razorpay-Signature": "x"}).status_code == 400
    r = client.post("/razorpay/webhook", content=body, headers={"X-Razorpay-Signature": sig})
    assert r.status_code == 200 and r.json()["detail"] == "settled"
    assert _notice(tmp, nid)["payment_status"] == "paid"
    assert client.get("/razorpay/status").json() == {"enabled": True, "mode": "test", "webhook_secret_set": True}
