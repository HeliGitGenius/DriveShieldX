"""Plate grammar / format-aware correction, registry providers and dispatch,
against a throw-away copy of the database."""
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detection.plate_format import correct, is_valid  # noqa: E402


@pytest.fixture()
def tmp_db(monkeypatch):
    from database import db_manager
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    shutil.copy(ROOT / "database" / "overspeed.db", tmp)
    monkeypatch.setattr(db_manager, "DB_PATH", tmp)
    yield tmp


def test_plate_grammar():
    assert is_valid("MH12AB1234") and is_valid("DL3CAF5031") and is_valid("22BH1234AB")
    assert not is_valid("XX12AB1234") and not is_valid("MH12AB123")


@pytest.mark.parametrize("ocr,truth", [("MH1ZAB1234", "MH12AB1234"), ("KAOSMN6789", "KA05MN6789"),
                                       ("MH12A81234", "MH12AB1234"), ("DL8CAF5O31", "DL8CAF5031")])
def test_format_aware_correction(ocr, truth):
    assert correct(ocr)[0] == truth


def test_no_correction_when_unfixable():
    assert correct("HELLOWORLD")[0] is None


def test_registry_local_and_flags(tmp_db):
    from backend.registry import Registry, compliance_flags, mask_name
    from database import db_manager
    c = db_manager.get_connection()
    row = c.execute("SELECT plate_number FROM REGISTERED_VEHICLE LIMIT 1").fetchone()
    c.close()
    if row:
        r = Registry().lookup(row["plate_number"], use_remote=False)
        assert r["found"] and r["provider"] == "local"
    assert mask_name("Heli Makwana") == "H*** M******"
    rec = {"insurance_upto": "2020-01-01", "pucc_upto": "31-12-2099", "blacklist_status": "Blacklisted"}
    assert set(compliance_flags(rec, date(2026, 1, 1))) == {"insurance_expired", "blacklisted"}


def test_http_provider_mapping(tmp_db, monkeypatch):
    from backend import registry
    monkeypatch.setenv("DSX_RC_API_URL", "https://example.invalid/rc")
    monkeypatch.setenv("DSX_RC_API_KEY", "k")

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": {"owner_name": "Test Owner", "maker_model": "HONDA ACTIVA",
                                         "insurance_upto": "2020-05-01"}}
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())
    r = registry.Registry().lookup("MH01ZZ9999")
    assert r["found"] and r["provider"] == "http" and r["record"]["owner_name"] == "T*** O****"
    assert "insurance_expired" in r["flags"]
    r2 = registry.Registry().lookup("MH01ZZ9999")          # served from cache now
    assert "cache" in r2["provider"]


def test_dispatch_nearest_and_log(tmp_db, monkeypatch):
    from backend import dispatch
    from database import safety_db
    monkeypatch.setenv("DSX_DISPATCH_MODE", "control_room")
    safety_db.set_camera_geo(1, 19.0269, 72.8553, "Test junction", phone="+910000000000")
    safety_db.upsert_facility("hospital", "Far Hospital", 19.20, 72.95)
    safety_db.upsert_facility("hospital", "Near Hospital", 19.03, 72.86)
    safety_db.upsert_facility("police", "Near Police", 19.025, 72.85)
    assert dispatch.nearest(19.0269, 72.8553, "hospital")[0]["name"] == "Near Hospital"
    aid = safety_db.insert_accident(1, None, 10, ["A", "B"], {"impact_iou": 0.3}, 0.9, None)
    res = dispatch.dispatch_accident(aid, 1, "2026-10-01 10:00", 0.9, {}, ["A", "B"])
    assert res["hospital"]["name"] == "Near Hospital" and res["police"]["name"] == "Near Police"
    assert ("sms", "+910000000000") in res["sent"]
    assert safety_db.list_accidents()[0]["status"] == "dispatched"
    assert abs(dispatch.haversine_km(19.0, 72.8, 19.0, 72.9) - 10.5) < 0.2
