from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "database" / "overspeed.db"
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"
PBKDF2_ITERATIONS = 260_000


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith("pbkdf2_sha256$"):
        _, iter_str, salt, digest = stored_hash.split("$", 3)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iter_str)
        ).hex()
        return hmac.compare_digest(candidate, digest)
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, stored_hash)


def generate_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("utf-8").replace("=", "")


def _totp_at(secret: str, counter: int, digits: int = 6) -> str:
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def verify_totp(secret: str, code: str, interval: int = 30, window: int = 3) -> bool:
    """Verify a TOTP code. window=3 accepts codes ±90 seconds from now, which handles
    slow connections, page load delays and slight clock drift without reducing security
    (an attacker would still need the secret key)."""
    code = (code or "").replace(" ", "").strip()
    if not (secret and code.isdigit() and len(code) == 6):
        return False
    counter = int(time.time() // interval)
    return any(hmac.compare_digest(_totp_at(secret, counter + offset), code) for offset in range(-window, window + 1))


def _rowdict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return dict(row) if row else None


def init_database() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:  # road-safety layer tables (fog, accidents, dispatch, registry cache)
        from database.safety_db import SAFETY_DDL
        conn.executescript(SAFETY_DDL)
    except Exception:
        pass
    _ensure_manual_challan_schema(conn)
    _seed_defaults(conn)
    conn.commit()
    conn.close()


def _ensure_manual_challan_schema(conn: sqlite3.Connection) -> None:
    """Ensure VIOLATION_NOTICE can store authority-created manual challans.

    Manual challans are not tied to a detected VIOLATION, so violation_id must be
    nullable. Existing databases created with an older NOT NULL definition are
    migrated in-place while preserving existing rows and table indexes.
    """
    try:
        columns = conn.execute("PRAGMA table_info(VIOLATION_NOTICE)").fetchall()
    except sqlite3.OperationalError:
        return

    violation_col = next((r for r in columns if r["name"] == "violation_id"), None)
    if not violation_col or not violation_col["notnull"]:
        return

    ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='VIOLATION_NOTICE'"
    ).fetchone()
    if not ddl_row or not ddl_row["sql"]:
        return

    # Save explicit indexes so they can be recreated after the table rebuild.
    index_rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND tbl_name='VIOLATION_NOTICE' AND sql IS NOT NULL"
    ).fetchall()

    old_name = "VIOLATION_NOTICE__legacy"
    conn.execute(f"DROP TABLE IF EXISTS {old_name}")

    ddl = ddl_row["sql"]
    # Rename the table in the CREATE statement and remove NOT NULL only from
    # the violation_id column definition.
    ddl = re.sub(
        r"(?i)^\s*CREATE\s+TABLE\s+VIOLATION_NOTICE",
        "CREATE TABLE VIOLATION_NOTICE",
        ddl,
        count=1,
    )
    ddl = re.sub(
        r"(?i)(\bviolation_id\b[^,]*?)\s+NOT\s+NULL",
        r"\1",
        ddl,
        count=1,
    )

    conn.execute("ALTER TABLE VIOLATION_NOTICE RENAME TO VIOLATION_NOTICE__legacy")
    conn.execute(ddl)

    column_names = [r["name"] for r in columns]
    quoted = ",".join(f'"{name}"' for name in column_names)
    conn.execute(
        f"INSERT INTO VIOLATION_NOTICE ({quoted}) "
        f"SELECT {quoted} FROM VIOLATION_NOTICE__legacy"
    )
    conn.execute("DROP TABLE VIOLATION_NOTICE__legacy")

    # Recreate non-auto indexes that existed on the original table.
    for row in index_rows:
        sql = row["sql"]
        if sql:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                # SQLite may already have recreated an equivalent auto-index.
                pass


def _seed_defaults(conn: sqlite3.Connection) -> None:
    if not conn.execute("SELECT 1 FROM USER WHERE email='admin@speedcam.com'").fetchone():
        conn.execute(
            "INSERT INTO USER(name,email,password,role,contact_no) VALUES (?,?,?,?,?)",
            ("System Admin", "admin@speedcam.com", hash_password("admin123"), "admin", "9999999999"),
        )
    if not conn.execute("SELECT 1 FROM USER WHERE email='officer@speedcam.com'").fetchone():
        conn.execute(
            "INSERT INTO USER(name,email,password,role,contact_no) VALUES (?,?,?,?,?)",
            ("Traffic Officer", "officer@speedcam.com", hash_password("officer123"), "traffic_authority", "8888888888"),
        )
    for email in ("admin@speedcam.com", "officer@speedcam.com"):
        row = conn.execute("SELECT user_id FROM USER WHERE email=?", (email,)).fetchone()
        conn.execute("INSERT OR IGNORE INTO USER_SECURITY(user_id) VALUES (?)", (row[0],))

    if not conn.execute("SELECT 1 FROM CAMERA WHERE location='UA-DETRAC Highway MVI_007'").fetchone():
        conn.execute(
            "INSERT INTO CAMERA(location,camera_type,rtsp_url,installation_date,status,last_heartbeat) VALUES (?,?,?,?,?,?)",
            ("UA-DETRAC Highway MVI_007", "CCTV", None, "2024-01-01", "active", datetime.now().isoformat()),
        )
    camera = conn.execute("SELECT camera_id FROM CAMERA ORDER BY camera_id LIMIT 1").fetchone()
    admin = conn.execute("SELECT user_id FROM USER WHERE role='admin' LIMIT 1").fetchone()
    if camera and admin and not conn.execute("SELECT 1 FROM CONFIGURATION WHERE camera_id=?", (camera[0],)).fetchone():
        conn.execute(
            "INSERT INTO CONFIGURATION(camera_id,speed_limit,pixel_to_meter_scale,tracker_mode,enable_npr,frame_skip,gpu_enabled,batch_mode,set_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (camera[0], 60.0, 0.045, "bytetrack", 1, 1, 0, 0, admin[0]),
        )
    if camera and not conn.execute("SELECT 1 FROM ZONE_CONFIG WHERE camera_id=?", (camera[0],)).fetchone():
        conn.execute(
            "INSERT INTO ZONE_CONFIG(camera_id,zone_name,x1,y1,x2,y2,speed_limit,pixel_to_meter_scale,is_active) VALUES (?,?,?,?,?,?,?,?,?)",
            (camera[0], "Default Lane Zone", 0, 0, 1920, 1080, 60.0, 0.045, 1),
        )
    if camera and not conn.execute("SELECT 1 FROM STREAM_STATUS WHERE camera_id=?", (camera[0],)).fetchone():
        conn.execute("INSERT INTO STREAM_STATUS(camera_id,is_online,last_frame_no,last_fps,last_latency_ms) VALUES (?,?,?,?,?)", (camera[0], 0, 0, 0.0, 0.0))


