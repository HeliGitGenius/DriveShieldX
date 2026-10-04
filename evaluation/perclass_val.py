"""Per-class detector validation (paper Table 3 row "per-class breakdown: not available").

Needs the validation split each model was trained with (the Kaggle
merged_helmet / merged_seatbelt / tw_merged / merged plate sets) or any
labelled YOLO-format set with the SAME class names. Run on Kaggle (GPU) or locally.

Usage:
  python -m evaluation.perclass_val --model models/helmet_yolov8.pt --data /kaggle/working/merged_helmet/data.yaml
  python -m evaluation.perclass_val --all-from-checkpoints   # uses the data path stored in each checkpoint
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from evaluation.common import REPO_ROOT, ensure_dir, env_info, write_json

MODELS = ["helmet_yolov8.pt", "seatbelt_yolov8.pt", "twowheeler_best.pt", "plate_best_v2.pt"]


def checkpoint_data_path(weights: str) -> str | None:
    import torch

    ck = torch.load(weights, map_location="cpu", weights_only=False)
    return (ck.get("train_args") or {}).get("data")


def val_one(weights: str, data: str, split: str, device: str, out: Path) -> list[dict]:
    from ultralytics import YOLO

    m = YOLO(weights)
    r = m.val(data=data, split=split, device=device, imgsz=640, batch=16, plots=False, verbose=False,
              project=str(out / "runs"), name=Path(weights).stem, exist_ok=True)
    b = r.box
    names = r.names
    rows = []
    for i, c in enumerate(b.ap_class_index):
        p, rc, ap50, ap = b.class_result(i)
        f1 = 2 * p * rc / (p + rc) if (p + rc) else 0.0
        rows.append({"model": Path(weights).name, "class": names[int(c)], "precision": p, "recall": rc, "F1": f1,
                     "mAP50": ap50, "mAP50-95": ap, "images": int(r.seen) if hasattr(r, "seen") else None})
    rows.append({"model": Path(weights).name, "class": "all", "precision": b.mp, "recall": b.mr,
                 "F1": 2 * b.mp * b.mr / (b.mp + b.mr) if (b.mp + b.mr) else 0.0, "mAP50": b.map50, "mAP50-95": b.map})
    # instance counts per class
    try:
        nt = r.nt_per_class
        for row in rows:
            if row["class"] != "all":
                k = [kk for kk, v in names.items() if v == row["class"]][0]
                row["instances"] = int(nt[k])
    except Exception:
        pass
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--data")
    ap.add_argument("--all-from-checkpoints", action="store_true")
    ap.add_argument("--split", default="val")
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default="evaluation/results/perclass")
    a = ap.parse_args()
    out = ensure_dir(a.out)
    jobs = []
    if a.all_from_checkpoints:
        for m in MODELS:
            w = str(REPO_ROOT / "models" / m)
            jobs.append((w, checkpoint_data_path(w)))
    else:
        jobs.append((a.model, a.data))
    rows = []
    for w, d in jobs:
        if not d or not Path(d).exists():
            print(f"SKIP {w}: data.yaml not found ({d})")
            continue
        rows += val_one(w, d, a.split, a.device, out)
    if rows:
        with open(out / "perclass.csv", "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}, key=list(rows[0]).index if False else None))
            wr.writeheader()
            wr.writerows(rows)
        write_json(out / "perclass.json", {"env": env_info(), "rows": rows})
        for r in rows:
            print(f"{r['model']:>22} {r['class']:>16}  P {r['precision']:.3f}  R {r['recall']:.3f}  "
                  f"mAP50 {r['mAP50']:.3f}  mAP50-95 {r['mAP50-95']:.3f}")


if __name__ == "__main__":
    main()
