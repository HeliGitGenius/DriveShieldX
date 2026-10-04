"""Precision-recall curves from DriveShieldX's own labelled evaluations (paper Fig. 4).

Every curve comes from data with independent ground truth; nothing is simulated.
  (a) helmet violation  : 281 human-reviewed gate events (review_sheet.csv), scored
                          by the standalone model, the combined model and min(p_s, p_c);
                          the OR rule and the deployed gate are marked as operating points
  (b) plate reading     : held-out Indian plate photos; a read is "retrieved" if its
                          confidence clears the threshold and "correct" if it matches exactly
  (c) crash detection   : UCF-Crime held-out clips; event-level precision/recall of the
                          learned model as its alarm threshold is swept; rule baselines marked
  (d) fog               : real fog vs clear clips, frame- and clip-level fog score

A panel whose data is missing is skipped and reported. Writes
evaluation/results/pr_curves.png, .pdf and pr_curves.json (average precision per curve).

  python -m evaluation.pr_curves
  python -m evaluation.pr_curves --skip accident      # the crash panel re-runs the model (~30 min on CPU)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MARKERS = ["o", "s", "^", "D", "v", "P"]
COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#000000", "#9467bd"]


def pr_from_scores(scores, labels):
    """Precision/recall over all thresholds (descending score) + average precision."""
    from sklearn.metrics import average_precision_score, precision_recall_curve
    y = np.asarray(labels, int)
    s = np.asarray(scores, float)
    p, r, _ = precision_recall_curve(y, s)
    return r, p, float(average_precision_score(y, s))


def ap_from_curve(recall, precision):
    """Step-wise AP for a curve given as (recall, precision) points with recall increasing."""
    order = np.argsort(recall)
    r, p = np.asarray(recall)[order], np.asarray(precision)[order]
    ap, prev = 0.0, 0.0
    for ri, pi in zip(r, p):
        ap += (ri - prev) * pi
        prev = ri
    return float(ap)


# ---------------------------------------------------------------- (a) helmet / gate
def panel_helmet(sheet: Path):
    rows = list(csv.DictReader(open(sheet, newline="", encoding="utf-8-sig")))
    rows = [r for r in rows if r.get("human_label", "").strip().lower() in ("violation", "no_violation")]
    if not rows:
        raise FileNotFoundError("no human labels in the review sheet")
    y = [r["human_label"].strip().lower() == "violation" for r in rows]
    ps = [float(r["p_standalone"]) for r in rows]
    pc = [float(r["p_combined"]) for r in rows]
    curves = {"Standalone helmet model": pr_from_scores(ps, y),
              "Combined model": pr_from_scores(pc, y),
              "Both models (min score)": pr_from_scores(np.minimum(ps, pc), y)}
    n_pos = sum(y)
    gate = [r for r, yy in zip(rows, y) if r["gate_verdict"] == "agree_positive"]
    gate_tp = sum(1 for r in gate if r["human_label"].strip().lower() == "violation")
    points = {"OR rule (no gate)": (1.0, n_pos / len(rows)),
              "Deployed gate": (gate_tp / n_pos, gate_tp / len(gate) if gate else 0.0)}
    note = f"{len(rows)} reviewed events; recall within the reviewed set"
    return curves, points, note


# ---------------------------------------------------------------- (b) plates
def panel_plates(bench: Path, engines):
    import cv2
    from detection.npr import NumberPlateRecognizer
    from evaluation.anpr_benchmark import norm
    rows = list(csv.DictReader(open(bench / "labels.csv", newline="", encoding="utf-8-sig")))
    rows = [r for r in rows if norm(r["true_text"]) and r["true_text"].strip().upper() != "UNREADABLE"]
    curves, points = {}, {}
    for eng in engines:
        npr = NumberPlateRecognizer(format_correction=True, engine=eng)
        if eng == "fastplate" and npr.fast is None:
            print("  fast-plate-ocr not installed; skipping that curve")
            continue
        conf, ok = [], []
        for r in rows:
            img = cv2.imread(str(bench / "vehicles" / r["vehicle_file"]))
            text, c = (None, 0.0)
            if img is not None and r.get("plate_box_in_vehicle"):
                box = [int(v) for v in r["plate_box_in_vehicle"].split()]
                text, c = npr.read_plate_crop(img, box)
            conf.append(float(c or 0.0))
            ok.append(norm(text or "") == norm(r["true_text"]))
        order = np.argsort(-np.asarray(conf))
        tp = np.cumsum(np.asarray(ok)[order])
        k = np.arange(1, len(order) + 1)
        prec, rec = tp / k, tp / len(rows)
        label = {"fastplate": "fast-plate-ocr", "easyocr": "EasyOCR"}[eng] + " + format decoding"
        curves[label] = (rec, prec, ap_from_curve(rec, prec))
        points[label + " (accept all)"] = (rec[-1], prec[-1])
    return curves, points, f"{len(rows)} held-out plates; correct = exact string match"


# ---------------------------------------------------------------- (c) accidents
def panel_accident(labels: Path, model: Path, tolerance: float, baselines: dict):
    from collections import defaultdict
    from detection.accident_classifier import AccidentVideoClassifier, events_from_probs, resample_video
    from evaluation.accident_eval import score
    gt, window = defaultdict(list), {}
    for r in csv.DictReader(open(labels, newline="", encoding="utf-8-sig")):
        gt[r["video"]]
        if r.get("clip_start_s") or r.get("clip_end_s"):
            window[r["video"]] = (float(r.get("clip_start_s") or 0), float(r["clip_end_s"]) if r.get("clip_end_s") else None)
        if r.get("accident_start_s"):
            gt[r["video"]].append((float(r["accident_start_s"]), float(r["accident_end_s"] or r["accident_start_s"])))
    clf = AccidentVideoClassifier(str(model))
    scored, durs = {}, {}
    for n, vid in enumerate(gt, 1):
        print(f"  [{n}/{len(gt)}] {Path(vid).name}", flush=True)
        lo, hi = window.get(vid, (0.0, None))
        frames = resample_video(vid, lo, hi)
        scored[vid] = clf.score_frames(frames, lo)
        durs[vid] = len(frames) / 8.0
    rec, prec, ths = [], [], []
    for th in np.round(np.arange(0.05, 1.0, 0.01), 2):
        dets = {v: events_from_probs(p, t, float(th), clf.consecutive, clf.cooldown_s) for v, (t, p) in scored.items()}
        res = score(gt, dets, durs, tolerance)
        if res["TP"] + res["FP"] == 0:
            continue
        rec.append(res["recall"])
        prec.append(res["precision"])
        ths.append(float(th))
    curves = {"Learned R3D-18 (threshold swept)": (np.array(rec), np.array(prec), ap_from_curve(rec, prec))}
    chosen = min(range(len(ths)), key=lambda i: abs(ths[i] - clf.threshold)) if ths else None
    points = dict(baselines)
    if chosen is not None:
        points[f"Deployed threshold {clf.threshold:.2f}"] = (rec[chosen], prec[chosen])
    n_crash = sum(1 for e in gt.values() if e)
    return curves, points, f"{n_crash} crashes + {len(gt) - n_crash} normal clips; event level, +{tolerance:.0f} s tolerance"


def _baseline_points(results_dir: Path):
    pts = {}
    for name, label in (("accident_test_baseline.json", "Track-based rules"), ("accident_test_tuned.json", "Rules, dev-tuned")):
        f = results_dir / name
        if f.exists():
            d = json.loads(f.read_text())
            if d.get("recall") is not None and d.get("precision") is not None:
                pts[label] = (d["recall"], d["precision"])
    return pts


# ---------------------------------------------------------------- (d) fog
def panel_fog(root: Path):
    from detection.fog_monitor import measure
    from evaluation.fog_eval import EXT, VID, _frames
    f_s, f_y, c_s, c_y = [], [], [], []
    for cls in ("fog", "clear"):
        for f in sorted(p for p in (root / cls).iterdir() if p.suffix.lower() in EXT | VID):
            sc = [measure(img)["fog_score"] for img in _frames(f, 2.0, 15)]
            if not sc:
                continue
            f_s += sc
            f_y += [cls == "fog"] * len(sc)
            c_s.append(float(np.mean(sc)))
            c_y.append(cls == "fog")
    if len(set(c_y)) < 2:
        raise FileNotFoundError("need fog/ and clear/ clips")
    curves = {"Frame level": pr_from_scores(f_s, f_y), "Clip level (mean score)": pr_from_scores(c_s, c_y)}
    return curves, {}, f"{sum(c_y)} fog + {len(c_y) - sum(c_y)} clear clips, {len(f_s)} frames"


# ---------------------------------------------------------------- figure
def draw(panels, out_png: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "font.family": "serif"})
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.4))
    for ax, (title, data) in zip(axes.ravel(), panels):
        ax.set_xlim(-0.02, 1.02)
        ax.set_ylim(-0.02, 1.05)
        ax.set_xlabel("Recall")
        ax.set_ylabel("Precision")
        ax.set_title(title, fontsize=8.5, pad=13)
        ax.grid(alpha=0.25, linewidth=0.5)
        if data is None:
            ax.text(0.5, 0.5, "not run", ha="center", va="center", color="#c81e1e", fontweight="bold")
            continue
        curves, points, note = data
        for i, (name, (r, p, ap)) in enumerate(curves.items()):
            r, p = np.asarray(r), np.asarray(p)
            order = np.argsort(r)
            every = max(1, len(r) // 12)
            ax.step(r[order], p[order], where="post", color=COLORS[i % 6], linestyle=["-", "--", "-."][i % 3],
                    linewidth=1.1, marker=MARKERS[i % 6], markersize=3.2, markevery=every,
                    label=f"{name} (AP {ap:.2f})")
        for j, (name, (r, p)) in enumerate(points.items()):
            ax.plot([r], [p], marker=["*", "X", "P", "h"][j % 4], markersize=9, linestyle="none",
                    color=["#c81e1e", "#7f7f7f", "#8c564b", "#17becf"][j % 4], label=f"{name} (R {r:.2f}, P {p:.2f})")
        ax.legend(loc="lower left", fontsize=6.2, frameon=True, framealpha=0.85)
        ax.text(0.5, 1.012, note, transform=ax.transAxes, ha="center", va="bottom", fontsize=6, color="#444444")
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=300)
    fig.savefig(out_png.with_suffix(".pdf"))
    print(f"saved {out_png} and {out_png.with_suffix('.pdf')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate-sheet", default="evaluation/results/gate/review_sheet.csv")
    ap.add_argument("--plate-bench", default="evaluation/anpr_bench_holdout")
    ap.add_argument("--plate-engines", nargs="+", default=["fastplate"], choices=["fastplate", "easyocr"],
                    help="add easyocr for a comparison curve (slow: ~15 min)")
    ap.add_argument("--accident-labels", default=r"C:\data\ucf\accident_labels.csv")
    ap.add_argument("--accident-model", default="models/accident_r3d18.pt")
    ap.add_argument("--fog-root", default=r"C:\data\fog")
    ap.add_argument("--tolerance", type=float, default=10.0)
    ap.add_argument("--skip", nargs="*", default=[], choices=["helmet", "plates", "accident", "fog"])
    ap.add_argument("--out", default="evaluation/results/pr_curves.png")
    a = ap.parse_args()

    jobs = [("helmet", "(a) Helmet violation: reviewed gate events", lambda: panel_helmet(Path(a.gate_sheet))),
            ("plates", "(b) Plate reading: held-out Indian plates", lambda: panel_plates(Path(a.plate_bench), a.plate_engines)),
            ("accident", "(c) Crash detection: UCF-Crime held-out clips",
             lambda: panel_accident(Path(a.accident_labels), Path(a.accident_model), a.tolerance,
                                    _baseline_points(Path(a.out).parent))),
            ("fog", "(d) Fog: real fog vs clear clips", lambda: panel_fog(Path(a.fog_root)))]
    panels, summary = [], {}
    for key, title, fn in jobs:
        data = None
        if key not in a.skip:
            print(f"{title} ...", flush=True)
            try:
                data = fn()
            except (FileNotFoundError, OSError, KeyError) as exc:
                print(f"  skipped: {exc}")
        panels.append((title, data))
        if data:
            curves, points, note = data
            summary[key] = {"note": note, "average_precision": {k: round(v[2], 4) for k, v in curves.items()},
                            "operating_points": {k: {"recall": round(r, 4), "precision": round(p, 4)}
                                                 for k, (r, p) in points.items()}}
    draw(panels, Path(a.out))
    Path(a.out).with_name("pr_curves.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