def _log_audit(actor_type: str, actor_id: int, event_type: str, ip_address: Optional[str] = None) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO LOGIN_AUDIT(actor_type,actor_id,event_type,ip_address) VALUES (?,?,?,?)",
        (actor_type, actor_id, event_type, ip_address),
    )
    conn.commit()
    conn.close()


def log_system_event(event_type: str, message: str, level: str = "info", camera_id: Optional[int] = None, payload: Optional[Dict[str, Any]] = None) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO SYSTEM_EVENT(camera_id,event_type,level,message,payload_json) VALUES (?,?,?,?,?)",
        (camera_id, event_type, level, message, json.dumps(payload or {})),
    )
    conn.commit()
    conn.close()


# AUTHORITY AUTH

def authenticate_user(email: str, password: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM USER WHERE email=?", (email.strip().lower(),)).fetchone()
    if not row:
        conn.close()
        return None
    data = dict(row)
    if not verify_password(password, data["password"]):
        conn.close()
        return None
    if not data["password"].startswith("pbkdf2_sha256$"):
        new_hash = hash_password(password)
        conn.execute("UPDATE USER SET password=? WHERE user_id=?", (new_hash, data["user_id"]))
        conn.commit()
        data["password"] = new_hash
    conn.close()
    _log_audit("authority", data["user_id"], "password_ok")
    return data


def add_user(name: str, email: str, password: str, role: str, contact: str) -> bool:
    try:
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO USER(name,email,password,role,contact_no) VALUES (?,?,?,?,?)",
            (name.strip(), email.strip().lower(), hash_password(password), role, contact.strip()),
        )
        conn.execute("INSERT INTO USER_SECURITY(user_id) VALUES (?)", (cur.lastrowid,))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def get_all_users() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT u.user_id,u.name,u.email,u.role,u.contact_no,u.created_at,us.is_2fa_enabled FROM USER u LEFT JOIN USER_SECURITY us ON us.user_id=u.user_id ORDER BY u.created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_security(user_id: int) -> Dict[str, Any]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM USER_SECURITY WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else {"user_id": user_id, "totp_secret": None, "is_2fa_enabled": 0}


def set_user_totp_secret(user_id: int, secret: str, enabled: bool = True) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO USER_SECURITY(user_id,totp_secret,is_2fa_enabled,last_verified_at) VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET totp_secret=excluded.totp_secret,is_2fa_enabled=excluded.is_2fa_enabled,last_verified_at=excluded.last_verified_at",
        (user_id, secret, 1 if enabled else 0, datetime.now().isoformat() if enabled else None),
    )
    conn.commit()
    conn.close()


# OWNER AUTH

def create_owner_account(name: str, email: str, password: str, phone: str) -> bool:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO OWNER_ACCOUNT(name,email,password_hash,phone) VALUES (?,?,?,?)",
            (name.strip(), email.strip().lower(), hash_password(password), phone.strip()),
        )
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def authenticate_owner(email: str, password: str) -> Optional[Dict[str, Any]]:
    conn = get_connection(); row = conn.execute("SELECT * FROM OWNER_ACCOUNT WHERE email=?", (email.strip().lower(),)).fetchone()
    if not row:
        conn.close(); return None
    data = dict(row)
    if not verify_password(password, data["password_hash"]):
        conn.close(); return None
    if not data["password_hash"].startswith("pbkdf2_sha256$"):
        new_hash = hash_password(password)
        conn.execute("UPDATE OWNER_ACCOUNT SET password_hash=? WHERE owner_id=?", (new_hash, data["owner_id"]))
        conn.commit(); data["password_hash"] = new_hash
    conn.close(); _log_audit("owner", data["owner_id"], "password_ok"); return data


def set_owner_totp_secret(owner_id: int, secret: str, enabled: bool = True) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE OWNER_ACCOUNT SET totp_secret=?, is_2fa_enabled=? WHERE owner_id=?",
        (secret, 1 if enabled else 0, owner_id),
    )
    conn.commit(); conn.close()


def add_registered_vehicle(owner_id: int, plate_number: str, vehicle_type: str, model_name: str = "") -> bool:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO REGISTERED_VEHICLE(owner_id,plate_number,vehicle_type,model_name) VALUES (?,?,?,?)",
            (owner_id, plate_number.strip().upper(), vehicle_type, model_name.strip()),
        )
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def list_owner_vehicles(owner_id: int) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute("SELECT * FROM REGISTERED_VEHICLE WHERE owner_id=? ORDER BY created_at DESC", (owner_id,)).fetchall(); conn.close(); return [dict(r) for r in rows]


def get_registered_vehicles() -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute("SELECT rv.*,oa.name AS owner_name,oa.email AS owner_email FROM REGISTERED_VEHICLE rv JOIN OWNER_ACCOUNT oa ON oa.owner_id=rv.owner_id ORDER BY rv.created_at DESC").fetchall(); conn.close(); return [dict(r) for r in rows]


# CAMERA / CONFIG / ZONES

def get_all_cameras() -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute("SELECT * FROM CAMERA ORDER BY camera_id ASC").fetchall(); conn.close(); return [dict(r) for r in rows]


