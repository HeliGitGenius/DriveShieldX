"""Accident detector evaluation on labelled clips.

labels.csv columns: video, accident_start_s, accident_end_s   (one row per crash;
a clip with no crash appears once with empty times). Optional columns
clip_start_s, clip_end_s limit the part of the video that is processed (keeps
long CCTV recordings fast); evaluation/accident_labels_ucf.py writes them. Every clip is run through
the deployed tracker + AccidentMonitor (no database writes, no dispatch).

Reports event-level precision / recall, false alarms per hour of video and mean
time-to-detect (seconds from the labelled start to the confirmation).
A detection counts as a true positive if it is confirmed between
accident_start_s and accident_end_s + tolerance.

  python -m evaluation.accident_eval --labels path\\to\\accident_labels.csv --tolerance 10

Tuning without touching the test set: build a DEV label file from other clips
(accident_labels_ucf --dev), cache the tracks once, then
  python -m evaluation.accident_eval --labels dev.csv --tracks-cache C:\\data\\ucf\\tracks --sweep
and apply the chosen settings to the test set with --set k=v ... .
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from evaluation.common import DEPLOYED, env_info, model_path, write_json
from detection.accident_monitor import AccidentConfig, AccidentMonitor


def track_clip(path: str, model, start_s: float = 0.0, end_s: float | None = None,
               conf: float | None = None, imgsz: int = 640) -> dict:
    """Run the deployed detector + ByteTrack once; return the per-frame tracks."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    first = int(round(start_s * fps)) if start_s else 0
    if first:
        cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    last = int(round(end_s * fps)) if end_s else None
    model.predictor = None  # fresh tracker state per clip
    f, frames = first, []
    while last is None or f < last:
        ok, img = cap.read()
        if not ok:
            break
        f += 1
        r = model.track(img, persist=True, conf=conf if conf is not None else DEPLOYED["vehicle_conf"], imgsz=imgsz,
                        classes=DEPLOYED["vehicle_classes"], tracker="bytetrack.yaml", verbose=False)[0]
        tracks = []
        if r.boxes.id is not None:
            tracks = [[str(int(i)), [round(float(v), 1) for v in b]]
                      for i, b in zip(r.boxes.id.cpu().numpy(), r.boxes.xyxy.cpu().numpy())]
        frames.append([f, tracks])
    cap.release()
    return {"fps": fps, "first": first, "frames": frames}


def replay(tr: dict, cfg: AccidentConfig) -> tuple[list[float], float]:
    """Feed cached tracks to the accident monitor (cheap: seconds per clip)."""
    mon = AccidentMonitor(fps=tr["fps"], cfg=cfg)
    times = []
    for f, tracks in tr["frames"]:
        for _ in mon.update(f, [(t, tuple(b)) for t, b in tracks]):
            times.append(f / tr["fps"])
    return times, len(tr["frames"]) / tr["fps"]


def run_clip(path: str, model, start_s: float = 0.0, end_s: float | None = None) -> tuple[list[float], float]:
    return replay(track_clip(path, model, start_s, end_s), AccidentConfig())


def _cached_tracks(cache: Path | None, vid: str, win: tuple, conf, imgsz: int, model_getter) -> dict:
    key = hashlib.sha1(json.dumps([str(Path(vid).resolve()), win, conf, imgsz]).encode()).hexdigest()[:16]
    f = cache / f"{Path(vid).stem}_{key}.json" if cache else None
    if f and f.exists():
        return json.loads(f.read_text())
    tr = track_clip(vid, model_getter(), *win, conf=conf, imgsz=imgsz)
    if f:
        f.write_text(json.dumps(tr))
    return tr


def score(gt: dict, dets: dict, durs: dict, tolerance: float) -> dict:
    tp = fp = fn = 0
    ttd, per_clip = [], {}
    for vid, events in gt.items():
        matched = set()
        for t in dets[vid]:
            hit = next((i for i, (s, e) in enumerate(events) if s <= t <= e + tolerance and i not in matched), None)
            if hit is None:
                fp += 1
            else:
                matched.add(hit); tp += 1; ttd.append(t - events[hit][0])
        fn += len(events) - len(matched)
        per_clip[vid] = {"detections_s": dets[vid], "events": events}
    hours = sum(durs.values()) / 3600
    crash_clips = [v for v, e in gt.items() if e]
    normal_clips = [v for v, e in gt.items() if not e]
    return {"clips": len(gt), "hours": hours, "TP": tp, "FP": fp, "FN": fn,
            "crashes_detected": f"{tp}/{tp + fn}",
            "normal_clips_with_false_alarm": f"{sum(1 for v in normal_clips if dets[v])}/{len(normal_clips)}",
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "false_alarms_per_hour": fp / hours if hours else None,
            "mean_time_to_detect_s": float(np.mean(ttd)) if ttd else None, "per_clip": per_clip}


