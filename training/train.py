"""Train / re-train a DriveShieldX detector reproducibly.

Hyper-parameters are copied from the DEPLOYED checkpoint's embedded train_args
(so a retrain is comparable to the model in production), then any CLI override
is applied. The result is written as a *candidate* with a model card; the
deployed weight in models/ is only replaced with --promote, and the previous
one is archived first -- nothing is ever overwritten silently.

Usage (Kaggle GPU or local):
  python -m training.train --task helmet --data /kaggle/working/merged_helmet/data.yaml
  python -m training.train --task plate  --data data/plates/data.yaml --epochs 120 --seed 1
  python -m training.train --task helmet --data ... --promote        # after reviewing the card
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOYED = {"helmet": "helmet_yolov8.pt", "seatbelt": "seatbelt_yolov8.pt",
            "twowheeler": "twowheeler_best.pt", "plate": "plate_best_v2.pt"}
KEEP = ["epochs", "batch", "imgsz", "optimizer", "lr0", "lrf", "momentum", "weight_decay", "warmup_epochs",
        "warmup_bias_lr", "patience", "cos_lr", "close_mosaic", "mosaic", "mixup", "copy_paste", "hsv_h", "hsv_s",
        "hsv_v", "degrees", "translate", "scale", "shear", "perspective", "fliplr", "flipud", "cls", "box", "dfl",
        "seed"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def main() -> None:
    import torch
    from ultralytics import YOLO

    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=list(DEPLOYED), required=True)
    ap.add_argument("--data", required=True, help="YOLO data.yaml")
    ap.add_argument("--base", default=None, help="override base weights (default: same as deployed run)")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--batch", type=int)
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default=str(ROOT / "training" / "runs"))
    ap.add_argument("--promote", action="store_true", help="install the new weight into models/ (old one archived)")
    a = ap.parse_args()

    deployed = ROOT / "models" / DEPLOYED[a.task]
    hp = {}
    base = "yolov8n.pt"
    if deployed.exists():
        ta = torch.load(deployed, map_location="cpu", weights_only=False).get("train_args") or {}
        hp = {k: ta[k] for k in KEEP if k in ta}
        base = Path(ta.get("model", base)).name
    for k in ("epochs", "batch", "imgsz", "seed"):
        if getattr(a, k) is not None:
            hp[k] = getattr(a, k)
    base = a.base or base
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"{a.task}_{stamp}"
    model = YOLO(base)
    model.train(data=a.data, device=a.device, project=a.out, name=name, exist_ok=True, deterministic=True, **hp)
    best = Path(a.out) / name / "weights" / "best.pt"
    v = YOLO(str(best)).val(data=a.data, device=a.device, plots=True, verbose=False, project=a.out,
                            name=f"{name}_val", exist_ok=True)
    per_class = []
    for i, c in enumerate(v.box.ap_class_index):
        p, r, ap50, ap = v.box.class_result(i)
        per_class.append({"class": v.names[int(c)], "precision": p, "recall": r, "mAP50": ap50, "mAP50-95": ap})
    card = {"task": a.task, "created": stamp, "git_commit": git_commit(), "base": base, "data": a.data,
            "hyperparameters": hp, "weights": str(best), "sha256": sha256(best),
            "metrics": {"precision": float(v.box.mp), "recall": float(v.box.mr), "mAP50": float(v.box.map50),
                        "mAP50-95": float(v.box.map)}, "per_class": per_class}
    cand_dir = ROOT / "models" / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    cand = cand_dir / f"{name}.pt"
    shutil.copy(best, cand)
    (cand_dir / f"{name}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    print(json.dumps(card["metrics"], indent=2))
    print(f"Candidate saved: {cand}  (model card next to it)")
    if a.promote:
        archive = ROOT / "models" / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        if deployed.exists():
            shutil.copy(deployed, archive / f"{deployed.stem}_{stamp}.pt")
        shutil.copy(cand, deployed)
        print(f"Promoted to {deployed}; previous weight archived in {archive}")


if __name__ == "__main__":
    main()