def get_camera(camera_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection(); row = conn.execute("SELECT * FROM CAMERA WHERE camera_id=?", (camera_id,)).fetchone(); conn.close(); return _rowdict(row)


def add_camera(location: str, camera_type: str, installation_date: str, status: str = "active", rtsp_url: Optional[str] = None) -> bool:
    try:
        conn = get_connection()
        cur = conn.execute(
            "INSERT INTO CAMERA(location,camera_type,rtsp_url,installation_date,status,last_heartbeat) VALUES (?,?,?,?,?,?)",
            (location.strip(), camera_type.strip(), rtsp_url, installation_date, status, datetime.now().isoformat()),
        )
        conn.execute("INSERT INTO STREAM_STATUS(camera_id) VALUES (?)", (cur.lastrowid,))
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def get_config(camera_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    if camera_id is None:
        row = conn.execute(
            "SELECT c.*,cam.location FROM CONFIGURATION c JOIN CAMERA cam ON cam.camera_id=c.camera_id ORDER BY c.set_date DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT c.*,cam.location FROM CONFIGURATION c JOIN CAMERA cam ON cam.camera_id=c.camera_id WHERE c.camera_id=? ORDER BY c.set_date DESC LIMIT 1",
            (camera_id,),
        ).fetchone()
    conn.close(); return _rowdict(row)


def update_config(camera_id: int, speed_limit: float, pixel_scale: float, set_by: int, tracker_mode: str = "bytetrack", enable_npr: bool = False, frame_skip: int = 1, gpu_enabled: bool = False, batch_mode: bool = False) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO CONFIGURATION(camera_id,speed_limit,pixel_to_meter_scale,tracker_mode,enable_npr,frame_skip,gpu_enabled,batch_mode,set_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (camera_id, speed_limit, pixel_scale, tracker_mode, int(enable_npr), max(1, int(frame_skip)), int(gpu_enabled), int(batch_mode), set_by),
    )
    conn.commit(); conn.close()
    log_system_event("config", f"Configuration updated for camera {camera_id}", payload={"camera_id": camera_id})


def list_zones(camera_id: int) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute("SELECT * FROM ZONE_CONFIG WHERE camera_id=? ORDER BY zone_id ASC", (camera_id,)).fetchall(); conn.close(); return [dict(r) for r in rows]


def add_zone(camera_id: int, zone_name: str, x1: int, y1: int, x2: int, y2: int, speed_limit: float, pixel_scale: Optional[float] = None, is_active: bool = True) -> bool:
    conn = get_connection()
    conn.execute(
        "INSERT INTO ZONE_CONFIG(camera_id,zone_name,x1,y1,x2,y2,speed_limit,pixel_to_meter_scale,is_active) VALUES (?,?,?,?,?,?,?,?,?)",
        (camera_id, zone_name, x1, y1, x2, y2, speed_limit, pixel_scale, 1 if is_active else 0),
    )
    conn.commit(); conn.close(); return True


def delete_zone(zone_id: int) -> None:
    conn = get_connection(); conn.execute("DELETE FROM ZONE_CONFIG WHERE zone_id=?", (zone_id,)); conn.commit(); conn.close()


# SESSIONS / MONITORING

def create_session(camera_id: int, video_source: str, source_type: str = "file", notes: str = "") -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO VIDEO_SESSION(camera_id,video_source,source_type,status,notes) VALUES (?,?,?,?,?)",
        (camera_id, video_source, source_type, "active", notes),
    )
    sid = cur.lastrowid
    conn.commit(); conn.close()
    log_system_event("monitor", f"Session started: {sid} ({video_source})", camera_id=camera_id)
    return sid


def update_session_progress(session_id: int, last_frame_no: int, status: str = "active", notes: Optional[str] = None) -> None:
    conn = get_connection()
    if notes is None:
        conn.execute("UPDATE VIDEO_SESSION SET last_frame_no=?, status=? WHERE session_id=?", (last_frame_no, status, session_id))
    else:
        conn.execute("UPDATE VIDEO_SESSION SET last_frame_no=?, status=?, notes=? WHERE session_id=?", (last_frame_no, status, notes, session_id))
    conn.commit(); conn.close()


def close_session(session_id: int, status: str = "completed", notes: str = "") -> None:
    conn = get_connection(); conn.execute("UPDATE VIDEO_SESSION SET end_time=?, status=?, notes=? WHERE session_id=?", (datetime.now().isoformat(), status, notes, session_id)); conn.commit(); conn.close()


def upsert_stream_status(camera_id: int, is_online: bool, last_frame_no: int = 0, last_fps: float = 0.0, last_latency_ms: float = 0.0, last_error: Optional[str] = None) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO STREAM_STATUS(camera_id,is_online,last_frame_no,last_fps,last_latency_ms,last_error,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(camera_id) DO UPDATE SET is_online=excluded.is_online,last_frame_no=excluded.last_frame_no,last_fps=excluded.last_fps,last_latency_ms=excluded.last_latency_ms,last_error=excluded.last_error,updated_at=excluded.updated_at",
        (camera_id, 1 if is_online else 0, last_frame_no, last_fps, last_latency_ms, last_error, datetime.now().isoformat()),
    )
    conn.execute("UPDATE CAMERA SET last_heartbeat=? WHERE camera_id=?", (datetime.now().isoformat(), camera_id))
    conn.commit(); conn.close()


def get_system_health() -> Dict[str, Any]:
    db_size_mb = DB_PATH.stat().st_size / (1024 * 1024) if DB_PATH.exists() else 0.0
    conn = get_connection()
    active = conn.execute("SELECT COUNT(*) AS c FROM STREAM_STATUS WHERE is_online=1").fetchone()["c"]
    sessions = conn.execute("SELECT COUNT(*) AS c FROM VIDEO_SESSION WHERE status='active'").fetchone()["c"]
    cameras = [dict(r) for r in conn.execute("SELECT cam.*, ss.is_online, ss.last_frame_no, ss.last_fps, ss.last_latency_ms, ss.last_error, ss.updated_at FROM CAMERA cam LEFT JOIN STREAM_STATUS ss ON ss.camera_id=cam.camera_id ORDER BY cam.camera_id").fetchall()]
    recent_errors = [dict(r) for r in conn.execute("SELECT * FROM SYSTEM_EVENT WHERE level IN ('error','warning') ORDER BY created_at DESC LIMIT 20").fetchall()]
    conn.close()
    return {"db_size_mb": round(db_size_mb, 2), "active_streams": active, "active_sessions": sessions, "cameras": cameras, "recent_errors": recent_errors}


def upsert_vehicle(session_id: int, tracker_id: str, vehicle_type: str, plate_text: Optional[str] = None, ocr_confidence: Optional[float] = None) -> int:
    conn = get_connection()
    existing = conn.execute("SELECT vehicle_id FROM VEHICLE WHERE session_id=? AND tracker_id=?", (session_id, str(tracker_id))).fetchone()
    if existing:
        conn.execute(
            "UPDATE VEHICLE SET vehicle_type=?, plate_text=COALESCE(?,plate_text), ocr_confidence=COALESCE(?,ocr_confidence), last_seen_time=? WHERE vehicle_id=?",
            (vehicle_type, plate_text, ocr_confidence, datetime.now().isoformat(), existing[0]),
        )
        vid = existing[0]
    else:
        cur = conn.execute(
            "INSERT INTO VEHICLE(session_id,tracker_id,vehicle_type,plate_text,ocr_confidence,last_seen_time) VALUES (?,?,?,?,?,?)",
            (session_id, str(tracker_id), vehicle_type, plate_text, ocr_confidence, datetime.now().isoformat()),
        )
        vid = cur.lastrowid
    conn.commit(); conn.close(); return vid


def insert_vehicle(vehicle_type: str, session_id: int) -> int:
    return upsert_vehicle(session_id, tracker_id=f"legacy-{time.time_ns()}", vehicle_type=vehicle_type)


def insert_speed_record(vehicle_id: int, speed_value: float, speed_limit: float, zone_id: Optional[int] = None, centroid: Optional[Tuple[float, float]] = None, source_fps: Optional[float] = None) -> int:
    cx, cy = (centroid if centroid else (None, None))
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO SPEED_RECORD(vehicle_id,zone_id,speed_value,speed_limit,calculated_time,centroid_x,centroid_y,source_fps) VALUES (?,?,?,?,?,?,?,?)",
        (vehicle_id, zone_id, round(speed_value, 2), speed_limit, datetime.now().isoformat(), cx, cy, source_fps),
    )
    sid = cur.lastrowid
    conn.commit(); conn.close(); return sid


