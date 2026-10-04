"""Persistence for the road-safety layer (fog, accidents, dispatch, registry cache,
camera geolocation). Kept in its own module so the existing db_manager is only
extended, never rewritten. Every table is created lazily with IF NOT EXISTS, so
old databases upgrade themselves on first use.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from database import db_manager

SAFETY_DDL = """
CREATE TABLE IF NOT EXISTS CAMERA_GEO (
    camera_id INTEGER PRIMARY KEY,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    address TEXT,
    control_room_phone TEXT,
    control_room_email TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS FOG_EVENT (
    fog_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER,
    session_id INTEGER,
    level TEXT NOT NULL CHECK(level IN ('clear','light_haze','moderate_fog','dense_fog','low_light')),
    visibility_index REAL,
    fog_score REAL,
    dark_channel REAL,
    contrast REAL,
    edge_density REAL,
    advisory_speed REAL,
    event_type TEXT NOT NULL CHECK(event_type IN ('advisory_on','advisory_off','reading')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fog_event_cam ON FOG_EVENT(camera_id, created_at);

CREATE TABLE IF NOT EXISTS ACCIDENT_EVENT (
    accident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER,
    session_id INTEGER,
    frame_no INTEGER,
    track_ids TEXT,
    cues TEXT,
    score REAL,
    snapshot_path TEXT,
    status TEXT DEFAULT 'confirmed' CHECK(status IN ('candidate','confirmed','dispatched','false_alarm','resolved')),
    reviewed_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_accident_cam ON ACCIDENT_EVENT(camera_id, created_at);

CREATE TABLE IF NOT EXISTS EMERGENCY_FACILITY (
    facility_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('hospital','police')),
    name TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    phone TEXT,
    email TEXT,
    address TEXT,
    source TEXT DEFAULT 'manual',
    external_ref TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, external_ref)
);

CREATE TABLE IF NOT EXISTS DISPATCH_LOG (
    dispatch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    accident_id INTEGER,
    facility_id INTEGER,
    kind TEXT,
    distance_km REAL,
    channel TEXT,
    recipient TEXT,
    alert_id INTEGER,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS FOG_BASELINE (
    camera_id INTEGER PRIMARY KEY,
    dark_channel REAL NOT NULL,
    contrast REAL NOT NULL,
    edge_density REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS REGISTRY_CACHE (
    plate TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    found INTEGER NOT NULL,
    payload_json TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_READY = set()


def conn() -> sqlite3.Connection:
    c = db_manager.get_connection()
    key = str(db_manager.DB_PATH)
    if key not in _READY:
        c.executescript(SAFETY_DDL)
        c.commit()
        _READY.add(key)
    return c


def _rows(cur) -> List[Dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- camera geo
def set_camera_geo(camera_id: int, lat: float, lon: float, address: str = "",
                   phone: str = "", email: str = "") -> None:
    c = conn()
    try:
        c.execute("""INSERT INTO CAMERA_GEO(camera_id, latitude, longitude, address, control_room_phone, control_room_email)
                     VALUES (?,?,?,?,?,?)
                     ON CONFLICT(camera_id) DO UPDATE SET latitude=excluded.latitude, longitude=excluded.longitude,
                     address=excluded.address, control_room_phone=excluded.control_room_phone,
                     control_room_email=excluded.control_room_email, updated_at=CURRENT_TIMESTAMP""",
                  (camera_id, lat, lon, address, phone, email))
        c.commit()
    finally:
        c.close()


def get_camera_geo(camera_id: int) -> Optional[Dict[str, Any]]:
    c = conn()
    try:
        r = c.execute("SELECT * FROM CAMERA_GEO WHERE camera_id=?", (camera_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def list_camera_geo() -> List[Dict[str, Any]]:
    c = conn()
    try:
        return _rows(c.execute("SELECT * FROM CAMERA_GEO"))
    finally:
        c.close()


# ---------------------------------------------------------------- fog
def insert_fog_event(camera_id, session_id, reading: Dict[str, Any], event_type: str,
                     advisory_speed: Optional[float] = None) -> int:
    c = conn()
    try:
        cur = c.execute("""INSERT INTO FOG_EVENT(camera_id, session_id, level, visibility_index, fog_score, dark_channel,
                           contrast, edge_density, advisory_speed, event_type) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (camera_id, session_id, reading["level"], reading["visibility_index"], reading["fog_score"],
                         reading["dark_channel"], reading["contrast"], reading["edge_density"], advisory_speed, event_type))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def list_fog_events(camera_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    c = conn()
    try:
        if camera_id is None:
            return _rows(c.execute("SELECT * FROM FOG_EVENT ORDER BY created_at DESC LIMIT ?", (limit,)))
        return _rows(c.execute("SELECT * FROM FOG_EVENT WHERE camera_id=? ORDER BY created_at DESC LIMIT ?",
                               (camera_id, limit)))
    finally:
        c.close()


def get_fog_baseline(camera_id: int) -> Optional[Dict[str, float]]:
    c = conn()
    try:
        r = c.execute("SELECT dark_channel, contrast, edge_density FROM FOG_BASELINE WHERE camera_id=?",
                      (camera_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def set_fog_baseline(camera_id: int, base: Dict[str, float]) -> None:
    c = conn()
    try:
        c.execute("""INSERT INTO FOG_BASELINE(camera_id, dark_channel, contrast, edge_density) VALUES (?,?,?,?)
                     ON CONFLICT(camera_id) DO UPDATE SET dark_channel=excluded.dark_channel,
                     contrast=excluded.contrast, edge_density=excluded.edge_density, updated_at=CURRENT_TIMESTAMP""",
                  (camera_id, base["dark_channel"], base["contrast"], base["edge_density"]))
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------- accidents
def insert_accident(camera_id, session_id, frame_no, track_ids, cues: Dict[str, Any], score: float,
                    snapshot_path: Optional[str], status: str = "confirmed") -> int:
    c = conn()
    try:
        cur = c.execute("""INSERT INTO ACCIDENT_EVENT(camera_id, session_id, frame_no, track_ids, cues, score,
                           snapshot_path, status) VALUES (?,?,?,?,?,?,?,?)""",
                        (camera_id, session_id, frame_no, ",".join(map(str, track_ids)), json.dumps(cues), score,
                         snapshot_path, status))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def update_accident_status(accident_id: int, status: str, reviewed_by: Optional[str] = None) -> None:
    c = conn()
    try:
        c.execute("UPDATE ACCIDENT_EVENT SET status=?, reviewed_by=COALESCE(?, reviewed_by) WHERE accident_id=?",
                  (status, reviewed_by, accident_id))
        c.commit()
    finally:
        c.close()


def list_accidents(limit: int = 200) -> List[Dict[str, Any]]:
    c = conn()
    try:
        return _rows(c.execute("SELECT * FROM ACCIDENT_EVENT ORDER BY created_at DESC LIMIT ?", (limit,)))
    finally:
        c.close()


# ---------------------------------------------------------------- facilities / dispatch
def upsert_facility(kind: str, name: str, lat: float, lon: float, phone: str = "", email: str = "",
                    address: str = "", source: str = "manual", external_ref: Optional[str] = None) -> int:
    c = conn()
    try:
        if external_ref is None:
            external_ref = f"{kind}:{name}:{lat:.5f}:{lon:.5f}"
        cur = c.execute("""INSERT INTO EMERGENCY_FACILITY(kind,name,latitude,longitude,phone,email,address,source,external_ref)
                           VALUES (?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(source, external_ref) DO UPDATE SET name=excluded.name,
                           latitude=excluded.latitude, longitude=excluded.longitude,
                           phone=COALESCE(NULLIF(excluded.phone,''), EMERGENCY_FACILITY.phone),
                           email=COALESCE(NULLIF(excluded.email,''), EMERGENCY_FACILITY.email),
                           address=COALESCE(NULLIF(excluded.address,''), EMERGENCY_FACILITY.address),
                           updated_at=CURRENT_TIMESTAMP""",
                        (kind, name, lat, lon, phone, email, address, source, external_ref))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def list_facilities(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    c = conn()
    try:
        if kind:
            return _rows(c.execute("SELECT * FROM EMERGENCY_FACILITY WHERE kind=?", (kind,)))
        return _rows(c.execute("SELECT * FROM EMERGENCY_FACILITY"))
    finally:
        c.close()


def delete_facility(facility_id: int) -> None:
    c = conn()
    try:
        c.execute("DELETE FROM EMERGENCY_FACILITY WHERE facility_id=?", (facility_id,))
        c.commit()
    finally:
        c.close()


def insert_dispatch(accident_id, facility_id, kind, distance_km, channel, recipient, alert_id, status) -> None:
    c = conn()
    try:
        c.execute("""INSERT INTO DISPATCH_LOG(accident_id,facility_id,kind,distance_km,channel,recipient,alert_id,status)
                     VALUES (?,?,?,?,?,?,?,?)""",
                  (accident_id, facility_id, kind, distance_km, channel, recipient, alert_id, status))
        c.commit()
    finally:
        c.close()


def list_dispatches(accident_id: Optional[int] = None) -> List[Dict[str, Any]]:
    c = conn()
    try:
        if accident_id is None:
            return _rows(c.execute("SELECT * FROM DISPATCH_LOG ORDER BY created_at DESC LIMIT 500"))
        return _rows(c.execute("SELECT * FROM DISPATCH_LOG WHERE accident_id=?", (accident_id,)))
    finally:
        c.close()


# ---------------------------------------------------------------- registry cache
def cache_get(plate: str, max_age_hours: float) -> Optional[Dict[str, Any]]:
    c = conn()
    try:
        r = c.execute("""SELECT * FROM REGISTRY_CACHE WHERE plate=?
                         AND fetched_at >= datetime('now', ?)""", (plate, f"-{max_age_hours} hours")).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def cache_put(plate: str, provider: str, found: bool, payload: Optional[Dict[str, Any]]) -> None:
    c = conn()
    try:
        c.execute("""INSERT INTO REGISTRY_CACHE(plate, provider, found, payload_json) VALUES (?,?,?,?)
                     ON CONFLICT(plate) DO UPDATE SET provider=excluded.provider, found=excluded.found,
                     payload_json=excluded.payload_json, fetched_at=CURRENT_TIMESTAMP""",
                  (plate, provider, int(found), json.dumps(payload) if payload is not None else None))
        c.commit()
    finally:
        c.close()
