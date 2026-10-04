"""ANPR recognition benchmark: exact-match accuracy and character error rate
(paper Sec. 5.4 / Table 5 rows marked "not measured").

Step 1  make-sheet : detect vehicles in real footage, run the YOLOv8 plate
                     localiser on each, and save the vehicle crop + plate box.
                     Produces labels.csv with an empty `true_text` column.
Step 2  (humans)   : type the true plate string for each row into `true_text`
                     (letters/digits only, e.g. MH12AB1234). Use UNREADABLE when
                     a person cannot read it either -- those rows are excluded.
                     Two people labelling independently is recommended.
Step 3  score      : runs both OCR paths on every labelled row and reports
                     exact-match accuracy, CER, no-read rate and localisation
                     recall, separately, so detector and OCR failures are not
                     conflated:
                       (A) two-stage  : YOLOv8 plate box -> deblur/sharpen -> EasyOCR
                       (B) legacy     : Haar cascade / heuristic crop -> EasyOCR
                       (C) two-stage + format-aware decoding (Indian plate grammar)

Usage:
  python -m evaluation.anpr_benchmark make-sheet --source video1.mp4 frames_dir --out evaluation/anpr_bench
  python -m evaluation.anpr_benchmark score --bench evaluation/anpr_bench
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.common import DEPLOYED, ensure_dir, env_info, model_path, write_json  # noqa: E402
from evaluation.gate_eval import iter_frames  # noqa: E402

VEHICLE_FOR_PLATES = [2, 3, 5, 7]


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def make_sheet(a) -> None:
    from ultralytics import YOLO
    from detection.rule_violations import RuleViolationDetector

    out = ensure_dir(a.out)
    vdir = ensure_dir(out / "vehicles")
    det = YOLO(model_path(DEPLOYED["vehicle_model"]))
    rv = RuleViolationDetector()
    rows = []
    seen = 0
    for src, idx, img in iter_frames(a.source, a.stride):
        r = det.predict(img, conf=DEPLOYED["vehicle_conf"], imgsz=640, classes=VEHICLE_FOR_PLATES, verbose=False)[0]
        for k, b in enumerate(r.boxes.xyxy.cpu().numpy() if len(r.boxes) else []):
            x1, y1, x2, y2 = (int(v) for v in b)
            if (x2 - x1) < a.min_width:
                continue  # plates on tiny vehicles are unreadable for humans too
            veh = img[max(0, y1):y2, max(0, x1):x2]
            if veh.size == 0:
                continue
            loc = rv.locate_plate(img, (x1, y1, x2, y2))
            name = f"{Path(src).stem}_{idx:06d}_{k}.jpg"
            cv2.imwrite(str(vdir / name), veh)
            if loc is not None:
                (px1, py1, px2, py2), pconf = loc
                rel = f"{px1 - x1} {py1 - y1} {px2 - x1} {py2 - y1}"
            else:
                rel, pconf = "", 0.0
            rows.append({"vehicle_file": name, "source": src, "frame": idx, "vehicle_box": f"{x1} {y1} {x2} {y2}",
                         "plate_box_in_vehicle": rel, "plate_conf": round(float(pconf), 4),
                         "true_text": "", "labeller": ""})
            seen += 1
            if a.max_rows and seen >= a.max_rows:
                break
        if a.max_rows and seen >= a.max_rows:
            break
    with open(out / "labels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["vehicle_file"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {out/'labels.csv'} -- fill `true_text` (or UNREADABLE) then run `score`.")


def score(a) -> None:
    from detection.npr import NumberPlateRecognizer

    bench = Path(a.bench)
    rows = list(csv.DictReader(open(bench / "labels.csv", newline="", encoding="utf-8")))
    lab = [r for r in rows if norm(r["true_text"]) and r["true_text"].strip().upper() != "UNREADABLE"]
    if not lab:
        raise SystemExit("No labelled rows: fill `true_text` in labels.csv first.")
    from detection.plate_format import correct

    npr = NumberPlateRecognizer(use_gpu=a.gpu, format_correction=False, engine=a.engine)  # raw reads; (C) applies it below
    if not npr.enabled:
        raise SystemExit("No OCR engine installed (pip install \"fast-plate-ocr[onnx]\" or easyocr).")
    if a.engine == "fastplate" and npr.fast is None:
        raise SystemExit("fast-plate-ocr is not installed: pip install \"fast-plate-ocr[onnx]\"")
    import time
    t0 = time.perf_counter()
    per_row = []
    for r in lab:
        veh = cv2.imread(str(bench / "vehicles" / r["vehicle_file"]))
        truth = norm(r["true_text"])
        h, w = veh.shape[:2]
        pa = ""
        if r["plate_box_in_vehicle"]:
            box = [int(v) for v in r["plate_box_in_vehicle"].split()]
            pa, _ = npr.read_plate_crop(veh, box)
            pa = norm(pa)
        pb, _ = npr.read_plate(veh, (0, 0, w, h))
        pb = norm(pb)
        pc = correct(pa)[0] or pa if pa else ""
        per_row.append({"vehicle_file": r["vehicle_file"], "true": truth, "localised": int(bool(r["plate_box_in_vehicle"])),
                        "pred_two_stage": pa, "pred_legacy": pb, "pred_two_stage_format": pc,
                        "exact_two_stage": int(pa == truth), "exact_legacy": int(pb == truth),
                        "exact_two_stage_format": int(pc == truth),
                        "cer_two_stage": levenshtein(pa, truth) / len(truth), "cer_legacy": levenshtein(pb, truth) / len(truth),
                        "cer_two_stage_format": levenshtein(pc, truth) / len(truth)})
    n = len(per_row)
    secs = time.perf_counter() - t0

    def agg(key):
        preds = [p[f"pred_{key}"] for p in per_row]
        return {"exact_match_accuracy": float(np.mean([p[f"exact_{key}"] for p in per_row])),
                "CER": float(sum(levenshtein(p[f"pred_{key}"], p["true"]) for p in per_row) / sum(len(p["true"]) for p in per_row)),
                "mean_per_plate_CER": float(np.mean([min(1.0, p[f"cer_{key}"]) for p in per_row])),
                "no_read_rate": float(np.mean([pr == "" for pr in preds]))}

    loc_rows = [p for p in per_row if p["localised"]]
    res = {"env": env_info(), "ocr_engine": npr.engine_name, "engine_arg": a.engine,
           "seconds_per_plate_both_paths": round(secs / max(1, n), 3), "labelled_plates": n, "excluded_unreadable_or_blank": len(rows) - n,
           "localisation_recall_on_readable_plates": len(loc_rows) / n,
           "two_stage_yolo_easyocr": agg("two_stage"),
           "two_stage_on_localised_only": {
               "n": len(loc_rows),
               "exact_match_accuracy": float(np.mean([p["exact_two_stage"] for p in loc_rows])) if loc_rows else None},
           "legacy_cascade_easyocr": agg("legacy"),
           "two_stage_plus_format_decoding": agg("two_stage_format"),
           "definitions": {"exact_match": "predicted string == true string after removing non-alphanumerics",
                           "CER": "sum of Levenshtein edits / sum of true characters (S+D+I)/N"}}
    tag = "" if a.engine == "easyocr" else f"_{a.engine}"
    write_json(bench / f"anpr_results{tag}.json", res)
    with open(bench / f"anpr_per_plate{tag}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_row[0].keys()))
        w.writeheader()
        w.writerows(per_row)
    print(res)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make-sheet")
    m.add_argument("--source", nargs="+", required=True)
    m.add_argument("--stride", type=int, default=15)
    m.add_argument("--min-width", type=int, default=80)
    m.add_argument("--max-rows", type=int, default=400)
    m.add_argument("--out", default="evaluation/anpr_bench")
    s = sub.add_parser("score")
    s.add_argument("--bench", default="evaluation/anpr_bench")
    s.add_argument("--gpu", action="store_true")
    s.add_argument("--engine", choices=["easyocr", "fastplate", "auto"], default="easyocr",
                   help="OCR engine; results are written to anpr_results[_<engine>].json")
    a = ap.parse_args()
    make_sheet(a) if a.cmd == "make-sheet" else score(a)


if __name__ == "__main__":
    main()