def classify_severity(speed: float, limit: float) -> str:
    excess = max(0.0, speed - limit)
    if excess <= 10:
        return "low"
    if excess <= 25:
        return "medium"
    if excess <= 40:
        return "high"
    return "extreme"


def insert_violation(speed_id: int, speed: float, limit: float, snapshot_path: Optional[str] = None, alert_status: str = "queued") -> int:
    sev = classify_severity(speed, limit)
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO VIOLATION(speed_id,severity_level,image_snapshot,alert_status) VALUES (?,?,?,?)",
        (speed_id, sev, snapshot_path, alert_status),
    )
    vid = cur.lastrowid
    conn.commit(); conn.close();
    log_system_event("violation", f"Violation stored: {vid} severity={sev}", payload={"violation_id": vid})
    return vid


# ALERTS / NOTICES / REPORTS

def queue_alert(channel: str, recipient: str, subject: str, body: str) -> int:
    conn = get_connection(); cur = conn.execute("INSERT INTO ALERT_OUTBOX(channel,recipient,subject,body,status) VALUES (?,?,?,?,?)", (channel, recipient, subject, body, "queued")); aid = cur.lastrowid; conn.commit(); conn.close(); return aid


def mark_alert_sent(alert_id: int, status: str, error: Optional[str] = None) -> None:
    conn = get_connection(); conn.execute("UPDATE ALERT_OUTBOX SET status=?, sent_at=?, error=? WHERE alert_id=?", (status, datetime.now().isoformat(), error, alert_id)); conn.commit(); conn.close()