SWEEP = {
    "impact_memory_s": [0.0, 1.5, 3.0],
    "spatial_confirm": [False, True],
    "confirm_s": [2.0, 1.0],
    "iou_contact": [0.25, 0.15],
    "v_move": [0.8, 0.5],
}


def _parse_set(items) -> dict:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        cur = getattr(AccidentConfig(), k)
        out[k] = (v.lower() in ("1", "true", "yes")) if isinstance(cur, bool) else type(cur)(v)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--tolerance", type=float, default=10.0)
    ap.add_argument("--out", help="result file (default evaluation/results/accident_eval.json, "
                                  "or accident_sweep.json with --sweep)")
    ap.add_argument("--tracks-cache", help="folder to save/reuse tracker output (tracking runs once per clip)")
    ap.add_argument("--conf", type=float, help="detector confidence for tracking (default: deployed 0.35)")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--set", nargs="*", help="AccidentConfig overrides, e.g. confirm_s=1.0 spatial_confirm=false")
    ap.add_argument("--model", help="learned crash model checkpoint (accident_r3d18.pt) instead of the rule monitor")
    ap.add_argument("--threshold", type=float, help="with --model: override the checkpoint's validation threshold")
    ap.add_argument("--sweep", action="store_true",
                    help="try a grid of monitor settings on cached tracks (use on a DEV set, never the test set)")
    a = ap.parse_args()
    gt = defaultdict(list)
    window = {}
    for r in csv.DictReader(open(a.labels, newline="", encoding="utf-8-sig")):
        gt[r["video"]]
        if r.get("clip_start_s") or r.get("clip_end_s"):
            window[r["video"]] = (float(r.get("clip_start_s") or 0), float(r["clip_end_s"]) if r.get("clip_end_s") else None)
        if r.get("accident_start_s"):
            gt[r["video"]].append((float(r["accident_start_s"]), float(r["accident_end_s"] or r["accident_start_s"])))
    if a.model:
        from detection.accident_classifier import AccidentVideoClassifier
        clf = AccidentVideoClassifier(a.model)
        if a.threshold is not None:
            clf.threshold = a.threshold
        dets, durs = {}, {}
        for n, vid in enumerate(gt, 1):
            print(f"[{n}/{len(gt)}] {Path(vid).name}", flush=True)
            lo, hi = window.get(vid, (0.0, None))
            dets[vid] = clf.detect_video(vid, lo, hi)
            if hi is None:
                cap = cv2.VideoCapture(vid)
                hi = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 25.0)
                cap.release()
            durs[vid] = hi - lo
        res = {"env": env_info(), "labels": a.labels, "detector": "learned R3D-18 crash model",
               "model": a.model, "threshold": clf.threshold, "consecutive": clf.consecutive,
               "model_info": {k: v for k, v in clf.info.items() if k in ("epoch", "val", "arch")},
               **score(gt, dets, durs, a.tolerance)}
        write_json(a.out or "evaluation/results/accident_eval_model.json", res)
        print({k: v for k, v in res.items() if k not in ("per_clip", "env", "model_info")})
        return
    cache = Path(a.tracks_cache) if a.tracks_cache else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
    _model = []

    def model_getter():
        if not _model:
            from ultralytics import YOLO
            _model.append(YOLO(model_path(DEPLOYED["vehicle_model"])))
        return _model[0]

    tracks = {}
    for n, vid in enumerate(gt, 1):
        print(f"[{n}/{len(gt)}] {Path(vid).name}", flush=True)
        tracks[vid] = _cached_tracks(cache, vid, window.get(vid, (0.0, None)), a.conf, a.imgsz, model_getter)

    def evaluate(cfg):
        dets, durs = {}, {}
        for vid in gt:
            dets[vid], durs[vid] = replay(tracks[vid], cfg)
        return score(gt, dets, durs, a.tolerance)

    base = {"env": env_info(), "labels": a.labels, "detector_conf": a.conf or DEPLOYED["vehicle_conf"], "imgsz": a.imgsz}
    if a.sweep:
        rows = []
        keys = list(SWEEP)
        for combo in itertools.product(*SWEEP.values()):
            over = dict(zip(keys, combo))
            r = evaluate(AccidentConfig(**over))
            rows.append({**over, **{k: r[k] for k in ("TP", "FP", "FN", "crashes_detected",
                                                      "normal_clips_with_false_alarm", "recall", "precision")}})
        rows.sort(key=lambda r: (-(r["TP"]), r["FP"]))
        write_json(a.out or "evaluation/results/accident_sweep.json", {**base, "grid": SWEEP, "results": rows})
        print("best settings on this set (most crashes found, then fewest false alarms):")
        for r in rows[:10]:
            print(r)
        return
    over = _parse_set(a.set)
    res = {**base, "monitor_settings": {**AccidentConfig().__dict__, **over}, **evaluate(AccidentConfig(**over))}
    write_json(a.out or "evaluation/results/accident_eval.json", res)
    print({k: v for k, v in res.items() if k not in ("per_clip", "monitor_settings", "env")})


if __name__ == "__main__":
    main()
