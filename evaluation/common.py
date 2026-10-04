"""Shared helpers for the evaluation scripts."""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = REPO_ROOT / "evaluation" / "results"

# Deployed settings, read from the code paths they come from (see paper Table 2).
DEPLOYED = {
    "vehicle_model": "yolov8n.pt",          # HybridTracker default (COCO)
    "vehicle_classes": [2, 3, 5, 7],        # detection/vehicle_detector.py VEHICLE_CLASSES
    "vehicle_conf": 0.35,                   # HybridTracker(conf=0.35)
    "imgsz": 640,
    "resize_width": 960,                    # advanced_pipeline default target width
    "centroid_max_disappeared": 25,         # HybridTracker -> CentroidTracker(...)
    "centroid_max_distance": 100,
    "pixel_to_meter": 0.045,
    "hardcoded_fps": 25.0,
    "speed_limit": 60.0,
    "speed_history_n": 12,                  # get_recent_positions(n=12)
    "speed_window": 5,                      # SpeedEstimator(smoothing_window=5)
}


def ensure_dir(p: Path | str) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def env_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
    except Exception:
        pass
    try:
        import ultralytics
        info["ultralytics"] = ultralytics.__version__
    except Exception:
        pass
    return info


def write_json(path: Path | str, obj: Any) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=float)


def model_path(name: str) -> str:
    """Resolve a weight file the same way the app does (models/ first, then repo root)."""
    for cand in (REPO_ROOT / "models" / name, REPO_ROOT / "backend" / "models" / name, REPO_ROOT / name):
        if cand.exists():
            return str(cand)
    return name  # let ultralytics resolve / download
