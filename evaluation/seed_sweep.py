"""Seed sweep: train each detector N times with different seeds and report
mean +/- sample s.d. (paper Sec. 5.5; Table 3 row "mean +/- s.d. over seeds").

GPU required (Kaggle T4 is fine). Hyper-parameters are copied from the deployed
checkpoint's train_args so the only thing that changes between runs is the seed.

Usage (Kaggle notebook cell):
  !python -m evaluation.seed_sweep --weights models/helmet_yolov8.pt --data /kaggle/working/merged_helmet/data.yaml --seeds 0 1 2 3 4
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from evaluation.common import ensure_dir, env_info, write_json
from evaluation.stats import mean_sd

KEEP = ["epochs", "batch", "imgsz", "optimizer", "lr0", "lrf", "momentum", "weight_decay", "warmup_epochs",
        "patience", "cos_lr", "close_mosaic", "mosaic", "mixup", "hsv_h", "hsv_s", "hsv_v", "degrees",
        "translate", "scale", "fliplr", "flipud"]


def main() -> None:
    import torch
    from ultralytics import YOLO

    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True, help="deployed checkpoint (hyper-params are read from it)")
    ap.add_argument("--data", required=True)
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4])
    ap.add_argument("--epochs", type=int, default=None, help="override (default: same as deployed run)")
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default="evaluation/results/seed_sweep")
    a = ap.parse_args()
    ck = torch.load(a.weights, map_location="cpu", weights_only=False)
    targs = ck.get("train_args") or {}
    base = targs.get("model", "yolov8n.pt")
    hp = {k: targs[k] for k in KEEP if k in targs}
    if a.epochs:
        hp["epochs"] = a.epochs
    out = ensure_dir(Path(a.out) / Path(a.weights).stem)
    runs = []
    for s in a.seeds:
        m = YOLO(Path(base).name)
        m.train(data=a.data, seed=s, deterministic=True, device=a.device, project=str(out), name=f"seed{s}",
                exist_ok=True, plots=False, **hp)
        v = m.val(data=a.data, device=a.device, plots=False, verbose=False)
        runs.append({"seed": s, "precision": float(v.box.mp), "recall": float(v.box.mr),
                     "mAP50": float(v.box.map50), "mAP50-95": float(v.box.map)})
        print(runs[-1])
    summ = {}
    for k in ("precision", "recall", "mAP50", "mAP50-95"):
        mu, sd = mean_sd([r[k] for r in runs])
        summ[k] = {"mean": mu, "sd": sd}
    write_json(out / "seed_sweep.json", {"env": env_info(), "weights": a.weights, "data": a.data,
                                         "base": base, "hyperparams": hp, "runs": runs, "summary": summ})
    print({k: f"{v['mean']:.3f} ± {v['sd']:.3f}" for k, v in summ.items()})


if __name__ == "__main__":
    main()
