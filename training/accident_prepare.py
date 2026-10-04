"""Make a small upload for training the crash model on Kaggle.

Takes the crash times you marked (accident_label_tool.py) plus normal videos,
resamples each to 8 fps at 128x171 and writes short mp4s, a manifest, and the two
code files Kaggle needs. The 23+23 test videos are excluded, always.

  python -m training.accident_prepare --train-labels C:\\data\\ucf\\accident_train_labels.csv --videos C:\\data\\ucf\\videos --exclude C:\\data\\ucf\\accident_labels.csv --out C:\\data\\ucf\\kaggle_upload
Result: C:\\data\\ucf\\kaggle_upload.zip (upload it as a Kaggle dataset).
"""
from __future__ import annotations

import argparse
import csv
import random
import shutil
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detection import accident_classifier as ac  # noqa: E402

VID = {".mp4", ".avi", ".mkv", ".mov"}


def write_mp4(frames, path: Path) -> None:
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), ac.FPS, (ac.SIZE[1], ac.SIZE[0]))
    for f in frames:
        w.write(f[:, :, ::-1])
    w.release()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train-labels", required=True)
    ap.add_argument("--videos", required=True)
    ap.add_argument("--exclude", required=True, help="test label csv: these videos are never used")
    ap.add_argument("--out", required=True)
    ap.add_argument("--normals", type=int, default=100)
    ap.add_argument("--normal-seconds", type=float, default=60.0)
    ap.add_argument("--context", type=float, default=40.0, help="seconds kept before/after each crash")
    ap.add_argument("--val-fraction", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    with open(a.exclude, newline="", encoding="utf-8-sig") as f:
        test = {Path(r["video"]).name for r in csv.DictReader(f)}
    with open(a.train_labels, newline="", encoding="utf-8-sig") as f:
        crashes = [r for r in csv.DictReader(f) if r.get("status", "crash") == "crash" and r["accident_start_s"]]
    if crashes:
        durs = sorted(float(r["accident_end_s"]) - float(r["accident_start_s"]) for r in crashes)
        early = sum(float(r["accident_start_s"]) < 1.0 for r in crashes)
        if durs[len(durs) // 2] < 0.5 or early > 0.3 * len(crashes):
            raise SystemExit(f"Refusing: the crash times look wrong ({early}/{len(crashes)} start before 1 s, "
                             f"median length {durs[len(durs) // 2]:.2f} s). They were probably typed as "
                             "minutes.seconds; re-label with the current labelling page (0:12 = 12 s).")
    leak = [r["video"] for r in crashes if Path(r["video"]).name in test]
    if leak:
        raise SystemExit(f"Refusing: {len(leak)} labelled video(s) are in the test set, e.g. {leak[0]}")
    normals = sorted(str(p.resolve()) for p in Path(a.videos).rglob("Normal_Videos*")
                     if p.suffix.lower() in VID and p.name not in test)
    rng = random.Random(a.seed)
    rng.shuffle(normals)
    normals = normals[: a.normals]
    if len(crashes) < 20:
        print(f"Warning: only {len(crashes)} labelled crashes; 60+ gives a much better model.")

    out = Path(a.out)
    (out / "clips").mkdir(parents=True, exist_ok=True)
    items = []
    for r in crashes:
        cs, ce = float(r["accident_start_s"]), float(r["accident_end_s"])
        lo = max(0.0, cs - a.context)
        items.append(("crash", r["video"], lo, ce + a.context, cs - lo, ce - lo))
    for v in normals:
        items.append(("normal", v, 0.0, a.normal_seconds, None, None))
    for kind in ("crash", "normal"):  # validation split by video, per class
        idx = [i for i, it in enumerate(items) if it[0] == kind]
        rng.shuffle(idx)
        for i in idx[: max(1, int(round(len(idx) * a.val_fraction)))]:
            items[i] = items[i] + ("val",)
    rows = []
    for n, it in enumerate(items, 1):
        kind, src, lo, hi, cs, ce = it[:6]
        split = it[6] if len(it) > 6 else "train"
        frames = ac.resample_video(src, lo, hi)
        if len(frames) < ac.CLIP_LEN:
            print(f"skipped (too short / unreadable): {src}")
            continue
        name = f"{kind}_{n:04d}_{Path(src).stem}.mp4"
        write_mp4(frames, out / "clips" / name)
        rows.append({"file": f"clips/{name}", "kind": kind, "split": split, "frames": len(frames),
                     "crash_start_s": "" if cs is None else f"{cs:.2f}", "crash_end_s": "" if ce is None else f"{ce:.2f}",
                     "source": Path(src).name})
        print(f"[{n}/{len(items)}] {name} ({len(frames)} frames, {split})", flush=True)
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    shutil.copy(ROOT / "detection" / "accident_classifier.py", out / "accident_classifier.py")
    shutil.copy(ROOT / "training" / "train_accident_model.py", out / "train_accident_model.py")
    z = shutil.make_archive(str(out), "zip", out)
    c = sum(r["kind"] == "crash" for r in rows)
    print(f"done: {c} crash + {len(rows) - c} normal clips -> {z}")


if __name__ == "__main__":
    main()
