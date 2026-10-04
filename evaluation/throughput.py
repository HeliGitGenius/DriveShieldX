"""End-to-end throughput of the deployed pipeline on a video, per device
(paper Sec. 6.2: 0.088-2.03 FPS on CPU; "whether a GPU deployment reaches frame
rate is an open question"). Runs the real AdvancedOverspeedPipeline against a
throw-away COPY of the database so production data is untouched.

Usage:
  python -m evaluation.throughput --video path\\to\\triple_riding_video_1.mp4 --frames 200 --device cpu
  python -m evaluation.throughput --video ... --device gpu      (on a CUDA machine / Kaggle)
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from evaluation.common import REPO_ROOT, env_info, write_json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--device", choices=["cpu", "gpu"], default="cpu")
    ap.add_argument("--npr", action="store_true", help="enable OCR (as in a full enforcement run)")
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--out", default="evaluation/results/throughput")
    a = ap.parse_args()

    from database import db_manager

    tmp = Path(tempfile.mkdtemp()) / "throughput.db"
    shutil.copy(REPO_ROOT / "database" / "overspeed.db", tmp)
    db_manager.DB_PATH = tmp  # never write into the real DB
    from detection import advanced_pipeline as ap_mod

    p = ap_mod.AdvancedOverspeedPipeline(camera_id=1, source=a.video, source_type="file", tracker_mode="bytetrack",
                                         enable_npr=a.npr, gpu_enabled=(a.device == "gpu"))
    times = []
    last = time.perf_counter()
    for i, _ in enumerate(p.run_generator(max_frames=a.frames)):
        now = time.perf_counter()
        if i >= a.warmup:
            times.append(now - last)
        last = now
    t = np.asarray(times)
    res = {"env": env_info(), "video": a.video, "device": a.device, "npr": a.npr, "frames_timed": int(len(t)),
           "fps_mean": float(1 / t.mean()) if len(t) else None, "ms_per_frame_mean": float(1000 * t.mean()) if len(t) else None,
           "ms_per_frame_p50": float(1000 * np.median(t)) if len(t) else None,
           "ms_per_frame_p95": float(1000 * np.percentile(t, 95)) if len(t) else None,
           "gate_stats": dict(p.rule_detector.gate_stats)}
    write_json(Path(a.out) / f"throughput_{Path(a.video).stem}_{a.device}{'_npr' if a.npr else ''}.json", res)
    print(res)


if __name__ == "__main__":
    main()
