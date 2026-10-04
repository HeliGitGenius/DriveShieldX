"""Step 1 of the tracking protocol: run the DEPLOYED vehicle detector once per
sequence and cache every box (conf >= 0.10), with per-frame inference time.

Deployed detector = HybridTracker default: COCO yolov8n.pt, classes car/motorcycle/
bus/truck, imgsz 640. The deployed confidence (0.35) is applied at replay time, so
the same cache also supports ByteTrack's low-score second stage if wanted.

Usage (UA-DETRAC on the project laptop):
  python -m evaluation.cache_detections --images-root C:\\datasets\\DETRAC-Images ^
      --ann-roots C:\\datasets\\DETRAC-Train-Annotations-XML C:\\datasets\\DETRAC-Test-Annotations-XML ^
      --num-seqs 10 --out evaluation/cache
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from evaluation.common import DEPLOYED, ensure_dir, env_info, model_path, write_json
from evaluation.detrac import find_detrac_sequences, load_detrac_sequence, pick_spread


def cache_sequence(model, seq, out_dir: Path, device: str, cache_conf: float, max_frames: int | None) -> dict:
    rows = []
    times = []
    shape = None
    n = seq.num_frames if not max_frames else min(seq.num_frames, max_frames)
    for f in range(1, n + 1):
        p = seq.frame_path(f)
        img = cv2.imread(str(p))
        if img is None:
            continue
        shape = img.shape
        t0 = time.perf_counter()
        res = model.predict(img, conf=cache_conf, imgsz=DEPLOYED["imgsz"], classes=DEPLOYED["vehicle_classes"],
                            device=device, verbose=False)[0]
        times.append(time.perf_counter() - t0)
        if res.boxes is not None and len(res.boxes):
            xyxy = res.boxes.xyxy.cpu().numpy()
            conf = res.boxes.conf.cpu().numpy()
            cls = res.boxes.cls.cpu().numpy()
            for b, c, k in zip(xyxy, conf, cls):
                rows.append((f, *b.tolist(), float(c), int(k)))
    dets = np.asarray(rows, dtype=np.float32).reshape(-1, 7)
    np.savez_compressed(out_dir / f"{seq.name}.npz", dets=dets, det_time=np.asarray(times),
                        shape=np.asarray(shape if shape else (0, 0, 3)), num_frames=n)
    t = np.asarray(times)
    return {"sequence": seq.name, "frames": int(n), "detections": int(len(dets)),
            "det_ms_mean": float(1000 * t.mean()) if len(t) else None,
            "det_fps": float(1.0 / t.mean()) if len(t) else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-root", required=True)
    ap.add_argument("--ann-roots", nargs="+", required=True)
    ap.add_argument("--seqs", nargs="*", help="explicit sequence names (overrides --num-seqs)")
    ap.add_argument("--num-seqs", type=int, default=10)
    ap.add_argument("--model", default=DEPLOYED["vehicle_model"])
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--cache-conf", type=float, default=0.10)
    ap.add_argument("--max-frames", type=int, default=None, help="debug only")
    ap.add_argument("--out", default="evaluation/cache")
    a = ap.parse_args()

    from ultralytics import YOLO

    out = ensure_dir(a.out)
    avail = find_detrac_sequences(a.images_root, a.ann_roots)
    if not avail:
        raise SystemExit("No sequences found that have both an image folder and an XML annotation.")
    names = [n for n, _, _ in avail]
    chosen = a.seqs if a.seqs else pick_spread(names, a.num_seqs)
    # Pre-registration: the sequence list is written BEFORE any tracker is run.
    write_json(out / "sequences.json", {"chosen": chosen, "available": len(names),
                                         "rule": "explicit" if a.seqs else f"pick_spread(sorted, k={a.num_seqs})"})
    lookup = {n: (d, x) for n, d, x in avail}
    model = YOLO(model_path(a.model))
    summary = []
    for name in chosen:
        d, x = lookup[name]
        seq = load_detrac_sequence(name, d, x)
        info = cache_sequence(model, seq, out, a.device, a.cache_conf, a.max_frames)
        print(json.dumps(info))
        summary.append(info)
    write_json(out / "cache_summary.json", {"env": env_info(), "model": model_path(a.model),
                                             "device": a.device, "sequences": summary})


if __name__ == "__main__":
    main()
