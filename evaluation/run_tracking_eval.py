"""Step 2 of the tracking protocol: replay the cached detections through every
tracker, score against UA-DETRAC ground-truth identities, and report
MOTA / IDF1 / HOTA / IDSW / association FPS, per sequence and combined, with
mean +/- s.d. across sequences and paired significance tests (paper Sec. 5.5/6.3).

Also produces the speed analysis that the ground truth makes possible:
speed from each tracker's trajectories vs. speed from the matched ground-truth
trajectory, computed with the SAME estimator and scale constant, so the error is
attributable to detection+tracking alone (calibration error is a separate issue).

Usage:
  python -m evaluation.run_tracking_eval --cache evaluation/cache ^
      --images-root C:\\datasets\\DETRAC-Images ^
      --ann-roots C:\\datasets\\DETRAC-Train-Annotations-XML C:\\datasets\\DETRAC-Test-Annotations-XML ^
      --trackers bytetrack centroid deepsort notracker
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np

from evaluation.common import DEPLOYED, ensure_dir, env_info, write_json
from evaluation.detrac import find_detrac_sequences, iou_matrix, load_detrac_sequence
from evaluation.mot_metrics import (combine, eval_sequence, filter_ignored, motmetrics_crosscheck, summarize)
from evaluation.stats import compare_all, mean_sd
from evaluation.trackers import ADAPTERS

IMPLAUSIBLE_KMH = (120.0, 200.0, 400.0)


# ---------------------------------------------------------------------------
# speed helpers (replicates detection/speed_estimator.py exactly)
# ---------------------------------------------------------------------------
def deployed_speed(positions, fps=DEPLOYED["hardcoded_fps"], scale=DEPLOYED["pixel_to_meter"],
                   window=DEPLOYED["speed_window"]):
    if len(positions) < 2:
        return None
    recent = positions[-window:]
    dist = sum(np.hypot(recent[i][0] - recent[i - 1][0], recent[i][1] - recent[i - 1][1]) for i in range(1, len(recent)))
    t = (len(recent) - 1) / fps  # assumes one frame between stored positions (deployed behaviour)
    return dist * scale / t * 3.6 if t > 0 else None


def gap_aware_speed(frames, positions, fps, scale=DEPLOYED["pixel_to_meter"], window=DEPLOYED["speed_window"]):
    if len(positions) < 2:
        return None
    rf, rp = frames[-window:], positions[-window:]
    dist = sum(np.hypot(rp[i][0] - rp[i - 1][0], rp[i][1] - rp[i - 1][1]) for i in range(1, len(rp)))
    t = (rf[-1] - rf[0]) / fps
    return dist * scale / t * 3.6 if t > 0 else None


def speed_analysis(gt: Dict[int, np.ndarray], trk: Dict[int, np.ndarray], num_frames: int, fps: float) -> dict:
    from scipy.optimize import linear_sum_assignment

    t_hist: Dict[int, list] = {}
    g_hist: Dict[int, list] = {}
    errs, errs_gap, est, est_gap, ref = [], [], [], [], []
    for f in range(1, num_frames + 1):
        g = gt.get(f, np.zeros((0, 5)))
        t = trk.get(f, np.zeros((0, 5)))
        for row in g:
            g_hist.setdefault(int(row[0]), []).append((f, (row[1] + row[3]) / 2, (row[2] + row[4]) / 2))
        match = {}
        if len(g) and len(t):
            iou = iou_matrix(t[:, 1:5], g[:, 1:5])
            r, c = linear_sum_assignment(-iou)
            for i, j in zip(r, c):
                if iou[i, j] >= 0.5:
                    match[i] = int(g[j, 0])
        for i, row in enumerate(t):
            tid = int(row[0])
            h = t_hist.setdefault(tid, [])
            h.append((f, (row[1] + row[3]) / 2, (row[2] + row[4]) / 2))
            hist = h[-DEPLOYED["speed_history_n"]:]
            v = deployed_speed([(x, y) for _, x, y in hist])
            vg = gap_aware_speed([fr for fr, _, _ in hist], [(x, y) for _, x, y in hist], fps)
            if v is None:
                continue
            est.append(v)
            if vg is not None:
                est_gap.append(vg)
            if i in match:
                gh = g_hist[match[i]][-DEPLOYED["speed_history_n"]:]
                vr = gap_aware_speed([fr for fr, _, _ in gh], [(x, y) for _, x, y in gh], fps)
                if vr is not None:
                    ref.append(vr)
                    errs.append(abs(v - vr))
                    if vg is not None:
                        errs_gap.append(abs(vg - vr))
    est, est_gap, ref = np.asarray(est), np.asarray(est_gap), np.asarray(ref)
    out = {"n_estimates": int(len(est)), "n_matched": int(len(errs)),
           "MAE_vs_gt_traj_kmh": float(np.mean(errs)) if errs else None,
           "MedAE_vs_gt_traj_kmh": float(np.median(errs)) if errs else None,
           "MAE_gap_aware_kmh": float(np.mean(errs_gap)) if errs_gap else None,
           "max_est_kmh": float(est.max()) if len(est) else None,
           "mean_est_kmh": float(est.mean()) if len(est) else None,
           "mean_gt_traj_kmh": float(ref.mean()) if len(ref) else None}
    for thr in IMPLAUSIBLE_KMH:
        out[f"frac_est_gt_{int(thr)}"] = float((est > thr).mean()) if len(est) else None
        out[f"frac_gt_traj_gt_{int(thr)}"] = float((ref > thr).mean()) if len(ref) else None
    out["_errs"] = errs
    out["_est"] = est.tolist()
    return out


def write_mot(path: Path, trk: Dict[int, np.ndarray]) -> None:
    ensure_dir(path.parent)
    with open(path, "w") as f:
        for fr in sorted(trk):
            for r in trk[fr]:
                f.write(f"{fr},{int(r[0])},{r[1]:.2f},{r[2]:.2f},{r[3]-r[1]:.2f},{r[4]-r[2]:.2f},1,-1,-1,-1\n")


def run_tracker(name: str, dets: np.ndarray, seq, conf: float, shape) -> tuple:
    adapter = ADAPTERS[name]()
    adapter.reset()
    by_frame: Dict[int, np.ndarray] = {}
    for f in range(1, seq.num_frames + 1):
        by_frame[f] = dets[dets[:, 0] == f][:, 1:7]
    out: Dict[int, np.ndarray] = {}
    assoc_time = 0.0
    for f in range(1, seq.num_frames + 1):
        d = by_frame[f]
        d = d[d[:, 4] >= conf]
        frame = None
        if adapter.needs_frame:
            frame = cv2.imread(str(seq.frame_path(f)))  # disk read excluded from timing
        t0 = time.perf_counter()
        out[f] = adapter.update(d, frame, shape)
        assoc_time += time.perf_counter() - t0
    return out, assoc_time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="evaluation/cache")
    ap.add_argument("--images-root", required=True)
    ap.add_argument("--ann-roots", nargs="+", required=True)
    ap.add_argument("--trackers", nargs="+", default=["bytetrack", "centroid", "deepsort", "notracker"])
    ap.add_argument("--conf", type=float, default=DEPLOYED["vehicle_conf"])
    ap.add_argument("--out", default="evaluation/results/tracking")
    a = ap.parse_args()

    cache = Path(a.cache)
    out = ensure_dir(a.out)
    chosen = json.load(open(cache / "sequences.json"))["chosen"]
    lookup = {n: (d, x) for n, d, x in find_detrac_sequences(a.images_root, a.ann_roots)}

    per_seq_res: Dict[str, Dict[str, dict]] = {t: {} for t in a.trackers}
    per_seq_flat: Dict[str, Dict[str, dict]] = {t: {} for t in a.trackers}
    speed: Dict[str, Dict[str, dict]] = {t: {} for t in a.trackers}
    timing: Dict[str, Dict[str, float]] = {t: {"frames": 0, "seconds": 0.0} for t in a.trackers}
    rows_csv: List[dict] = []

    for name in chosen:
        d, x = lookup[name]
        seq = load_detrac_sequence(name, d, x)
        z = np.load(cache / f"{name}.npz")
        dets, shape = z["dets"], tuple(int(v) for v in z["shape"])
        seq.num_frames = int(z["num_frames"])
        gt = {f: v for f, v in seq.gt.items() if f <= seq.num_frames}
        for tname in a.trackers:
            trk, secs = run_tracker(tname, dets, seq, a.conf, shape)
            trk = filter_ignored(trk, seq.ignored)
            label = ADAPTERS[tname].name
            write_mot(out / "mot" / label / f"{name}.txt", trk)
            res = eval_sequence(gt, trk, seq.num_frames)
            flat = summarize(res)
            flat["assoc_fps"] = seq.num_frames / secs if secs > 0 else float("inf")
            check = motmetrics_crosscheck(gt, trk, seq.num_frames)
            flat["check_MOTA_motmetrics"] = check["MOTA"]
            flat["check_IDF1_motmetrics"] = check["IDF1"]
            per_seq_res[tname][name] = res
            per_seq_flat[tname][name] = flat
            sp = speed_analysis(gt, trk, seq.num_frames, seq.fps)
            speed[tname][name] = sp
            timing[tname]["frames"] += seq.num_frames
            timing[tname]["seconds"] += secs
            rows_csv.append({"tracker": label, "sequence": name, **{k: v for k, v in flat.items()},
                             **{k: v for k, v in sp.items() if not k.startswith("_")}})
            print(f"{name:>10} {label:>20}  MOTA {flat['MOTA']:6.2f}  IDF1 {flat['IDF1']:6.2f}  "
                  f"HOTA {flat['HOTA']:6.2f}  IDSW {flat['IDSW']:5d}  assocFPS {flat['assoc_fps']:8.1f}")

    # ---- combined (all sequences pooled, TrackEval combine) + dispersion
    summary = {"env": env_info(), "conf": a.conf, "sequences": chosen, "trackers": {}}
    for tname in a.trackers:
        label = ADAPTERS[tname].name
        comb = summarize(combine(per_seq_res[tname]))
        comb["assoc_fps"] = timing[tname]["frames"] / timing[tname]["seconds"] if timing[tname]["seconds"] else None
        disp = {}
        for m in ("MOTA", "IDF1", "HOTA", "IDSW", "assoc_fps"):
            mu, sd = mean_sd([per_seq_flat[tname][s][m] for s in chosen])
            disp[m] = {"mean": mu, "sd": sd}
        all_err = sum((speed[tname][s]["_errs"] for s in chosen), [])
        all_est = np.asarray(sum((speed[tname][s]["_est"] for s in chosen), []))
        sp = {"MAE_vs_gt_traj_kmh": float(np.mean(all_err)) if all_err else None,
              "n_matched_estimates": len(all_err), "n_estimates": int(len(all_est)),
              "max_est_kmh": float(all_est.max()) if len(all_est) else None}
        for thr in IMPLAUSIBLE_KMH:
            sp[f"frac_est_gt_{int(thr)}"] = float((all_est > thr).mean()) if len(all_est) else None
        summary["trackers"][label] = {"combined": comb, "per_sequence_mean_sd": disp, "speed": sp}

    tests = []
    for m in ("IDF1", "MOTA", "HOTA"):
        tests += compare_all({ADAPTERS[t].name: {s: per_seq_flat[t][s][m] for s in chosen} for t in a.trackers}, m)
    summary["significance"] = tests
    write_json(out / "summary.json", summary)
    with open(out / "per_sequence.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_csv[0].keys()))
        w.writeheader()
        w.writerows(rows_csv)

    # ---- markdown + LaTeX table for the paper / slides
    lines = ["# Tracking evaluation (UA-DETRAC)", "",
             f"Sequences ({len(chosen)}): {', '.join(chosen)}", f"Detector: deployed yolov8n (conf >= {a.conf}); "
             "identical cached detections replayed through every tracker.", "",
             "| Tracker | MOTA | IDF1 | HOTA | IDSW | Assoc. FPS | MOTA mean±sd | IDF1 mean±sd | HOTA mean±sd |",
             "|---|---|---|---|---|---|---|---|---|"]
    tex = [r"\begin{tabular}{lrrrrr}", r"\toprule",
           r"Tracker & MOTA $\uparrow$ & IDF1 $\uparrow$ & HOTA $\uparrow$ & IDSW $\downarrow$ & Assoc. FPS $\uparrow$\\",
           r"\midrule"]
    for label, r in summary["trackers"].items():
        c, d = r["combined"], r["per_sequence_mean_sd"]
        lines.append(f"| {label} | {c['MOTA']:.1f} | {c['IDF1']:.1f} | {c['HOTA']:.1f} | {c['IDSW']} | "
                     f"{c['assoc_fps']:.0f} | {d['MOTA']['mean']:.1f}±{d['MOTA']['sd']:.1f} | "
                     f"{d['IDF1']['mean']:.1f}±{d['IDF1']['sd']:.1f} | {d['HOTA']['mean']:.1f}±{d['HOTA']['sd']:.1f} |")
        tex.append(f"{label} & {c['MOTA']:.1f} & {c['IDF1']:.1f} & {c['HOTA']:.1f} & {c['IDSW']} & {c['assoc_fps']:.0f}\\\\")
    tex += [r"\bottomrule", r"\end{tabular}"]
    lines += ["", "## Paired tests (per-sequence, Holm-corrected)", "",
              "| Metric | A | B | test | mean diff (A-B) | p | p (Holm) |", "|---|---|---|---|---|---|---|"]
    for t in tests:
        lines.append(f"| {t['metric']} | {t['A']} | {t['B']} | {t['test']} | {t.get('mean_diff', float('nan')):.2f} | "
                     f"{t['p']:.4g} | {t.get('p_holm', float('nan')):.4g} |")
    lines += ["", "## Speed from trajectories (same estimator and s_px for tracker and ground truth)", "",
              "| Tracker | MAE vs GT trajectory (km/h) | max estimate | >120 | >200 | >400 |", "|---|---|---|---|---|---|"]
    for label, r in summary["trackers"].items():
        s = r["speed"]
        fmt = lambda v: "n/a" if v is None else f"{100*v:.2f}%"
        mae = "n/a" if s["MAE_vs_gt_traj_kmh"] is None else f"{s['MAE_vs_gt_traj_kmh']:.2f}"
        mx = "n/a" if s["max_est_kmh"] is None else f"{s['max_est_kmh']:.1f}"
        lines.append(f"| {label} | {mae} | {mx} | {fmt(s['frac_est_gt_120'])} | {fmt(s['frac_est_gt_200'])} | {fmt(s['frac_est_gt_400'])} |")
    lines += ["", "```latex", *tex, "```"]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