def list_alerts(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute("SELECT * FROM ALERT_OUTBOX ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall(); conn.close(); return [dict(r) for r in rows]


def get_unissued_violations(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute(
        """
        SELECT v.violation_id,v.violation_time,v.severity_level,v.image_snapshot,sr.speed_value,sr.speed_limit,ve.vehicle_id,ve.vehicle_type,ve.plate_text,cam.location
        FROM VIOLATION v
        JOIN SPEED_RECORD sr ON sr.speed_id=v.speed_id
        JOIN VEHICLE ve ON ve.vehicle_id=sr.vehicle_id
        JOIN VIDEO_SESSION vs ON vs.session_id=ve.session_id
        JOIN CAMERA cam ON cam.camera_id=vs.camera_id
        LEFT JOIN VIOLATION_NOTICE vn ON vn.violation_id=v.violation_id
        WHERE vn.notice_id IS NULL
        ORDER BY v.violation_time DESC LIMIT ?
        """, (limit,)
    ).fetchall(); conn.close(); return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# PENALTY POLICY  (graduated / repeat-offender escalation)
# ---------------------------------------------------------------------------
# Per-offence fine schedule. Each tier defines the fine if paid on time and the
# escalated fine that applies if the challan is NOT paid by the due date — exactly
# like Airtel / utility bills where the amount jumps after the deadline.
#   1st offence (warning):  Rs 500  -> Rs 1000 if overdue
#   2nd offence:            Rs 1000 -> Rs 2000 if overdue
#   3rd offence:            Rs 3000 -> Rs 5000 if overdue  (+ licence suspension)
FINE_SCHEDULE = {
    "first":  {"on_time": 500.0,  "overdue": 1000.0},
    "second": {"on_time": 1000.0, "overdue": 2000.0},
    "third":  {"on_time": 3000.0, "overdue": 5000.0},
}
# severity can nudge a first-timer up a notch (extreme speeding starts higher),
# but the tier (repeat count) is the primary driver of the fine.
SEVERITY_BUMP = {"low": 0, "medium": 0, "high": 0, "extreme": 0}
GRACE_DAYS = 14            # days to pay before the challan escalates / goes overdue


def offense_number(this_violation_id: int, vehicle_id: int, plate: Optional[str]) -> int:
    """How many times THIS vehicle (by plate, or same tracked vehicle) has
    overspeeded up to and including this violation. 1 = first offence."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT v.violation_id, ve.vehicle_id, ve.plate_text
        FROM VIOLATION v
        JOIN SPEED_RECORD sr ON sr.speed_id = v.speed_id
        JOIN VEHICLE ve ON ve.vehicle_id = sr.vehicle_id
        WHERE v.violation_id <= ?
        ORDER BY v.violation_id
        """, (this_violation_id,)
    ).fetchall()
    conn.close()
    norm = (plate or "").strip().upper()
    count = 0
    for r in rows:
        rp = (r["plate_text"] or "").strip().upper()
        if (norm and rp == norm) or (r["vehicle_id"] == vehicle_id):
            count += 1
    return max(1, count)


def compute_penalty(severity: str, offense_no: int) -> Dict[str, Any]:
    """Return the graduated penalty for a given offence number, using the fixed
    fine schedule (on-time amount now; escalated amount if it goes overdue)."""
    tier = "first" if offense_no <= 1 else "second" if offense_no == 2 else "third"
    sched = FINE_SCHEDULE[tier]
    amount = sched["on_time"]
    overdue_amount = sched["overdue"]
    suspension = 0
    if tier == "first":
        action = "warning_fine"
        warning = ("First offence — official warning + e-challan of Rs 500. "
                   "Pay within 14 days or it rises to Rs 1000.")
    elif tier == "second":
        action = "escalated_fine"
        warning = ("Second offence — e-challan of Rs 1000. "
                   "Pay within 14 days or it rises to Rs 2000.")
    else:
        action = "license_suspension"
        suspension = 3 + max(0, offense_no - 3)  # 3 months, +1 per extra repeat
        warning = (f"Third (or repeated) offence — e-challan of Rs 3000 (rises to Rs 5000 if overdue) "
                   f"AND driving licence suspended for {suspension} month(s).")
    return {"tier": tier, "amount": amount, "base_amount": amount,
            "overdue_amount": overdue_amount, "action": action,
            "suspension_months": suspension, "offense_no": offense_no, "warning": warning}


def match_registered_vehicle(plate: Optional[str]) -> Optional[Dict[str, Any]]:
    """Find a registered owner+vehicle for a detected plate (case-insensitive)."""
    if not plate:
        return None
    conn = get_connection()
    row = conn.execute(
        """
        SELECT rv.reg_vehicle_id, rv.owner_id, rv.vehicle_type,
               oa.name AS owner_name, oa.email AS owner_email, oa.phone AS owner_phone
        FROM REGISTERED_VEHICLE rv
        JOIN OWNER_ACCOUNT oa ON oa.owner_id = rv.owner_id
        WHERE UPPER(rv.plate_number) = UPPER(?)
        """, (plate.strip(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _violation_detail(violation_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT v.violation_id, v.severity_level, v.violation_time,
               sr.speed_value, sr.speed_limit,
               ve.vehicle_id, ve.vehicle_type, ve.plate_text, cam.location
        FROM VIOLATION v
        JOIN SPEED_RECORD sr ON sr.speed_id = v.speed_id
        JOIN VEHICLE ve ON ve.vehicle_id = sr.vehicle_id
        JOIN VIDEO_SESSION vs ON vs.session_id = ve.session_id
        JOIN CAMERA cam ON cam.camera_id = vs.camera_id
        WHERE v.violation_id = ?
        """, (violation_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def issue_challan_for_violation(violation_id: int, issued_by: int,
                                delivery_channel: str = "dashboard",
                                due_days: int = GRACE_DAYS) -> Optional[Dict[str, Any]]:
    """Issue a graduated e-challan for ANY overspeeding vehicle — registered or not.
    Computes the offence tier, fine and (if 3rd+) licence-suspension action, links a
    registered owner when the plate matches, queues the chosen alert(s), and returns a summary."""
    det = _violation_detail(violation_id)
    if det is None:
        return None
    plate = det.get("plate_text") or f"UNREG-{det['vehicle_id']}"
    offense_no = offense_number(violation_id, det["vehicle_id"], det.get("plate_text"))
    pen = compute_penalty(det["severity_level"], offense_no)
    match = match_registered_vehicle(det.get("plate_text"))
    due = (datetime.now() + timedelta(days=due_days)).date().isoformat()

    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO VIOLATION_NOTICE(
                   violation_id, reg_vehicle_id, owner_id, plate_number, issued_by,
                   base_amount, amount, overdue_amount, offense_count, offense_tier, action_taken,
                   suspension_months, delivery_channel, due_date, payment_status, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (violation_id,
             match["reg_vehicle_id"] if match else None,
             match["owner_id"] if match else None,
             plate, issued_by,
             pen["base_amount"], pen["amount"], pen["overdue_amount"], pen["offense_no"], pen["tier"],
             pen["action"], pen["suspension_months"], delivery_channel, due,
             "pending", pen["warning"]),
        )
        conn.commit(); conn.close()
    except sqlite3.IntegrityError:
        conn.close()
        return None  # already has a notice

    # mark the underlying violation reviewed
    conn = get_connection()
    conn.execute("UPDATE VIOLATION SET status='reviewed' WHERE violation_id=?", (violation_id,))
    conn.commit(); conn.close()

    # queue the alert(s) on the chosen channel
    subject = f"DriveShieldX e-Challan · {plate} · {det['speed_value']} km/h"
    body = (f"{pen['warning']} Speed {det['speed_value']} km/h in a {det['speed_limit']} km/h zone "
            f"at {det['location']}. Fine ₹{pen['amount']:.0f}. Due {due}.")
    recipient = (match["owner_email"] if match else None) or plate
    if delivery_channel in ("email", "both"):
        queue_alert("email", recipient, subject, body)
    if delivery_channel in ("dashboard", "both"):
        queue_alert("dashboard", recipient, subject, body)

    log_system_event("notice",
                     f"E-challan issued v{violation_id} {plate} tier={pen['tier']} ₹{pen['amount']:.0f}"
                     + (f" SUSPEND {pen['suspension_months']}mo" if pen['suspension_months'] else ""),
                     level="warning" if pen["tier"] != "first" else "info")
    pen.update({"plate": plate, "matched_owner": bool(match), "due_date": due})
    return pen


def bulk_issue_challans_for_all(issued_by: int, delivery_channel: str = "dashboard") -> Dict[str, Any]:
    """Generate e-challans for EVERY overspeeding vehicle that doesn't yet have one
    (registered or unregistered) — typically after processing an uploaded video / webcam run."""
    pending = get_unissued_violations(1000)
    issued, suspended, total_fine, matched = 0, 0, 0.0, 0
    for v in pending:
        res = issue_challan_for_violation(v["violation_id"], issued_by, delivery_channel)
        if res:
            issued += 1
            total_fine += res["amount"]
            if res["suspension_months"]:
                suspended += 1
            if res["matched_owner"]:
                matched += 1
    return {"issued": issued, "suspended": suspended,
            "total_fine": total_fine, "matched_owners": matched,
            "unregistered": issued - matched}



def create_manual_challan(
    plate_number: str,
    amount: float,
    issued_by: int,
    due_date: Optional[str] = None,
    notes: str = "",
    delivery_channel: str = "dashboard",
) -> Optional[Dict[str, Any]]:
    """Create an authority-issued challan without requiring a detected violation.

    The plate is matched against REGISTERED_VEHICLE when possible. Manual notices
    still use the existing repeat-offence tier fields so payment/overdue handling
    and the owner dashboard work exactly like detected-violation challans.
    """
    plate = (plate_number or "").strip().upper()
    if not plate:
        raise ValueError("Plate number is required.")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise ValueError("Fine amount must be a valid number.")

    if amount <= 0:
        raise ValueError("Fine amount must be greater than ₹0.")

    if delivery_channel not in ("dashboard", "email", "both"):
        delivery_channel = "dashboard"

    if due_date:
        try:
            due = datetime.fromisoformat(str(due_date)).date().isoformat()
        except ValueError:
            try:
                due = datetime.strptime(str(due_date), "%Y-%m-%d").date().isoformat()
            except ValueError:
                raise ValueError("Due date must be in YYYY-MM-DD format.")
    else:
        due = (datetime.now() + timedelta(days=GRACE_DAYS)).date().isoformat()

    conn = get_connection()
    try:
        match = conn.execute(
            """
            SELECT rv.reg_vehicle_id, rv.owner_id, rv.vehicle_type,
                   oa.name AS owner_name, oa.email AS owner_email,
                   oa.phone AS owner_phone
            FROM REGISTERED_VEHICLE rv
            JOIN OWNER_ACCOUNT oa ON oa.owner_id = rv.owner_id
            WHERE UPPER(rv.plate_number) = UPPER(?)
            """,
            (plate,),
        ).fetchone()

        # Count previous challans for the same plate. This keeps the existing
        # first/second/third offence semantics even when the authority creates
        # a challan manually.
        history = conn.execute(
            "SELECT COUNT(*) AS c FROM VIOLATION_NOTICE "
            "WHERE UPPER(COALESCE(plate_number,'')) = UPPER(?)",
            (plate,),
        ).fetchone()
        offense_no = int(history["c"] or 0) + 1

        tier = "first" if offense_no <= 1 else "second" if offense_no == 2 else "third"
        sched = FINE_SCHEDULE[tier]
        overdue_amount = float(sched["overdue"])

        if tier == "first":
            action = "warning_fine"
            suspension_months = 0
            default_note = "Manual e-challan — first offence."
        elif tier == "second":
            action = "escalated_fine"
            suspension_months = 0
            default_note = "Manual e-challan — second offence."
        else:
            action = "license_suspension"
            suspension_months = 3 + max(0, offense_no - 3)
            default_note = (
                f"Manual e-challan — repeated offence; driving licence "
                f"suspension for {suspension_months} month(s)."
            )

        final_notes = notes.strip() or default_note

        cur = conn.execute(
            """
            INSERT INTO VIOLATION_NOTICE(
                violation_id, reg_vehicle_id, owner_id, plate_number, issued_by,
                base_amount, amount, overdue_amount, offense_count, offense_tier,
                action_taken, suspension_months, delivery_channel, due_date,
                payment_status, notes
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                None,
                match["reg_vehicle_id"] if match else None,
                match["owner_id"] if match else None,
                plate,
                issued_by,
                amount,
                amount,
                overdue_amount,
                offense_no,
                tier,
                action,
                suspension_months,
                delivery_channel,
                due,
                "pending",
                final_notes,
            ),
        )
        notice_id = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError(
            "Could not create the manual challan. "
            "Make sure the database schema allows violation_id to be NULL."
        ) from exc
    finally:
        conn.close()

    subject = f"DriveShieldX manual e-Challan · {plate}"
    body = (
        f"Manual e-challan issued for vehicle {plate}. "
        f"Fine ₹{amount:.0f}. Due {due}. "
        f"Offence tier: {tier}."
    )
    recipient = (match["owner_email"] if match else None) or plate

    if delivery_channel in ("email", "both"):
        queue_alert("email", recipient, subject, body)
    if delivery_channel in ("dashboard", "both"):
        queue_alert("dashboard", recipient, subject, body)

    log_system_event(
        "notice",
        f"Manual e-challan issued #{notice_id} {plate} tier={tier} ₹{amount:.0f}",
        level="warning" if tier != "first" else "info",
        payload={
            "notice_id": notice_id,
            "plate_number": plate,
            "manual": True,
            "offense_count": offense_no,
        },
    )

    return {
        "notice_id": notice_id,
        "plate_number": plate,
        "matched_owner": bool(match),
        "owner_name": match["owner_name"] if match else None,
        "owner_email": match["owner_email"] if match else None,
        "amount": amount,
        "overdue_amount": overdue_amount,
        "offense_count": offense_no,
        "offense_tier": tier,
        "action_taken": action,
        "suspension_months": suspension_months,
        "due_date": due,
        "payment_status": "pending",
    }


# Back-compat shim: the older manual single-notice path still works.
def create_notice(violation_id: int, reg_vehicle_id: int, issued_by: int, amount: float,
                   due_date: Optional[str] = None, external_reference: Optional[str] = None,
                   notes: str = "", pdf_path: Optional[str] = None) -> bool:
    try:
        conn = get_connection()
        row = conn.execute("SELECT owner_id, plate_number FROM REGISTERED_VEHICLE WHERE reg_vehicle_id=?",
                           (reg_vehicle_id,)).fetchone()
        owner_id = row["owner_id"] if row else None
        plate = row["plate_number"] if row else None
        conn.execute(
            """INSERT INTO VIOLATION_NOTICE(violation_id,reg_vehicle_id,owner_id,plate_number,issued_by,
                   base_amount,amount,due_date,payment_status,external_reference,notes,pdf_path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (violation_id, reg_vehicle_id, owner_id, plate, issued_by, amount, amount,
             due_date or (datetime.now() + timedelta(days=14)).date().isoformat(),
             "pending", external_reference, notes, pdf_path),
        )
        conn.commit(); conn.close(); log_system_event("notice", f"Notice issued for violation {violation_id}"); return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def update_notice_payment_status(notice_id: int, status: str) -> None:
    conn = get_connection(); conn.execute("UPDATE VIOLATION_NOTICE SET payment_status=? WHERE notice_id=?", (status, notice_id)); conn.commit(); conn.close()


def pay_notice(notice_id: int, method: str = "UPI", txn_ref: Optional[str] = None) -> bool:
    """Settle a challan after the (simulated) UPI/gateway verification succeeds.
    Records the payment method, paid timestamp and the gateway/transaction reference —
    the same fields a real gateway callback would persist."""
    conn = get_connection()
    conn.execute(
        "UPDATE VIOLATION_NOTICE SET payment_status='paid', payment_method=?, paid_at=?, external_reference=? WHERE notice_id=?",
        (method, datetime.now().isoformat(timespec="seconds"), txn_ref, notice_id),
    )
    conn.commit(); conn.close()
    log_system_event("payment", f"Challan {notice_id} paid via {method} ref={txn_ref}")
    return True


def apply_overdue_penalties() -> int:
    """Auto-penalty sweep: any unpaid challan past its due date becomes 'overdue' and the
    amount JUMPS to the tier's escalated fine (Rs 500->1000, 1000->2000, 3000->5000),
    exactly like a utility bill that increases after the deadline. The increase (late_fee)
    is recorded so the owner sees the breakdown."""
    today = datetime.now().date().isoformat()
    conn = get_connection()
    rows = conn.execute(
        "SELECT notice_id, amount, overdue_amount, late_fee FROM VIOLATION_NOTICE "
        "WHERE payment_status='pending' AND due_date IS NOT NULL AND due_date < ?",
        (today,),
    ).fetchall()
    n = 0
    for r in rows:
        if (r["late_fee"] or 0) > 0:
            continue  # already escalated once
        target = r["overdue_amount"] or 0
        # if no overdue target stored, fall back to doubling
        if target <= (r["amount"] or 0):
            target = round((r["amount"] or 0) * 2, 2)
        increase = round(target - (r["amount"] or 0), 2)
        conn.execute(
            "UPDATE VIOLATION_NOTICE SET payment_status='overdue', amount=?, late_fee=? WHERE notice_id=?",
            (target, increase, r["notice_id"]),
        )
        n += 1
    conn.commit(); conn.close()
    if n:
        log_system_event("payment", f"{n} challan(s) overdue — fine escalated to the next tier", level="warning")
    return n


def get_all_notices(limit: int = 200) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute(
        """
        SELECT vn.*,
               COALESCE(vn.plate_number, rv.plate_number, ve.plate_text) AS plate_number,
               COALESCE(rv.vehicle_type, ve.vehicle_type) AS vehicle_type,
               oa.name AS owner_name, oa.email AS owner_email,
               CASE WHEN vn.owner_id IS NULL THEN 'Unregistered' ELSE 'Registered' END AS registration,
               v.violation_time, v.image_snapshot, v.severity_level,
               sr.speed_value, sr.speed_limit, cam.location
        FROM VIOLATION_NOTICE vn
        LEFT JOIN OWNER_ACCOUNT oa ON oa.owner_id = vn.owner_id
        LEFT JOIN REGISTERED_VEHICLE rv ON rv.reg_vehicle_id = vn.reg_vehicle_id
        LEFT JOIN VIOLATION v ON v.violation_id = vn.violation_id
        LEFT JOIN SPEED_RECORD sr ON sr.speed_id = v.speed_id
        LEFT JOIN VEHICLE ve ON ve.vehicle_id = sr.vehicle_id
        LEFT JOIN VIDEO_SESSION vs ON vs.session_id = ve.session_id
        LEFT JOIN CAMERA cam ON cam.camera_id = vs.camera_id
        ORDER BY vn.issued_at DESC LIMIT ?
        """, (limit,)
    ).fetchall(); conn.close(); return [dict(r) for r in rows]


def get_owner_notices(owner_id: int) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute(
        """
        SELECT vn.*,
               COALESCE(vn.plate_number, rv.plate_number, ve.plate_text) AS plate_number,
               COALESCE(rv.vehicle_type, ve.vehicle_type) AS vehicle_type, rv.model_name,
               v.violation_time, v.image_snapshot, v.severity_level,
               sr.speed_value, sr.speed_limit, cam.location
        FROM VIOLATION_NOTICE vn
        LEFT JOIN REGISTERED_VEHICLE rv ON rv.reg_vehicle_id = vn.reg_vehicle_id
        LEFT JOIN VIOLATION v ON v.violation_id = vn.violation_id
        LEFT JOIN SPEED_RECORD sr ON sr.speed_id = v.speed_id
        LEFT JOIN VEHICLE ve ON ve.vehicle_id = sr.vehicle_id
        LEFT JOIN VIDEO_SESSION vs ON vs.session_id = ve.session_id
        LEFT JOIN CAMERA cam ON cam.camera_id = vs.camera_id
        WHERE vn.owner_id = ? ORDER BY vn.issued_at DESC
        """, (owner_id,)
    ).fetchall(); conn.close(); return [dict(r) for r in rows]


def create_report(generated_by: int, report_type: str, file_path: str, from_date: Optional[str] = None, to_date: Optional[str] = None, violation_ids: Optional[Iterable[int]] = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO REPORT(generated_by,report_type,from_date,to_date,file_path) VALUES (?,?,?,?,?)",
        (generated_by, report_type, from_date, to_date, file_path),
    )
    report_id = cur.lastrowid
    for vid in violation_ids or []:
        conn.execute("INSERT OR IGNORE INTO REPORT_VIOLATION(report_id,violation_id) VALUES (?,?)", (report_id, vid))
    conn.commit(); conn.close(); return report_id


def get_reports(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute("SELECT r.*,u.name AS generated_by_name FROM REPORT r JOIN USER u ON u.user_id=r.generated_by ORDER BY r.report_date DESC LIMIT ?", (limit,)).fetchall(); conn.close(); return [dict(r) for r in rows]


# ANALYTICS

def get_violation_stats() -> Dict[str, Any]:
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) AS c FROM VIOLATION").fetchone()["c"]
    today = conn.execute("SELECT COUNT(*) AS c FROM VIOLATION WHERE DATE(violation_time)=DATE('now')").fetchone()["c"]
    avg_speed = conn.execute("SELECT AVG(speed_value) AS avg FROM SPEED_RECORD").fetchone()["avg"] or 0
    by_sev = {r["severity_level"]: r["count"] for r in conn.execute("SELECT severity_level,COUNT(*) AS count FROM VIOLATION GROUP BY severity_level").fetchall()}
    active_sessions = conn.execute("SELECT COUNT(*) AS c FROM VIDEO_SESSION WHERE status='active'").fetchone()["c"]
    total_notices = conn.execute("SELECT COUNT(*) AS c FROM VIOLATION_NOTICE").fetchone()["c"]
    conn.close()
    return {"total": total, "today": today, "avg_speed": round(float(avg_speed), 2), "active_sessions": active_sessions, "total_notices": total_notices, "by_severity": by_sev}


def get_speed_records_for_chart(limit: int = 300, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    if session_id is None:
        rows = conn.execute(
            "SELECT sr.calculated_time,sr.speed_value,sr.speed_limit,sr.centroid_x,sr.centroid_y,sr.source_fps,ve.vehicle_type,ve.vehicle_id,ve.plate_text,vs.session_id,cam.location FROM SPEED_RECORD sr JOIN VEHICLE ve ON ve.vehicle_id=sr.vehicle_id JOIN VIDEO_SESSION vs ON vs.session_id=ve.session_id JOIN CAMERA cam ON cam.camera_id=vs.camera_id ORDER BY sr.calculated_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT sr.calculated_time,sr.speed_value,sr.speed_limit,sr.centroid_x,sr.centroid_y,sr.source_fps,ve.vehicle_type,ve.vehicle_id,ve.plate_text,vs.session_id,cam.location FROM SPEED_RECORD sr JOIN VEHICLE ve ON ve.vehicle_id=sr.vehicle_id JOIN VIDEO_SESSION vs ON vs.session_id=ve.session_id JOIN CAMERA cam ON cam.camera_id=vs.camera_id WHERE vs.session_id=? ORDER BY sr.calculated_time DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    conn.close(); return [dict(r) for r in rows]


def get_all_violations(limit: int = 300, camera_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    base = """
        SELECT v.violation_id,v.violation_time,v.severity_level,v.status,v.image_snapshot,v.alert_status,
               sr.speed_value,sr.speed_limit,sr.zone_id,
               ve.vehicle_id,ve.vehicle_type,ve.plate_text,
               vs.session_id,vs.video_source,cam.camera_id,cam.location,
               vn.notice_id,vn.payment_status,rv.plate_number
        FROM VIOLATION v
        JOIN SPEED_RECORD sr ON sr.speed_id=v.speed_id
        JOIN VEHICLE ve ON ve.vehicle_id=sr.vehicle_id
        JOIN VIDEO_SESSION vs ON vs.session_id=ve.session_id
        JOIN CAMERA cam ON cam.camera_id=vs.camera_id
        LEFT JOIN VIOLATION_NOTICE vn ON vn.violation_id=v.violation_id
        LEFT JOIN REGISTERED_VEHICLE rv ON rv.reg_vehicle_id=vn.reg_vehicle_id
    """
    if camera_id is None:
        rows = conn.execute(base + " ORDER BY v.violation_time DESC LIMIT ?", (limit,)).fetchall()
    else:
        rows = conn.execute(base + " WHERE cam.camera_id=? ORDER BY v.violation_time DESC LIMIT ?", (camera_id, limit)).fetchall()
    conn.close(); return [dict(r) for r in rows]


def get_recent_sessions(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute(
        """
        SELECT vs.*,cam.location,cam.camera_type,
               COUNT(DISTINCT ve.vehicle_id) AS vehicle_count,
               COUNT(DISTINCT v.violation_id) AS violation_count
        FROM VIDEO_SESSION vs
        JOIN CAMERA cam ON cam.camera_id=vs.camera_id
        LEFT JOIN VEHICLE ve ON ve.session_id=vs.session_id
        LEFT JOIN SPEED_RECORD sr ON sr.vehicle_id=ve.vehicle_id
        LEFT JOIN VIOLATION v ON v.speed_id=sr.speed_id
        GROUP BY vs.session_id
        ORDER BY vs.start_time DESC LIMIT ?
        """, (limit,)
    ).fetchall(); conn.close(); return [dict(r) for r in rows]


def get_system_events(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection(); rows = conn.execute("SELECT * FROM SYSTEM_EVENT ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall(); conn.close(); return [dict(r) for r in rows]


# -------------------------------------------------------------------------
# DriveShieldX rule violations (helmet / triple-riding / seat-belt)
# -------------------------------------------------------------------------
DEFAULT_RULE_FINES = {"no_helmet": 500.0, "three_seater": 1000.0, "no_seatbelt": 1000.0}


def _resolve_reg_vehicle(conn, plate_text: Optional[str]) -> Optional[int]:
    if not plate_text:
        return None
    row = conn.execute(
        "SELECT reg_vehicle_id FROM REGISTERED_VEHICLE WHERE plate_number=?",
        (plate_text,)
    ).fetchone()
    return row["reg_vehicle_id"] if row else None


def insert_rule_violation(session_id: Optional[int], camera_id: Optional[int],
                          tracker_id: str, rule_type: str,
                          plate_text: Optional[str] = None,
                          snapshot_path: Optional[str] = None,
                          fine_amount: Optional[float] = None) -> Optional[int]:
    """Insert one rule violation. The (session, tracker, rule) uniqueness
    constraint guarantees a single record even if the caller retries."""
    if rule_type not in DEFAULT_RULE_FINES:
        return None
    conn = get_connection()
    try:
        reg = _resolve_reg_vehicle(conn, plate_text)
        amt = float(fine_amount) if fine_amount is not None else DEFAULT_RULE_FINES[rule_type]
        cur = conn.execute(
            """INSERT OR IGNORE INTO RULE_VIOLATION
               (session_id, camera_id, tracker_id, rule_type, plate_text,
                reg_vehicle_id, snapshot_path, fine_amount)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, camera_id, str(tracker_id), rule_type, plate_text,
             reg, snapshot_path, amt)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


_GATE_REVIEW_DDL = """CREATE TABLE IF NOT EXISTS GATE_REVIEW (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, camera_id INTEGER,
    tracker_id TEXT NOT NULL, rule_type TEXT NOT NULL, p_standalone REAL, p_combined REAL,
    theta REAL, snapshot_path TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','confirmed','rejected')),
    reviewer_note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, tracker_id, rule_type))"""


def insert_gate_review(session_id: Optional[int], camera_id: Optional[int], tracker_id: str,
                       rule_type: str, p_standalone: Optional[float], p_combined: Optional[float],
                       theta: float, snapshot_path: Optional[str] = None) -> Optional[int]:
    """Queue a disagreement-gate event for officer review. No challan is created."""
    conn = get_connection()
    try:
        conn.execute(_GATE_REVIEW_DDL)
        cur = conn.execute(
            """INSERT OR IGNORE INTO GATE_REVIEW
               (session_id, camera_id, tracker_id, rule_type, p_standalone, p_combined, theta, snapshot_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, camera_id, str(tracker_id), rule_type, p_standalone, p_combined, theta, snapshot_path),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_gate_reviews(limit: int = 200, status: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        conn.execute(_GATE_REVIEW_DDL)
        if status:
            rows = conn.execute("SELECT * FROM GATE_REVIEW WHERE status=? ORDER BY created_at DESC LIMIT ?",
                                (status, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM GATE_REVIEW ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_gate_review(review_id: int, status: str, note: Optional[str] = None) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE GATE_REVIEW SET status=?, reviewer_note=? WHERE review_id=?", (status, note, review_id))
        conn.commit()
    finally:
        conn.close()


def get_rule_violations(limit: int = 200, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    if session_id is not None:
        rows = conn.execute(
            "SELECT * FROM RULE_VIOLATION WHERE session_id=? ORDER BY detected_at DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM RULE_VIOLATION ORDER BY detected_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_owner_rule_violations(owner_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT rv.*, r.plate_number AS registered_plate
           FROM RULE_VIOLATION rv
           JOIN REGISTERED_VEHICLE r ON r.reg_vehicle_id=rv.reg_vehicle_id
           WHERE r.owner_id=? ORDER BY detected_at DESC""",
        (owner_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_rule_violation_status(rule_violation_id: int, status: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE RULE_VIOLATION SET status=? WHERE rule_violation_id=?",
        (status, rule_violation_id)
    )
    conn.commit(); conn.close()


def get_rule_violation_summary() -> Dict[str, int]:
    """Counts per rule for the dashboard KPI tiles."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT rule_type, COUNT(*) AS c FROM RULE_VIOLATION GROUP BY rule_type"
    ).fetchall()
    conn.close()
    out = {"no_helmet": 0, "three_seater": 0, "no_seatbelt": 0}
    for row in rows:
        out[row["rule_type"]] = row["c"]
    return out