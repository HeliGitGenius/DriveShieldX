"""Speed field test: does the deployed speed estimator match a real, known speed?

Setup (see docs/SPEED_TEST.md): a phone on a stand films a straight road side-on;
two markers 10 m apart are visible; a scooter passes at a steady speed while a
second phone shows its GPS speed. One short video per pass.

For each video this runs the DEPLOYED detector + ByteTrack + SpeedEstimator, with
the pixel->metre scale taken from the two markers, and compares its speed with two
independent references:
  gps   : speed you wrote down from the GPS speedometer
  gate  : 10 m / time the vehicle takes to go from marker A to marker B (timing gate)

  python -m evaluation.speed_field_test frame --video C:\\data\\speed\\run01.mp4
      -> saves run01_frame.png with a pixel grid; read the two markers' x,y from it (or Paint)
  python -m evaluation.speed_field_test run --runs C:\\data\\speed\\runs.csv --mark-a 412,610 --mark-b 1490,605
      runs.csv columns: video,gps_kmh   (optional per-row ax,ay,bx,by if the phone moved)
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detection.speed_estimator import SpeedEstimator  # noqa: E402

TWO_WHEELER_AND_CARS = [1, 2, 3, 5, 7]   # COCO: bicycle, car, motorcycle, bus, truck


def _pt(s: str) -> Tuple[float, float]:
    x, y = s.split(",")
    return float(x), float(y)


def analyse_track(points: Sequence[Tuple[int, float, float]], fps: float, a, b, distance_m: float) -> dict:
    """points: (frame, cx, cy) of one vehicle. Returns estimator and timing-gate speeds."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ab = b - a
    length_px = float(np.linalg.norm(ab))
    u = ab / length_px
    scale = distance_m / length_px                       # metres per pixel along the road
    est = SpeedEstimator(fps=fps, pixel_to_meter=scale)
    readings, path = [], []
    for i, (f, x, y) in enumerate(points):
        s = float(np.dot(np.array([x, y]) - a, u)) / length_px      # 0 at A, 1 at B
        path.append((f, s))
        sp = est.estimate_speed([(px, py) for _, px, py in points[: i + 1]], vehicle_id=1)
        if sp is not None and 0.0 <= s <= 1.0:
            readings.append(sp)

    def crossing(level: float) -> Optional[float]:
        for (f0, s0), (f1, s1) in zip(path, path[1:]):
            if (s0 - level) * (s1 - level) <= 0 and s0 != s1:
                return f0 + (level - s0) / (s1 - s0) * (f1 - f0)
        return None

    fa, fb = crossing(0.0), crossing(1.0)
    gate = None
    if fa is not None and fb is not None and fb != fa:
        gate = distance_m / (abs(fb - fa) / fps) * 3.6
    return {"estimator_kmh": float(np.median(readings)) if readings else None,
            "estimator_readings": len(readings), "gate_kmh": gate,
            "metres_per_pixel": scale}


def track_video(path: str, conf: float = 0.35) -> Tuple[float, List[Tuple[int, float, float]]]:
    """Deployed detector + ByteTrack; returns fps and the longest track's centroids."""
    from ultralytics import YOLO
    from evaluation.common import DEPLOYED, model_path
    model = YOLO(model_path(DEPLOYED["vehicle_model"]))
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    tracks: dict = {}
    f = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        f += 1
        r = model.track(img, persist=True, conf=conf, imgsz=640, classes=TWO_WHEELER_AND_CARS,
                        tracker="bytetrack.yaml", verbose=False)[0]
        if r.boxes.id is None:
            continue
        for tid, (x1, y1, x2, y2) in zip(r.boxes.id.cpu().numpy(), r.boxes.xyxy.cpu().numpy()):
            tracks.setdefault(int(tid), []).append((f, (x1 + x2) / 2, (y1 + y2) / 2))
    cap.release()
    if not tracks:
        return fps, []
    return fps, max(tracks.values(), key=lambda pts: abs(pts[-1][1] - pts[0][1]))   # longest horizontal travel


def save_frame(video: str, out: Optional[str] = None) -> str:
    cap = cv2.VideoCapture(video)
    ok, img = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"Cannot read {video}")
    h, w = img.shape[:2]
    for x in range(0, w, 100):
        cv2.line(img, (x, 0), (x, h), (0, 255, 255), 1)
        cv2.putText(img, str(x), (x + 2, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    for y in range(0, h, 100):
        cv2.line(img, (0, y), (w, y), (0, 255, 255), 1)
        cv2.putText(img, str(y), (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    out = out or str(Path(video).with_name(Path(video).stem + "_frame.png"))
    cv2.imwrite(out, img)
    print(f"saved {out} ({w}x{h}). Read the x,y of marker A and marker B (Paint shows exact pixels).")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("frame")
    f.add_argument("--video", required=True)
    r = sub.add_parser("run")
    r.add_argument("--runs", required=True)
    r.add_argument("--mark-a", help="x,y of marker A in pixels")
    r.add_argument("--mark-b", help="x,y of marker B in pixels")
    r.add_argument("--distance", type=float, default=10.0, help="metres between the markers")
    r.add_argument("--out", default="evaluation/results/speed_field_test.json")
    a = ap.parse_args()
    if a.cmd == "frame":
        save_frame(a.video)
        return

    from evaluation.common import env_info, write_json
    rows = list(csv.DictReader(open(a.runs, newline="", encoding="utf-8-sig")))
    results = []
    for n, row in enumerate(rows, 1):
        ma = (float(row["ax"]), float(row["ay"])) if row.get("ax") else _pt(a.mark_a)
        mb = (float(row["bx"]), float(row["by"])) if row.get("bx") else _pt(a.mark_b)
        fps, pts = track_video(row["video"])
        res = analyse_track(pts, fps, ma, mb, a.distance) if pts else {"estimator_kmh": None, "gate_kmh": None}
        gps = float(row["gps_kmh"]) if row.get("gps_kmh") else None
        res.update({"video": Path(row["video"]).name, "fps": fps, "gps_kmh": gps, "track_points": len(pts)})
        results.append(res)
        print(f"[{n}/{len(rows)}] {res['video']}: estimator {res['estimator_kmh']} km/h | gate {res['gate_kmh']} | "
              f"gps {gps}", flush=True)

    def mae(key, ref):
        d = [abs(x[key] - x[ref]) for x in results if x.get(key) is not None and x.get(ref) is not None]
        return (round(float(np.mean(d)), 2), len(d)) if d else (None, 0)

    def mape(key, ref):
        d = [abs(x[key] - x[ref]) / x[ref] for x in results if x.get(key) and x.get(ref)]
        return round(100 * float(np.mean(d)), 1) if d else None

    summary = {"runs": len(results),
               "estimator_vs_gps_MAE_kmh": mae("estimator_kmh", "gps_kmh"),
               "estimator_vs_gps_mean_pct_error": mape("estimator_kmh", "gps_kmh"),
               "estimator_vs_gate_MAE_kmh": mae("estimator_kmh", "gate_kmh"),
               "estimator_vs_gate_mean_pct_error": mape("estimator_kmh", "gate_kmh"),
               "gate_vs_gps_MAE_kmh (reference agreement)": mae("gate_kmh", "gps_kmh")}
    write_json(a.out, {"env": env_info(), "distance_m": a.distance, "summary": summary, "runs": results})
    print(summary)


if __name__ == "__main__":
    main()
