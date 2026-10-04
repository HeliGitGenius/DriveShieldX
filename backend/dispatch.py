"""Emergency dispatch for confirmed accidents (paper Fig. 1: "Police + Hospital
Dispatch (Alert Engine + GPS)").

- Camera position comes from CAMERA_GEO (set on the Road Safety page).
- Hospitals and police stations come from EMERGENCY_FACILITY, filled either by
  hand or from OpenStreetMap through the public Overpass API (fetch_osm_facilities).
- Nearest facility = great-circle (haversine) distance.

Dispatch modes (env DSX_DISPATCH_MODE):
  control_room (default)  alert the traffic control room (phone/email on the camera,
                          or DSX_CONTROL_ROOM_PHONE / DSX_CONTROL_ROOM_EMAIL) and the
                          dashboard, listing the nearest hospital and police station
                          with distance and a map link. A human confirms and calls 112.
  direct                  additionally message the facilities' own phone/email.
The SMS/e-mail channels are the project's existing ones (backend/alerts.py).
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

from backend.alerts import send_email_alert, send_sms_alert
from backend.logger import get_logger
from database import safety_db
from database.db_manager import queue_alert

logger = get_logger("dispatch")
OVERPASS_URL = os.environ.get("DSX_OVERPASS_URL", "https://overpass-api.de/api/interpreter")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest(lat: float, lon: float, kind: str, k: int = 1, max_km: float = 25.0,
            facilities: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    facs = facilities if facilities is not None else safety_db.list_facilities(kind)
    ranked = sorted(({**f, "distance_km": round(haversine_km(lat, lon, f["latitude"], f["longitude"]), 2)}
                     for f in facs if f["kind"] == kind), key=lambda f: f["distance_km"])
    return [f for f in ranked if f["distance_km"] <= max_km][:k]


def fetch_osm_facilities(lat: float, lon: float, radius_m: int = 5000, timeout: float = 30.0) -> Dict[str, int]:
    """Pull hospitals and police stations around a camera from OpenStreetMap."""
    import requests

    q = f"""[out:json][timeout:25];
(
  node["amenity"="hospital"](around:{radius_m},{lat},{lon});
  way["amenity"="hospital"](around:{radius_m},{lat},{lon});
  node["amenity"="police"](around:{radius_m},{lat},{lon});
  way["amenity"="police"](around:{radius_m},{lat},{lon});
);
out center tags;"""
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=timeout,
                      headers={"User-Agent": "DriveShieldX/1.0 (academic project)"})
    r.raise_for_status()
    counts = {"hospital": 0, "police": 0}
    for el in r.json().get("elements", []):
        tags = el.get("tags", {})
        kind = tags.get("amenity")
        if kind not in counts:
            continue
        la = el.get("lat") or (el.get("center") or {}).get("lat")
        lo = el.get("lon") or (el.get("center") or {}).get("lon")
        if la is None or lo is None:
            continue
        name = tags.get("name") or tags.get("name:en") or f"Unnamed {kind}"
        phone = tags.get("phone") or tags.get("contact:phone") or ""
        email = tags.get("email") or tags.get("contact:email") or ""
        addr = ", ".join(v for k, v in tags.items() if k.startswith("addr:") and v)
        if kind == "hospital" and tags.get("emergency") == "yes":
            name += " (emergency)"
        safety_db.upsert_facility(kind, name, float(la), float(lo), phone, email, addr, "osm",
                                  f"{el.get('type')}/{el.get('id')}")
        counts[kind] += 1
    return counts


def _maps_link(lat: float, lon: float) -> str:
    return f"https://maps.google.com/?q={lat:.6f},{lon:.6f}"


def dispatch_accident(accident_id: int, camera_id: int, when: str, score: float,
                      cues: Dict[str, Any], track_ids: List[str]) -> Dict[str, Any]:
    mode = os.environ.get("DSX_DISPATCH_MODE", "control_room")
    geo = safety_db.get_camera_geo(camera_id)
    result: Dict[str, Any] = {"mode": mode, "camera_geo": bool(geo), "hospital": None, "police": None, "sent": []}
    lines = [f"DriveShieldX ACCIDENT ALERT #{accident_id}", f"Camera {camera_id} at {when}",
             f"Confidence score {score:.2f}; vehicles {', '.join(map(str, track_ids))}"]
    if geo:
        lat, lon = geo["latitude"], geo["longitude"]
        lines.append(f"Location: {geo.get('address') or ''} {_maps_link(lat, lon)}".strip())
        for kind in ("hospital", "police"):
            n = nearest(lat, lon, kind)
            if n:
                f = n[0]
                result[kind] = f
                lines.append(f"Nearest {kind}: {f['name']} ({f['distance_km']} km) {f.get('phone') or ''} "
                             f"{_maps_link(f['latitude'], f['longitude'])}")
            else:
                lines.append(f"Nearest {kind}: none on file -- add facilities on the Road Safety page")
    else:
        lines.append("Camera location not configured -- set latitude/longitude on the Road Safety page.")
    lines.append("Emergency number: 112")
    body = "\n".join(lines)
    sms = " | ".join(lines[:3] + [l for l in lines if l.startswith(("Location", "Nearest hospital", "Nearest police"))])

    phone = (geo or {}).get("control_room_phone") or os.environ.get("DSX_CONTROL_ROOM_PHONE")
    email = (geo or {}).get("control_room_email") or os.environ.get("DSX_CONTROL_ROOM_EMAIL")
    dash_id = queue_alert("dashboard", "traffic-control-room", f"Accident #{accident_id}", body)
    safety_db.insert_dispatch(accident_id, None, "control_room", None, "dashboard", "traffic-control-room", dash_id, "queued")
    result["sent"].append(("dashboard", "traffic-control-room"))
    if phone:
        aid = send_sms_alert(phone, sms)
        safety_db.insert_dispatch(accident_id, None, "control_room", None, "sms", phone, aid, "sent")
        result["sent"].append(("sms", phone))
    if email:
        aid = send_email_alert(email, f"DriveShieldX accident alert #{accident_id}", body)
        safety_db.insert_dispatch(accident_id, None, "control_room", None, "email", email, aid, "sent")
        result["sent"].append(("email", email))
    if mode == "direct":
        for kind in ("hospital", "police"):
            f = result.get(kind)
            if not f:
                continue
            if f.get("phone"):
                aid = send_sms_alert(f["phone"], sms)
                safety_db.insert_dispatch(accident_id, f["facility_id"], kind, f["distance_km"], "sms", f["phone"], aid, "sent")
                result["sent"].append(("sms", f["phone"]))
            if f.get("email"):
                aid = send_email_alert(f["email"], f"Accident alert #{accident_id}", body)
                safety_db.insert_dispatch(accident_id, f["facility_id"], kind, f["distance_km"], "email", f["email"], aid, "sent")
                result["sent"].append(("email", f["email"]))
    safety_db.update_accident_status(accident_id, "dispatched")
    logger.warning("Accident %s dispatched: %s", accident_id, result["sent"])
    result["message"] = body
    return result
