"""Metrics computable from the runtime database alone (read-only).

- implausible-speed proportion (paper Sec. 5.4 "proportion of physically
  implausible estimates") at 120 / 200 / 400 km/h
- severity distribution of graded over-speed events
- plate-string provenance (OCR vs demo-synthesised, via ocr_confidence)
- gate outcomes from GATE_REVIEW / SYSTEM_EVENT (after the gate is deployed)

Usage:  python -m evaluation.db_metrics [--db database/overspeed.db] [--since "2026-10-01"]
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from evaluation.common import REPO_ROOT, write_json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "database" / "overspeed.db"))
    ap.add_argument("--since", default=None, help="only rows at/after this timestamp")
    ap.add_argument("--out", default="evaluation/results/db_metrics.json")
    a = ap.parse_args()
    # Read a temporary copy so the live database (WAL mode) is never touched.
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "snapshot.db"
    shutil.copy(a.db, tmp)
    for ext in ("-wal", "-shm"):
        if Path(a.db + ext).exists():
            shutil.copy(a.db + ext, str(tmp) + ext)
    con = sqlite3.connect(str(tmp))
    q = lambda sql, *p: con.execute(sql, p).fetchall()
    since = a.since or "0000"
    res = {"db": a.db, "since": a.since}

    n, mean, mx = q("SELECT COUNT(*), AVG(speed_value), MAX(speed_value) FROM SPEED_RECORD WHERE calculated_time>=?", since)[0]
    res["speed"] = {"estimates": n, "mean_kmh": mean, "max_kmh": mx}
    for thr in (60, 120, 200, 400):
        k = q("SELECT COUNT(*) FROM SPEED_RECORD WHERE speed_value>? AND calculated_time>=?", thr, since)[0][0]
        res["speed"][f"n_gt_{thr}"] = k
        res["speed"][f"frac_gt_{thr}"] = k / n if n else None
    fps = q("SELECT source_fps, COUNT(*) FROM SPEED_RECORD WHERE calculated_time>=? GROUP BY source_fps", since)
    res["speed"]["source_fps_values"] = {str(f): c for f, c in fps}
    res["overspeed_severity"] = dict(q("SELECT severity_level, COUNT(*) FROM VIOLATION WHERE violation_time>=? GROUP BY 1", since))
    res["rule_violations"] = dict(q("SELECT rule_type, COUNT(*) FROM RULE_VIOLATION WHERE detected_at>=? GROUP BY 1", since))
    tot, with_plate, ocr = q("SELECT COUNT(*), SUM(plate_text IS NOT NULL AND plate_text!=''), "
                             "SUM(plate_text IS NOT NULL AND plate_text!='' AND ocr_confidence>0) FROM VEHICLE "
                             "WHERE detection_time>=?", since)[0]
    res["plates"] = {"vehicle_records": tot, "with_plate_string": with_plate, "ocr_origin(conf>0)": ocr,
                     "synthesised_or_manual(conf<=0)": (with_plate or 0) - (ocr or 0)}
    tables = {r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'")}
    if "GATE_REVIEW" in tables:
        res["gate_review_queue"] = dict(q("SELECT status, COUNT(*) FROM GATE_REVIEW WHERE created_at>=? GROUP BY 1", since))
        issued = q("SELECT COUNT(*) FROM RULE_VIOLATION WHERE rule_type='no_helmet' AND detected_at>=?", since)[0][0]
        withheld = sum(res["gate_review_queue"].values())
        res["gate_withheld_rate_no_helmet"] = withheld / (withheld + issued) if (withheld + issued) else None
    else:
        res["gate_review_queue"] = "table absent (gate not yet run on this DB)"
    write_json(a.out, res)
    import json
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
