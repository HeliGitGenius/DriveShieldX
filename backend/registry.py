"""Vehicle registry lookup (the "vehicle registry" the paper's ANPR stage matches
plates against, Sec. 4.5).

India has no free public VAHAN API: direct VAHAN/Parivahan access is limited to
authorised government systems, and developers normally use a paid RC-verification
API from a KYC aggregator. This module therefore has two providers behind one
interface:

  LocalRegistryProvider  the project's own REGISTERED_VEHICLE / OWNER_ACCOUNT
                         tables (always on, offline).
  HttpRCProvider         any RC-verification REST API, configured ONLY via .env:
      DSX_RC_API_URL       endpoint; may contain {plate} (e.g. for GET APIs)
      DSX_RC_API_METHOD    POST (default) or GET
      DSX_RC_API_BODY_TEMPLATE  optional JSON body with {plate} / {uuid} placeholders, for APIs
                           whose body is nested, e.g. {"task_id":"{uuid}","data":{"rc_number":"{plate}"}}
      DSX_RC_API_HEADERS   optional JSON of extra headers (APIs that need two keys, e.g. account id)
      DSX_RC_API_KEY       secret
      DSX_RC_API_AUTH      "bearer" (Authorization: Bearer <key>) or "header:<Name>"
      DSX_RC_API_BODY_KEY  JSON field carrying the plate (default "id_number")
      DSX_RC_API_ROOT      dotted path to the record inside the response (default "data")
      DSX_RC_API_MAP       optional JSON {our_field: "dotted.path"} to rename fields
      DSX_RC_CACHE_HOURS   cache lifetime (default 24)

The HTTP provider has NOT been exercised against a live key in this repository;
check the field names of the provider you subscribe to and set DSX_RC_API_MAP.
Personal data is minimised: owner names are masked before they reach the UI
(data-minimisation, in line with India's Digital Personal Data Protection Act, 2023).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from backend.env_loader import load_env
from database import db_manager, safety_db
from detection.plate_format import clean, is_valid

load_env()

FIELDS = ["plate", "owner_name", "vehicle_class", "maker_model", "fuel_type", "registration_date",
          "insurance_upto", "pucc_upto", "fitness_upto", "rc_status", "blacklist_status", "rto", "source"]

DEFAULT_MAP = {
    "owner_name": "owner_name", "vehicle_class": "vehicle_category", "maker_model": "maker_model",
    "fuel_type": "fuel_type", "registration_date": "registration_date", "insurance_upto": "insurance_upto",
    "pucc_upto": "pucc_upto", "fitness_upto": "fit_up_to", "rc_status": "rc_status",
    "blacklist_status": "blacklist_status", "rto": "registered_at",
}


def mask_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    return " ".join(p[0] + "*" * (len(p) - 1) if len(p) > 1 else p for p in str(name).split())


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


class LocalRegistryProvider:
    name = "local"

    def lookup(self, plate: str) -> Optional[Dict[str, Any]]:
        c = db_manager.get_connection()
        try:
            r = c.execute("""SELECT v.plate_number, v.vehicle_type, v.model_name, o.name AS owner_name
                             FROM REGISTERED_VEHICLE v LEFT JOIN OWNER_ACCOUNT o ON o.owner_id=v.owner_id
                             WHERE REPLACE(UPPER(v.plate_number),' ','')=?""", (plate,)).fetchone()
        finally:
            c.close()
        if not r:
            return None
        return {"plate": plate, "owner_name": r["owner_name"], "vehicle_class": r["vehicle_type"],
                "maker_model": r["model_name"], "source": self.name}


class HttpRCProvider:
    name = "http"

    def __init__(self) -> None:
        self.url = os.environ.get("DSX_RC_API_URL", "").strip()
        self.key = os.environ.get("DSX_RC_API_KEY", "").strip()
        self.auth = os.environ.get("DSX_RC_API_AUTH", "bearer").strip()
        self.body_key = os.environ.get("DSX_RC_API_BODY_KEY", "id_number")
        self.root = os.environ.get("DSX_RC_API_ROOT", "data")
        m = os.environ.get("DSX_RC_API_MAP")
        self.map = {**DEFAULT_MAP, **(json.loads(m) if m else {})}
        self.timeout = float(os.environ.get("DSX_RC_API_TIMEOUT", "8"))
        self.method = os.environ.get("DSX_RC_API_METHOD", "POST").strip().upper()
        self.body_template = os.environ.get("DSX_RC_API_BODY_TEMPLATE", "").strip()
        h = os.environ.get("DSX_RC_API_HEADERS", "").strip()
        self.extra_headers = json.loads(h) if h else {}

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.auth.lower() == "bearer":
            h["Authorization"] = f"Bearer {self.key}"
        elif self.auth.lower().startswith("header:"):
            h[self.auth.split(":", 1)[1]] = self.key
        h.update({str(k): str(v) for k, v in self.extra_headers.items()})
        return h

    def _body(self, plate: str) -> Any:
        if not self.body_template:
            return {self.body_key: plate}
        import uuid

        def fill(v: Any) -> Any:
            if isinstance(v, str):
                return v.replace("{plate}", plate).replace("{uuid}", str(uuid.uuid4()))
            if isinstance(v, dict):
                return {k: fill(x) for k, x in v.items()}
            if isinstance(v, list):
                return [fill(x) for x in v]
            return v
        return fill(json.loads(self.body_template))

    def raw(self, plate: str) -> Any:
        """The provider's untouched JSON (used once to set DSX_RC_API_MAP)."""
        import requests

        url = self.url.replace("{plate}", plate)
        if self.method == "GET":
            r = requests.get(url, headers=self._headers(), timeout=self.timeout)
        else:
            r = requests.post(url, json=self._body(plate), headers=self._headers(), timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def lookup(self, plate: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        body = self.raw(plate)
        if body is None:
            return None
        rec = _dig(body, self.root) if self.root else body
        if not isinstance(rec, dict) or not rec:
            return None
        out = {"plate": plate, "source": self.name}
        for ours, theirs in self.map.items():
            out[ours] = _dig(rec, theirs)
        return out


def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(v)[:19], fmt).date()
        except ValueError:
            continue
    return None


def compliance_flags(rec: Optional[Dict[str, Any]], today: Optional[date] = None) -> List[str]:
    """Document validity flags an enforcement officer cares about (expired
    insurance / PUC / fitness, blacklisted RC). Informational only -- no challan
    is generated from these automatically."""
    if not rec:
        return []
    today = today or date.today()
    flags = []
    for key, flag in (("insurance_upto", "insurance_expired"), ("pucc_upto", "puc_expired"),
                      ("fitness_upto", "fitness_expired")):
        d = _parse_date(rec.get(key))
        if d and d < today:
            flags.append(flag)
    bl = str(rec.get("blacklist_status") or "").strip().lower()
    if bl and bl not in ("", "na", "n/a", "none", "not blacklisted", "no", "false", "0"):
        flags.append("blacklisted")
    return flags


class Registry:
    def __init__(self) -> None:
        self.local = LocalRegistryProvider()
        self.http = HttpRCProvider()
        self.cache_hours = float(os.environ.get("DSX_RC_CACHE_HOURS", "24"))

    def lookup(self, plate_text: Optional[str], use_remote: bool = True, masked: bool = True) -> Dict[str, Any]:
        """Always returns a dict with 'plate', 'valid_format', 'found', 'record', 'flags', 'provider'."""
        plate = clean(plate_text)
        res: Dict[str, Any] = {"plate": plate, "valid_format": is_valid(plate), "found": False, "record": None,
                               "flags": [], "provider": None, "error": None}
        if not plate:
            return res
        rec = self.local.lookup(plate)
        provider = "local" if rec else None
        if rec is None and use_remote and self.http.enabled and res["valid_format"]:
            cached = safety_db.cache_get(plate, self.cache_hours)
            if cached:
                rec = json.loads(cached["payload_json"]) if cached["found"] else None
                provider = f"{cached['provider']} (cache)"
            else:
                try:
                    rec = self.http.lookup(plate)
                    safety_db.cache_put(plate, self.http.name, rec is not None, rec)
                    provider = self.http.name
                except Exception as exc:  # network / quota / auth problems never break enforcement
                    res["error"] = f"{type(exc).__name__}: {exc}"
        if rec:
            res["found"] = True
            res["flags"] = compliance_flags(rec)
            if masked:
                rec = {**rec, "owner_name": mask_name(rec.get("owner_name"))}
            res["record"] = rec
            res["provider"] = provider
        return res


REGISTRY = Registry()


def _keys(obj: Any, prefix: str = "") -> List[str]:
    """Dotted field names of a JSON object, values left out (safe to show)."""
    out: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _keys(v, f"{prefix}{k}.") if isinstance(v, dict) else [f"{prefix}{k}"]
    return out


if __name__ == "__main__":
    # python -m backend.registry MH12AB1234          -> masked lookup + compliance flags
    # python -m backend.registry MH12AB1234 --fields -> the provider's field NAMES only (to set DSX_RC_API_MAP)
    import sys

    from backend.env_loader import load_env

    load_env()
    if len(sys.argv) < 2:
        sys.exit("usage: python -m backend.registry <PLATE> [--fields]")
    reg = Registry()
    plate = clean(sys.argv[1])
    if "--fields" in sys.argv:
        if not reg.http.enabled:
            sys.exit("DSX_RC_API_URL / DSX_RC_API_KEY not set")
        print("\n".join(_keys(reg.http.raw(plate))))
    else:
        print(json.dumps(reg.lookup(plate, masked=True), indent=2, default=str))
