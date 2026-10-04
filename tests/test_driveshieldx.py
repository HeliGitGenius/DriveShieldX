"""
DriveShieldX Phase-2 backend regression tests.

Covers: owner seeding, RULE_VIOLATION table CRUD, PDF challan enhancement,
SMS alert outbox writer, rule-violation pipeline hook, UPI intent + QR PNG,
codebase import sanity, and Streamlit dashboard boot.

Run:
    cd /app/workspace/project/OverSpeedX_phase2
    pytest tests/test_driveshieldx.py -v --tb=short \
        --junitxml=/app/test_reports/pytest/pytest_results.xml
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

# Make sure the project root is on sys.path so `detection.*` etc. resolve.
PROJECT_ROOT = Path("/app/workspace/project/OverSpeedX_phase2").resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Environment variables required by backend/payments.py + reporting.
os.environ.setdefault("UPI_ID", "driveshieldx@upi")
os.environ.setdefault("UPI_PAYEE_NAME", "DriveShieldX Traffic Authority")
os.environ.setdefault("CONTACT_PHONE", "+91-9000000000")


# ---------------------------------------------------------------------------
# Codebase import sanity
# ---------------------------------------------------------------------------
IMPORT_MODULES = [
    "detection.rule_violations",
    "detection.advanced_pipeline",
    "database.db_manager",
    "backend.reporting",
    "backend.payments",
    "backend.alerts",
    "dashboard.app",
]


@pytest.mark.parametrize("modname", IMPORT_MODULES)
def test_import_sanity(modname):
    """Every core module must import cleanly."""
    mod = importlib.import_module(modname)
    assert mod is not None


# ---------------------------------------------------------------------------
# Owner seeding
# ---------------------------------------------------------------------------
def test_owner_seeded():
    """owner@driveshield.com exists and has the three requested registered plates."""
    from database import db_manager as dbm  # noqa: WPS433

    conn = dbm.get_connection()
    row = conn.execute(
        "SELECT owner_id, email FROM OWNER_ACCOUNT WHERE email=?",
        ("owner@driveshield.com",),
    ).fetchone()
    assert row is not None, "owner@driveshield.com missing from OWNER_ACCOUNT"
    owner_id = row["owner_id"]

    plates = [
        r["plate_number"]
        for r in conn.execute(
            "SELECT plate_number FROM REGISTERED_VEHICLE WHERE owner_id=? ORDER BY plate_number",
            (owner_id,),
        ).fetchall()
    ]
    conn.close()
    assert "MH12AB1234" in plates
    assert "GJ01EF9012" in plates
    assert "DL03CD5678" in plates


def test_owner_rule_violations_returns_three_rows():
    """get_owner_rule_violations(owner_id) yields the 3 seeded demo rows."""
    from database import db_manager as dbm

    conn = dbm.get_connection()
    owner_row = conn.execute(
        "SELECT owner_id FROM OWNER_ACCOUNT WHERE email=?",
        ("owner@driveshield.com",),
    ).fetchone()
    conn.close()
    assert owner_row is not None
    rows = dbm.get_owner_rule_violations(owner_row["owner_id"])
    assert len(rows) == 3, f"expected 3 rule violations, got {len(rows)}"
    plates = {r["plate_text"] for r in rows}
    assert {"MH12AB1234", "GJ01EF9012", "DL03CD5678"}.issubset(plates)


# ---------------------------------------------------------------------------
# RULE_VIOLATION table + helpers
# ---------------------------------------------------------------------------
def test_rule_violation_table_exists():
    from database import db_manager as dbm

    conn = dbm.get_connection()
    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    conn.close()
    assert "RULE_VIOLATION" in tables


def test_insert_and_summary_roundtrip():
    """Insert a fresh row and confirm the summary counters reflect it."""
    from database import db_manager as dbm

    before = dbm.get_rule_violation_summary()
    new_id = dbm.insert_rule_violation(
        session_id=None,
        camera_id=1,
        tracker_id=f"TEST-{int(time.time())}",
        rule_type="no_helmet",
        plate_text="MH12AB1234",
        snapshot_path="snapshots/nonexistent.jpg",
        fine_amount=500.0,
    )
    assert new_id, "insert_rule_violation should return a positive rowid"

    after = dbm.get_rule_violation_summary()
    assert after["no_helmet"] == before["no_helmet"] + 1

    # Cleanup: purge the test row so subsequent runs stay idempotent.
    conn = dbm.get_connection()
    conn.execute("DELETE FROM RULE_VIOLATION WHERE rule_violation_id=?", (new_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# UPI intent + QR PNG
# ---------------------------------------------------------------------------
def test_upi_intent_prefix():
    from detection.rule_violations import upi_intent

    intent = upi_intent(
        "driveshieldx@upi",
        "DriveShieldX Traffic Authority",
        500,
        "42",
        "test",
    )
    assert intent.startswith("upi://pay?pa=driveshieldx%40upi"), intent
    assert "am=500" in intent
    assert "cu=INR" in intent
    assert "tr=" in intent


def test_upi_qr_png_bytes():
    from detection.rule_violations import upi_intent, upi_qr_png

    intent = upi_intent(
        "driveshieldx@upi",
        "DriveShieldX Traffic Authority",
        500,
        "42",
        "test",
    )
    png = upi_qr_png(intent)
    assert isinstance(png, (bytes, bytearray)), "upi_qr_png must return bytes"
    assert len(png) > 200, f"PNG too small: {len(png)} bytes"
    assert png[:4] == b"\x89PNG", f"missing PNG magic header, got {png[:8]!r}"


# ---------------------------------------------------------------------------
# PDF challan enhancement
# ---------------------------------------------------------------------------
def test_generate_challan_pdf_content():
    from backend import reporting

    notice = {
        "notice_id": 12345,
        "plate_number": "MH12AB1234",
        "speed_value": 82,
        "speed_limit": 60,
        "amount": 500,
        "late_fee": 0,
    }
    result = reporting.generate_challan_pdf(notice)
    # generate_challan_pdf may return a path string OR bytes.
    if isinstance(result, (bytes, bytearray)):
        pdf_bytes = bytes(result)
    else:
        pdf_path = Path(str(result))
        assert pdf_path.exists(), f"PDF not created at {pdf_path}"
        pdf_bytes = pdf_path.read_bytes()
    assert len(pdf_bytes) > 10_000, f"PDF too small: {len(pdf_bytes)} bytes"

    # Extract text via pypdf
    from pypdf import PdfReader
    import io

    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    for token in [
        "DriveShieldX Digital E-Challan",
        "Additional DriveShieldX",
        "MH12AB1234",
        "Pay via UPI",
        "helimakwana969",
        "No Helmet",
    ]:
        assert token in text, (
            f"token missing from PDF: {token!r}\n---extracted text---\n{text[:800]}"
        )


# ---------------------------------------------------------------------------
# SMS alert outbox writer
# ---------------------------------------------------------------------------
def test_send_sms_alert_writes_outbox_file():
    from backend import alerts

    outbox_dir = PROJECT_ROOT / "logs" / "alert_outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in outbox_dir.glob("sms_*.txt")}

    alerts.send_sms_alert("MH12AB1234", "test")

    new = {p.name for p in outbox_dir.glob("sms_*.txt")} - existing
    assert new, "send_sms_alert did not drop a new sms_*.txt file"


# ---------------------------------------------------------------------------
# Rule-violation pipeline hook (end-to-end run on demo clip)
# ---------------------------------------------------------------------------
def test_rule_violation_pipeline_hook():
    """Run detection.pipeline on the pan-crop demo clip.

    Zero rule-violation rows on this specific wide-angle CCTV clip is expected
    (seatbelt/helmet YOLOs do not fire from far). We only verify:
      * exit code == 0
      * a new VIDEO_SESSION row was written
      * no traceback surfaced
    """
    from database import db_manager as dbm

    # Regenerate /tmp/dsx_demo.mp4 if it is missing.
    demo_mp4 = Path("/tmp/dsx_demo.mp4")
    snap = PROJECT_ROOT / "snapshots" / "cam1_v47_20260527_120608_013055.jpg"
    if not demo_mp4.exists() and snap.exists():
        import cv2  # local import so the test suite still loads without cv2

        img = cv2.imread(str(snap))
        h, w = img.shape[:2]
        out_w = min(w, 960)
        out_h = min(h, 540)
        vw = cv2.VideoWriter(
            str(demo_mp4), cv2.VideoWriter_fourcc(*"mp4v"), 12, (out_w, out_h)
        )
        for i in range(40):
            xs = int(max(0, w - out_w) * i / 39)
            crop = img[:out_h, xs:xs + out_w]
            if crop.shape[:2] != (out_h, out_w):
                crop = cv2.resize(img, (out_w, out_h))
            vw.write(crop)
        vw.release()

    assert demo_mp4.exists(), "/tmp/dsx_demo.mp4 missing after regeneration"

    conn = dbm.get_connection()
    sessions_before = conn.execute("SELECT COUNT(*) FROM VIDEO_SESSION").fetchone()[0]
    conn.close()

    cmd = [
        sys.executable, "-m", "detection.pipeline",
        "--source", str(demo_mp4),
        "--camera-id", "1",
        "--tracker", "centroid",
        "--enable-npr",
        "--frame-skip", "3",
        "--max-frames", "15",
        "--resize-width", "720",
    ]
    proc = subprocess.run(
        cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=180
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    assert proc.returncode == 0, (
        f"pipeline exited {proc.returncode}\n---output---\n{combined[-2000:]}"
    )
    assert "Traceback" not in combined, (
        f"pipeline logged a traceback:\n{combined[-2000:]}"
    )

    conn = dbm.get_connection()
    sessions_after = conn.execute("SELECT COUNT(*) FROM VIDEO_SESSION").fetchone()[0]
    conn.close()
    assert sessions_after == sessions_before + 1, (
        f"expected a new VIDEO_SESSION row, before={sessions_before} after={sessions_after}"
    )

    # RuleViolationDetector should be importable without side effects.
    from detection.rule_violations import RuleViolationDetector  # noqa: F401


# ---------------------------------------------------------------------------
# Streamlit dashboard boot
# ---------------------------------------------------------------------------
def test_streamlit_dashboard_boot():
    """Launch `streamlit run dashboard/app.py`, curl the root, then shut down."""
    log_path = PROJECT_ROOT / "logs" / "test_streamlit_boot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # remove old log so we detect fresh tracebacks only
    if log_path.exists():
        log_path.unlink()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + ":" + env.get("PYTHONPATH", "")

    with log_path.open("wb") as fh:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "streamlit", "run", "dashboard/app.py",
                "--server.port", "8501",
                "--server.headless", "true",
                "--browser.gatherUsageStats", "false",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=fh, stderr=subprocess.STDOUT,
            env=env,
        )

    try:
        # Wait for server to come up (max 15s).
        status = None
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                r = requests.get("http://127.0.0.1:8501/", timeout=2)
                status = r.status_code
                if status == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)

        # Read log to check for tracebacks
        log_text = log_path.read_text(errors="ignore") if log_path.exists() else ""

        assert status == 200, (
            f"streamlit did not return 200 (got {status})\n---log---\n{log_text[-2000:]}"
        )
        assert "Traceback" not in log_text, f"streamlit log has traceback:\n{log_text[-2000:]}"
        assert "Exception" not in log_text or "ExceptionGroup" in log_text, (
            f"streamlit log has Exception:\n{log_text[-2000:]}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
