"""Build accident_eval labels from UCF-Crime (real CCTV, with crash timings).

UCF-Crime ships `Temporal_Anomaly_Annotation_for_Testing_Videos.txt`, one line per
test video:  <file> <class> <start1> <end1> <start2> <end2>   (frame numbers, -1 = none)
e.g.  RoadAccidents002_x264.mp4  RoadAccidents  1300  1500  -1  -1
      Normal_Videos_003_x264.mp4  Normal  -1  -1  -1  -1

This picks the RoadAccidents test videos (crashes) and Normal test videos (no crash),
converts frames to seconds with each video's own frame rate, and limits processing
to a window around each crash (default 30 s before to 30 s after) and to the first
60 s of each normal video, so 40 clips run in reasonable time on a CPU.

  python -m evaluation.accident_labels_ucf --annotations <txt> --videos <folder> --out C:\\data\\accidents\\accident_labels.csv
  python -m evaluation.accident_eval --labels C:\\data\\accidents\\accident_labels.csv --tolerance 10

--dev builds a separate TUNING set that never overlaps the test labels given with
--exclude: RoadAccidents videos WITHOUT timings (counted at clip level: the crash
window is the whole processed clip) and Normal videos not used in the test.
"""
from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

import cv2

VID_EXT = {".mp4", ".avi", ".mkv", ".mov"}
FIELDS = ["video", "accident_start_s", "accident_end_s", "clip_start_s", "clip_end_s"]


def parse_annotations(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        p = line.split()
        if len(p) < 4:
            continue
        nums = []
        for v in p[2:]:
            try:
                nums.append(int(float(v)))
            except ValueError:
                pass
        events = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2) if nums[i] >= 0 and nums[i + 1] >= 0]
        out[Path(p[0]).name] = (p[1], events)
    return out


def video_info(path: Path) -> tuple[float, float]:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return fps, (n / fps if n else 0.0)


def build_rows(ann: dict, files: dict, n_crash: int, n_normal: int, pre: float, post: float,
               normal_s: float, seed: int, info=video_info) -> list[dict]:
    rng = random.Random(seed)
    crash = sorted(k for k, (cls, ev) in ann.items() if cls.lower().startswith("roadaccident") and ev and k in files)
    normal = sorted(k for k, (cls, ev) in ann.items() if cls.lower().startswith("normal") and not ev and k in files)
    rng.shuffle(crash)
    rng.shuffle(normal)
    rows = []
    for name in crash[:n_crash]:
        fps, dur = info(files[name])
        events = [(s / fps, e / fps) for s, e in ann[name][1]]
        lo = max(0.0, min(s for s, _ in events) - pre)
        hi = max(e for _, e in events) + post
        if dur:
            hi = min(hi, dur)
        for s, e in events:
            rows.append({"video": str(files[name]), "accident_start_s": f"{s:.2f}", "accident_end_s": f"{e:.2f}",
                         "clip_start_s": f"{lo:.2f}", "clip_end_s": f"{hi:.2f}"})
    for name in normal[:n_normal]:
        _, dur = info(files[name])
        end = min(normal_s, dur) if dur else normal_s
        rows.append({"video": str(files[name]), "accident_start_s": "", "accident_end_s": "",
                     "clip_start_s": "0", "clip_end_s": f"{end:.2f}"})
    return rows


def build_dev_rows(ann: dict, files: dict, exclude: set, n_crash: int, n_normal: int, max_s: float,
                   normal_s: float, seed: int, info=video_info) -> list[dict]:
    rng = random.Random(seed)
    crash = sorted(k for k in files if k.lower().startswith("roadaccident") and k not in ann
                   and str(files[k]) not in exclude)
    normal = sorted(k for k in files if k.lower().startswith("normal") and str(files[k]) not in exclude
                    and (k not in ann or not ann[k][1]))
    rng.shuffle(crash)
    rng.shuffle(normal)
    rows = []
    for name in crash[:n_crash]:
        _, dur = info(files[name])
        end = min(max_s, dur) if dur else max_s
        rows.append({"video": str(files[name]), "accident_start_s": "0", "accident_end_s": f"{end:.2f}",
                     "clip_start_s": "0", "clip_end_s": f"{end:.2f}"})
    for name in normal[:n_normal]:
        _, dur = info(files[name])
        end = min(normal_s, dur) if dur else normal_s
        rows.append({"video": str(files[name]), "accident_start_s": "", "accident_end_s": "",
                     "clip_start_s": "0", "clip_end_s": f"{end:.2f}"})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotations", required=True, help="Temporal_Anomaly_Annotation_for_Testing_Videos.txt")
    ap.add_argument("--videos", required=True, help="folder containing the downloaded videos (searched recursively)")
    ap.add_argument("--out", default="evaluation/results/accident_labels.csv")
    ap.add_argument("--crash", type=int, default=20)
    ap.add_argument("--normal", type=int, default=20)
    ap.add_argument("--pre", type=float, default=30.0, help="seconds kept before a crash")
    ap.add_argument("--post", type=float, default=30.0, help="seconds kept after a crash")
    ap.add_argument("--normal-seconds", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dev", action="store_true", help="build a tuning set instead (see module help)")
    ap.add_argument("--exclude", help="test label csv whose videos must NOT appear in the dev set")
    ap.add_argument("--dev-max-seconds", type=float, default=120.0, help="crash videos: process at most this much")
    a = ap.parse_args()

    ann = parse_annotations(Path(a.annotations))
    files = {p.name: p.resolve() for p in Path(a.videos).rglob("*") if p.suffix.lower() in VID_EXT}
    if a.dev:
        if not a.exclude:
            raise SystemExit("--dev needs --exclude <test labels csv> so tuning never sees test videos.")
        with open(a.exclude, newline="", encoding="utf-8-sig") as f:
            excl = {str(Path(r["video"]).resolve()) for r in csv.DictReader(f)}
        rows = build_dev_rows(ann, files, excl, a.crash, a.normal, a.dev_max_seconds, a.normal_seconds, a.seed + 1)
    else:
        rows = build_rows(ann, files, a.crash, a.normal, a.pre, a.post, a.normal_seconds, a.seed)
    n_c = len({r["video"] for r in rows if r["accident_start_s"]})
    n_n = len({r["video"] for r in rows if not r["accident_start_s"]})
    if not rows:
        raise SystemExit("No annotated RoadAccidents/Normal test videos found in that folder.")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}: {n_c} crash videos ({sum(1 for r in rows if r['accident_start_s'])} crashes), "
          f"{n_n} normal videos.")
    if n_c < a.crash or n_n < a.normal:
        print("Fewer than requested: download more RoadAccidents / Normal *test* videos into the folder.")
    print(f"next: python -m evaluation.accident_eval --labels {out} --tolerance 10")


if __name__ == "__main__":
    main()
