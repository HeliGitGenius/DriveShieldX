"""Violation-level accuracy: precision / recall / F1 for no-helmet, triple-riding
and no-seat-belt, for the LEGACY decision rules and the CURRENT ones, on the same
detections (paper Sec. 6.6 "violation F1"; the per-rule accuracy the paper could
not report because no violation-level ground truth existed).

Legacy  : helmet = OR of both models on the raw bike box; triple = persons inside a
          fixed 40/120-px padded bike box >= 3; seat belt = no_seatbelt >= 0.5 and
          >= seatbelt, no CLAHE.
Current : helmet = disagreement gate on the rider crop, head regions only (a
          'review' verdict is NOT an issued violation); triple = unique, scale-aware
          rider assignment in tandem groups (3-4 riders, or 2 + model corroboration);
          seat belt = +0.12 margin and CLAHE on dark/glare crops.
Both are single-frame decisions (temporal confirmation is applied later in the
app and is the same for both), so this isolates the decision rule.

Step 1  make-sheet : python -m evaluation.violation_eval make-sheet --source video1.mp4 ... --out evaluation/violation_bench
Step 2  humans fill true_no_helmet / true_triple / true_no_seatbelt with y / n / ? (look at crops/)
Step 3  score      : python -m evaluation.violation_eval score --bench evaluation/violation_bench
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.common import DEPLOYED, ensure_dir, env_info, model_path, write_json  # noqa: E402
from evaluation.gate_eval import iter_frames  # noqa: E402


def legacy_seatbelt(rv, frame, car_box, persons) -> bool:
    saved = (rv.SEATBELT_MARGIN,)
    import detection.rule_violations as rvm
    orig = rvm._clahe_if_needed
    try:
        rv.SEATBELT_MARGIN = 0.0
        rvm._clahe_if_needed = lambda c: c
        return rv._check_car_seatbelt(frame=frame, vehicle_box=car_box, person_boxes=persons)
    finally:
        rv.SEATBELT_MARGIN, = saved
        rvm._clahe_if_needed = orig


def make_sheet(a) -> None:
    from ultralytics import YOLO
    from detection.rule_violations import RuleViolationDetector

    out = ensure_dir(a.out)
    crops = ensure_dir(out / "crops")
    det = YOLO(model_path(DEPLOYED["vehicle_model"]))
    rv = RuleViolationDetector()
    rows = []
    for src, idx, img in iter_frames(a.source, a.stride):
        r = det.predict(img, conf=0.30, imgsz=640, classes=[0, 2, 3], verbose=False)[0]
        xyxy = r.boxes.xyxy.cpu().numpy() if len(r.boxes) else []
        cls = r.boxes.cls.cpu().numpy() if len(r.boxes) else []
        cf = r.boxes.conf.cpu().numpy() if len(r.boxes) else []
        persons = [tuple(int(v) for v in b) for b, c in zip(xyxy, cls) if int(c) == 0]
        bikes = [tuple(int(v) for v in b) for b, c, s in zip(xyxy, cls, cf) if int(c) == 3 and s >= DEPLOYED["vehicle_conf"]]
        cars = [tuple(int(v) for v in b) for b, c, s in zip(xyxy, cls, cf) if int(c) == 2 and s >= DEPLOYED["vehicle_conf"]]
        rv.set_frame_context([(f"b{i}", "bike", b) for i, b in enumerate(bikes)], persons)
        for i, b in enumerate(bikes):
            riders = rv.riders_for(f"b{i}")
            cb = rv._rider_crop_box(img, b, riders)
            p_s = rv._head_conf(rv.helmet_model, img, cb, "no_helmet", riders)
            p_c = rv._head_conf(rv.twowheeler_model, img, cb, "no_helmet", riders)
            verdict = "issue" if (p_s > rv.GATE_THETA and p_c > rv.GATE_THETA) else (
                "clear" if (p_s <= rv.GATE_THETA and p_c <= rv.GATE_THETA) else "review")
            legacy_helmet = rv._has(rv.twowheeler_model, img, b, "no_helmet") or rv._has(rv.helmet_model, img, b, "no_helmet")
            legacy_triple = rv._riders_inside(b, persons) >= rv.TRIPLE_RIDING_COUNT
            n = len(riders or [])
            new_triple = n <= 4 and (n >= 3 or (n >= 2 and rv._has(rv.twowheeler_model, img, cb, "three_seater")))
            name = f"{Path(src).stem}_{idx:06d}_b{i}.jpg"
            cv2.imwrite(str(crops / name), img[cb[1]:cb[3], cb[0]:cb[2]])
            rows.append({"crop": name, "kind": "two_wheeler", "source": src, "frame": idx, "box": " ".join(map(str, b)),
                         "riders_assigned": n, "legacy_no_helmet": int(legacy_helmet), "new_no_helmet": int(verdict == "issue"),
                         "gate_verdict": verdict, "legacy_triple": int(legacy_triple), "new_triple": int(new_triple),
                         "legacy_no_seatbelt": "", "new_no_seatbelt": "",
                         "true_no_helmet": "", "true_triple": "", "true_no_seatbelt": ""})
        for i, b in enumerate(cars):
            if (b[2] - b[0]) < a.min_car_width:
                continue
            new_sb = rv._check_car_seatbelt(frame=img, vehicle_box=b, person_boxes=persons)
            old_sb = legacy_seatbelt(rv, img, b, persons)
            name = f"{Path(src).stem}_{idx:06d}_c{i}.jpg"
            cv2.imwrite(str(crops / name), img[b[1]:b[3], b[0]:b[2]])
            rows.append({"crop": name, "kind": "car", "source": src, "frame": idx, "box": " ".join(map(str, b)),
                         "riders_assigned": "", "legacy_no_helmet": "", "new_no_helmet": "", "gate_verdict": "",
                         "legacy_triple": "", "new_triple": "", "legacy_no_seatbelt": int(old_sb),
                         "new_no_seatbelt": int(new_sb), "true_no_helmet": "", "true_triple": "", "true_no_seatbelt": ""})
        if a.max_rows and len(rows) >= a.max_rows:
            break
    with open(out / "labels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["crop"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out/'labels.csv'}. Fill true_* with y / n / ? then run score.")


def prf(pairs):
    tp = sum(1 for p, t in pairs if p and t)
    fp = sum(1 for p, t in pairs if p and not t)
    fn = sum(1 for p, t in pairs if not p and t)
    tn = sum(1 for p, t in pairs if not p and not t)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
    return {"n": len(pairs), "TP": tp, "FP": fp, "FN": fn, "TN": tn, "precision": prec, "recall": rec, "F1": f1}


def score(a) -> None:
    bench = Path(a.bench)
    rows = list(csv.DictReader(open(bench / "labels.csv", newline="", encoding="utf-8")))
    res = {"env": env_info(), "note": "single-frame decisions; '?' labels excluded"}
    for rule, truth in (("no_helmet", "true_no_helmet"), ("triple", "true_triple"), ("no_seatbelt", "true_no_seatbelt")):
        lab = [r for r in rows if r.get(truth, "").strip().lower() in ("y", "n") and r.get(f"new_{rule}", "") != ""]
        if not lab:
            res[rule] = "no labelled rows"
            continue
        t = [r[truth].strip().lower() == "y" for r in lab]
        res[rule] = {"legacy": prf([(int(r[f"legacy_{rule}"]) == 1, y) for r, y in zip(lab, t)]),
                     "current": prf([(int(r[f"new_{rule}"]) == 1, y) for r, y in zip(lab, t)])}
        if rule == "no_helmet":
            rev = [y for r, y in zip(lab, t) if r["gate_verdict"] == "review"]
            res[rule]["sent_to_review"] = {"events": len(rev), "of_which_true_violations": sum(rev)}
    write_json(bench / "violation_results.json", res)
    import json
    print(json.dumps(res, indent=2, default=str))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make-sheet")
    m.add_argument("--source", nargs="+", required=True)
    m.add_argument("--stride", type=int, default=10)
    m.add_argument("--min-car-width", type=int, default=120)
    m.add_argument("--max-rows", type=int, default=600)
    m.add_argument("--out", default="evaluation/violation_bench")
    s = sub.add_parser("score")
    s.add_argument("--bench", default="evaluation/violation_bench")
    a = ap.parse_args()
    make_sheet(a) if a.cmd == "make-sheet" else score(a)


if __name__ == "__main__":
    main()
