"""Model provenance from the checkpoint itself (paper Table 1 / Table 2).

Every Ultralytics checkpoint embeds its training arguments (train_args) and the
validation metrics of the saved epoch (train_metrics). This prints them and,
more usefully, lists every hyper-parameter that differs from Ultralytics'
defaults -- which is what "augmentation: Ultralytics defaults" has to be checked
against.

Usage:  python -m training.inspect_checkpoint models/helmet_yolov8.pt [more.pt ...] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def inspect(path: str) -> dict:
    import torch
    from ultralytics.cfg import DEFAULT_CFG_DICT

    ck = torch.load(path, map_location="cpu", weights_only=False)
    ta = dict(ck.get("train_args") or {})
    tm = {k: float(v) for k, v in (ck.get("train_metrics") or {}).items() if isinstance(v, (int, float))}
    ignore = {"data", "model", "project", "name", "save_dir", "exist_ok", "device", "workers", "cache", "plots",
              "val", "resume", "pretrained", "tracker", "source", "mode", "task"}
    non_default = {k: {"used": v, "default": DEFAULT_CFG_DICT.get(k)} for k, v in ta.items()
                   if k not in ignore and k in DEFAULT_CFG_DICT and DEFAULT_CFG_DICT.get(k) != v}
    model = ck.get("model")
    return {"file": str(path), "date": str(ck.get("date")), "ultralytics_version": ck.get("version"),
            "base_model": ta.get("model"), "data": ta.get("data"), "classes": getattr(model, "names", None),
            "epochs": ta.get("epochs"), "batch": ta.get("batch"), "imgsz": ta.get("imgsz"),
            "optimizer": ta.get("optimizer"), "lr0": ta.get("lr0"), "momentum": ta.get("momentum"),
            "weight_decay": ta.get("weight_decay"), "seed": ta.get("seed"), "patience": ta.get("patience"),
            "cos_lr": ta.get("cos_lr"), "train_metrics": tm, "non_default_args": non_default}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("weights", nargs="+")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    out = [inspect(w) for w in a.weights]
    for r in out:
        print(f"\n== {Path(r['file']).name}  ({r['date']}, ultralytics {r['ultralytics_version']})")
        for k in ("base_model", "data", "classes", "epochs", "batch", "imgsz", "optimizer", "lr0", "momentum",
                  "weight_decay", "seed", "patience", "cos_lr"):
            print(f"  {k:>13}: {r[k]}")
        print("  train_metrics:", {k: round(v, 4) for k, v in r["train_metrics"].items()})
        print("  differs from Ultralytics defaults:", {k: v["used"] for k, v in r["non_default_args"].items()} or "nothing")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
