"""Disagreement-gate evaluation (paper Sec. 5.4 "Enforcement" + ablation row
"without the disagreement gate").

Step 1  measure  : run on real footage (videos or image folders). For every
                   detected two-wheeler, compute p_s (standalone helmet model)
                   and p_c (combined two-wheeler model) on the same crop and
                   record the gate verdict. Reports the WITHHELD RATE:
                       withheld = disagree / (agree_positive + disagree)
                   i.e. the share of helmet events the old OR rule would have
                   issued that the gate sends to officer review instead.
                   Also writes review_sheet.csv + crops for human judgement.

Step 2  score    : after a person fills the `human_label` column of
                   review_sheet.csv with  violation / no_violation / unclear,
                   computes the FALSE-ISSUANCE RATE with the gate (issue only on
                   agreement) and without it (legacy OR rule) on the same events.

Usage:
  python -m evaluation.gate_eval measure --source path\\to\\triple_riding_video_1.mp4 path\\to\\frames_dir --out evaluation/results/gate
  python -m evaluation.gate_eval score --sheet evaluation/results/gate/review_sheet.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterator, Tuple

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.common import DEPLOYED, ensure_dir, env_info, model_path, write_json  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
VID_EXT = {".mp4", ".avi", ".mov", ".mkv"}


def iter_frames(sources, stride: int) -> Iterator[Tuple[str, int, np.ndarray]]:
    for src in sources:
        p = Path(src)
        if p.is_dir():
            for i, f in enumerate(sorted(x for x in p.iterdir() if x.suffix.lower() in IMG_EXT)):
                img = cv2.imread(str(f))
                if img is not None:
                    yield f.name, i, img
        elif p.suffix.lower() in VID_EXT:
            cap = cv2.VideoCapture(str(p))
            i = 0
            while True:
                ok, img = cap.read()
                if not ok:
                    break
                if i % stride == 0:
                    yield p.name, i, img
                i += 1
        elif p.suffix.lower() in IMG_EXT:
            img = cv2.imread(str(p))
            if img is not None:
                yield p.name, 0, img


def measure(a) -> None:
    from ultralytics import YOLO
    from detection.rule_violations import RuleViolationDetector

    out = ensure_dir(a.out)
    crops_dir = ensure_dir(out / "crops")
    det = YOLO(model_path(DEPLOYED["vehicle_model"]))
    rv = RuleViolationDetector()
    if rv.helmet_model is None or rv.twowheeler_model is None:
        raise SystemExit("Both helmet_yolov8.pt and twowheeler_best.pt must be in models/ to evaluate the gate.")
    theta = rv.GATE_THETA
    rows = []
    counts = {"agree_positive": 0, "agree_negative": 0, "disagree": 0}
    for src, idx, img in iter_frames(a.source, a.stride):
        h, w = img.shape[:2]
        if w > DEPLOYED["resize_width"]:
            s = DEPLOYED["resize_width"] / w
            img = cv2.resize(img, (DEPLOYED["resize_width"], int(h * s)), interpolation=cv2.INTER_AREA)
        r = det.predict(img, conf=0.30, imgsz=640, classes=[0, 3], verbose=False)[0]
        xyxy = r.boxes.xyxy.cpu().numpy() if len(r.boxes) else []
        cls = r.boxes.cls.cpu().numpy() if len(r.boxes) else []
        confs = r.boxes.conf.cpu().numpy() if len(r.boxes) else []
        persons = [tuple(int(v) for v in b) for b, c in zip(xyxy, cls) if int(c) == 0]
        bikes = [tuple(int(v) for v in b) for b, c, s_ in zip(xyxy, cls, confs)
                 if int(c) == 3 and s_ >= DEPLOYED["vehicle_conf"]]
        # same rider association + crop + head-region scoring as the deployed gate
        rv.set_frame_context([(str(i), "bike", b) for i, b in enumerate(bikes)], persons)
        for k, box in enumerate(bikes):
            riders = rv.riders_for(str(k))
            crop_box = rv._rider_crop_box(img, box, riders)
            p_s = rv._head_conf(rv.helmet_model, img, crop_box, "no_helmet", riders)
            p_c = rv._head_conf(rv.twowheeler_model, img, crop_box, "no_helmet", riders)
            if p_s > theta and p_c > theta:
                v = "agree_positive"
            elif p_s <= theta and p_c <= theta:
                v = "agree_negative"
            else:
                v = "disagree"
            counts[v] += 1
            crop_name = ""
            if v != "agree_negative":  # only OR-positive events can become challans -> need human judgement
                x1, y1, x2, y2 = crop_box
                crop = img[y1:y2, x1:x2]
                crop_name = f"{Path(src).stem}_{idx:06d}_{k}.jpg"
                cv2.imwrite(str(crops_dir / crop_name), crop)
            rows.append({"source": src, "frame": idx, "box": " ".join(map(str, box)), "riders": len(riders or []),
                         "p_standalone": round(p_s, 4),
                         "p_combined": round(p_c, 4), "theta": theta, "gate_verdict": v,
                         "or_rule_issues": int(v != "agree_negative"), "gate_issues": int(v == "agree_positive"),
                         "crop": crop_name, "human_label": ""})
    total = sum(counts.values())
    or_pos = counts["agree_positive"] + counts["disagree"]
    summary = {"env": env_info(), "sources": a.source, "stride": a.stride, "theta": theta,
               "two_wheeler_crops_evaluated": total, **counts,
               "withheld_rate": counts["disagree"] / or_pos if or_pos else None,
               "disagreement_rate_all_crops": counts["disagree"] / total if total else None,
               "note": "withheld_rate = disagree / (agree_positive + disagree): share of helmet events the legacy "
                       "OR rule would issue that the gate routes to officer review instead."}
    write_json(out / "gate_summary.json", summary)
    with open(out / "review_sheet.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["source"])
        wr.writeheader()
        wr.writerows([r for r in rows if r["or_rule_issues"]])
    with open(out / "all_crops.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["source"])
        wr.writeheader()
        wr.writerows(rows)
    print(summary)


def score(a) -> None:
    rows = list(csv.DictReader(open(a.sheet, newline="", encoding="utf-8")))
    judged = [r for r in rows if r.get("human_label", "").strip().lower() in {"violation", "no_violation"}]
    if not judged:
        raise SystemExit("Fill human_label (violation / no_violation / unclear) in the sheet first.")

    def fir(sel):
        issued = [r for r in judged if sel(r)]
        bad = [r for r in issued if r["human_label"].strip().lower() == "no_violation"]
        return {"issued": len(issued), "unsupported": len(bad), "false_issuance_rate": len(bad) / len(issued) if issued else None}

    with_gate = fir(lambda r: r["gate_verdict"] == "agree_positive")
    without_gate = fir(lambda r: r["gate_verdict"] in {"agree_positive", "disagree"})
    withheld = [r for r in judged if r["gate_verdict"] == "disagree"]
    withheld_true = sum(r["human_label"].strip().lower() == "violation" for r in withheld)
    res = {"judged_events": len(judged), "unclear_excluded": len(rows) - len(judged),
           "with_gate": with_gate, "without_gate_OR_rule": without_gate,
           "withheld_events": len(withheld), "withheld_that_were_real_violations": withheld_true,
           "definition": "false-issuance rate = automatically issued events a reviewer judged unsupported / "
                         "automatically issued events (paper Sec. 5.4)"}
    write_json(Path(a.sheet).with_name("gate_false_issuance.json"), res)
    print(res)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--source", nargs="+", required=True)
    m.add_argument("--stride", type=int, default=5, help="every Nth video frame")
    m.add_argument("--out", default="evaluation/results/gate")
    s = sub.add_parser("score")
    s.add_argument("--sheet", required=True)
    a = ap.parse_args()
    measure(a) if a.cmd == "measure" else score(a)


if __name__ == "__main__":
    main()
