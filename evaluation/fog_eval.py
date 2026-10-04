"""Fog detector evaluation.

synthetic : apply the atmospheric scattering model I = J t + A (1 - t) to real
            clear frames at known transmissions and report the score/level for
            each t (a controlled sanity check -- NOT a substitute for real fog).
labelled  : folder with sub-folders clear/, light_haze/, moderate_fog/,
            dense_fog/ (any subset) of REAL frames from ONE camera plus a clear
            baseline frame -> accuracy, per-level recall, binary fog-vs-not ROC AUC
            and the best score threshold.

  python -m evaluation.fog_eval synthetic --frames snapshots --out evaluation/results/fog
  python -m evaluation.fog_eval labelled --root path\\to\\fog_labels --baseline clear_frame.jpg

clips     : folder with fog/ and clear/ sub-folders of REAL videos or images from
            DIFFERENT cameras (e.g. free stock footage). No baseline exists across
            cameras, so the absolute score is used. Samples one frame every
            --every seconds and reports, per clip and per frame: ROC AUC of fog vs
            clear, accuracy of the deployed levels (moderate/dense = fog) and the
            best threshold.

  python -m evaluation.fog_eval clips --root C:\\data\\fog
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from evaluation.common import ensure_dir, env_info, write_json
from detection.fog_monitor import LEVELS, add_synthetic_fog, measure

EXT = {".jpg", ".jpeg", ".png"}


def synthetic(a) -> None:
    files = sorted(p for p in Path(a.frames).rglob("*") if p.suffix.lower() in EXT)[: a.max_frames]
    ts = [1.0, 0.85, 0.7, 0.5, 0.35, 0.2]
    table = {t: [] for t in ts}
    levels = {t: {} for t in ts}
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        m0 = measure(img)
        base = {k: m0[k] for k in ("dark_channel", "contrast", "edge_density")}
        for t in ts:
            m = measure(add_synthetic_fog(img, t) if t < 1 else img, baseline=base)
            table[t].append(m["fog_score"])
            levels[t][m["level"]] = levels[t].get(m["level"], 0) + 1
    res = {"env": env_info(), "frames": len(files),
           "by_transmission": {str(t): {"mean_score": float(np.mean(v)), "sd": float(np.std(v, ddof=1)) if len(v) > 1 else None,
                                        "levels": levels[t]} for t, v in table.items()}}
    out = ensure_dir(a.out)
    write_json(out / "fog_synthetic.json", res)
    for t in ts:
        r = res["by_transmission"][str(t)]
        print(f"t={t:<4} score {r['mean_score']:.3f} ± {r['sd'] or 0:.3f}  levels {r['levels']}")


def labelled(a) -> None:
    from sklearn.metrics import roc_auc_score  # optional dependency

    root = Path(a.root)
    b = measure(cv2.imread(a.baseline))
    base = {k: b[k] for k in ("dark_channel", "contrast", "edge_density")}
    y_true, y_pred, scores, foggy = [], [], [], []
    for lvl in LEVELS:
        d = root / lvl
        if not d.exists():
            continue
        for f in sorted(p for p in d.iterdir() if p.suffix.lower() in EXT):
            m = measure(cv2.imread(str(f)), baseline=base)
            y_true.append(lvl); y_pred.append(m["level"]); scores.append(m["fog_score"])
            foggy.append(lvl in ("moderate_fog", "dense_fog"))
    acc = float(np.mean([t == p for t, p in zip(y_true, y_pred)]))
    per = {l: float(np.mean([p == l for t, p in zip(y_true, y_pred) if t == l])) for l in set(y_true)}
    res = {"n": len(y_true), "accuracy": acc, "recall_per_level": per}
    if len(set(foggy)) == 2:
        res["fog_vs_not_auc"] = float(roc_auc_score(foggy, scores))
        s = np.asarray(scores); fg = np.asarray(foggy)
        best = max(((float(th), float(((s >= th) == fg).mean())) for th in np.unique(s)), key=lambda x: x[1])
        res["best_threshold"], res["best_threshold_accuracy"] = best
    write_json(Path(a.out) / "fog_labelled.json", res)
    print(res)


VID = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _frames(path: Path, every: float, max_frames: int):
    if path.suffix.lower() in EXT:
        img = cv2.imread(str(path))
        return [img] if img is not None else []
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(every * fps)))
    out, i = [], 0
    while len(out) < max_frames:
        ok, img = cap.read()
        if not ok:
            break
        if i % step == 0:
            out.append(img)
        i += 1
    cap.release()
    return out


def _auc_and_threshold(scores, labels):
    from sklearn.metrics import roc_auc_score
    s, y = np.asarray(scores), np.asarray(labels, bool)
    best = max(((float(th), float(((s >= th) == y).mean())) for th in np.unique(s)), key=lambda x: x[1])
    return float(roc_auc_score(y, s)), best


def clips(a) -> None:
    root = Path(a.root)
    per_clip, f_scores, f_true, f_pred = [], [], [], []
    for cls in ("fog", "clear"):
        d = root / cls
        if not d.exists():
            raise SystemExit(f"Missing folder: {d}")
        for f in sorted(p for p in d.iterdir() if p.suffix.lower() in EXT | VID):
            ms = [measure(img) for img in _frames(f, a.every, a.max_frames)]
            if not ms:
                print(f"skipped (unreadable): {f.name}")
                continue
            sc = [m["fog_score"] for m in ms]
            fog_pred = [m["level"] in ("moderate_fog", "dense_fog") for m in ms]
            per_clip.append({"clip": f.name, "truth": cls, "frames": len(ms), "mean_score": float(np.mean(sc)),
                             "majority_level": max(set(m["level"] for m in ms), key=[m["level"] for m in ms].count),
                             "fog_predicted": float(np.mean(fog_pred)) >= 0.5})
            f_scores += sc; f_true += [cls == "fog"] * len(sc); f_pred += fog_pred
    c_true = [c["truth"] == "fog" for c in per_clip]
    if len(set(c_true)) < 2:
        raise SystemExit("Need at least one fog clip and one clear clip.")
    c_auc, c_best = _auc_and_threshold([c["mean_score"] for c in per_clip], c_true)
    f_auc, f_best = _auc_and_threshold(f_scores, f_true)
    res = {"env": env_info(), "mode": "absolute score, different cameras", "clips_fog": sum(c_true),
           "clips_clear": len(c_true) - sum(c_true), "frames": len(f_scores),
           "clip_level": {"auc": c_auc, "deployed_accuracy": float(np.mean([c["fog_predicted"] == t for c, t in zip(per_clip, c_true)])),
                          "best_threshold": c_best[0], "best_threshold_accuracy": c_best[1]},
           "frame_level": {"auc": f_auc, "deployed_accuracy": float(np.mean([p == t for p, t in zip(f_pred, f_true)])),
                           "best_threshold": f_best[0], "best_threshold_accuracy": f_best[1]},
           "per_clip": per_clip}
    write_json(Path(a.out) / "fog_clips.json", res)
    print({k: v for k, v in res.items() if k != "per_clip"})


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synthetic"); s.add_argument("--frames", required=True); s.add_argument("--max-frames", type=int, default=100)
    s.add_argument("--out", default="evaluation/results/fog")
    l = sub.add_parser("labelled"); l.add_argument("--root", required=True); l.add_argument("--baseline", required=True)
    l.add_argument("--out", default="evaluation/results/fog")
    c = sub.add_parser("clips"); c.add_argument("--root", required=True)
    c.add_argument("--every", type=float, default=2.0, help="seconds between sampled frames")
    c.add_argument("--max-frames", type=int, default=15, help="frames per clip")
    c.add_argument("--out", default="evaluation/results/fog")
    a = ap.parse_args()
    {"synthetic": synthetic, "labelled": labelled, "clips": clips}[a.cmd](a)


if __name__ == "__main__":
    main()
