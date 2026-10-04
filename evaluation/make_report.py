"""Collect every results JSON into evaluation/results/RESULTS.md -- one file with
the numbers that replace the red "not measured / not run" cells (paper Tables 3-5,
Sec. 6.4, 6.6). Missing experiments are listed as still missing, never estimated.

Usage:  python -m evaluation.make_report
"""
from __future__ import annotations

import json
from pathlib import Path

from evaluation.common import REPO_ROOT

R = REPO_ROOT / "evaluation" / "results"


def load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def pct(v):
    return "n/a" if v is None else f"{100*v:.2f}%"


def main() -> None:
    L = ["# DriveShieldX -- measured results", "",
         "Every number below was produced by the scripts in `evaluation/` on the data named next to it.", ""]

    t = load(R / "tracking" / "summary.json")
    L += ["## Table 4 -- Multi-object tracking (UA-DETRAC)", ""]
    if t:
        L += [f"Sequences ({len(t['sequences'])}): {', '.join(t['sequences'])}  ",
              f"Machine: {t['env'].get('processor') or t['env'].get('platform')}, torch {t['env'].get('torch')}, "
              f"cuda={t['env'].get('cuda')}", "",
              "| Tracker | MOTA | IDF1 | HOTA | IDSW | Assoc. FPS | IDF1 mean±sd (per seq) |", "|---|---|---|---|---|---|---|"]
        for k, v in t["trackers"].items():
            c, d = v["combined"], v["per_sequence_mean_sd"]
            L.append(f"| {k} | {c['MOTA']:.1f} | {c['IDF1']:.1f} | {c['HOTA']:.1f} | {c['IDSW']} | {c['assoc_fps']:.0f} | "
                     f"{d['IDF1']['mean']:.1f} ± {d['IDF1']['sd']:.1f} |")
        L += ["", "Paired tests (Holm-corrected):", ""]
        for s in t["significance"]:
            L.append(f"- {s['metric']}: {s['A']} vs {s['B']} -- {s['test']}, mean diff {s.get('mean_diff', float('nan')):.2f}, "
                     f"p={s['p']:.4g}, p_Holm={s.get('p_holm', float('nan')):.4g}")
        L += ["", "Speed from trajectories on UA-DETRAC (same estimator & s_px for tracker and GT):", ""]
        for k, v in t["trackers"].items():
            s = v["speed"]
            if s["MAE_vs_gt_traj_kmh"] is not None:
                L.append(f"- {k}: MAE vs GT trajectory {s['MAE_vs_gt_traj_kmh']:.2f} km/h; >200 km/h {pct(s['frac_est_gt_200'])}; "
                         f"max {s['max_est_kmh']:.1f} km/h")
    else:
        L += ["**Still missing** -- run cache_detections + run_tracking_eval.", ""]

    g = load(R / "gate" / "gate_summary.json")
    L += ["", "## Disagreement gate", ""]
    if g:
        L += [f"- Two-wheeler crops evaluated: {g['two_wheeler_crops_evaluated']} (sources: {', '.join(map(str, g['sources']))})",
              f"- agree-positive {g['agree_positive']}, disagree {g['disagree']}, agree-negative {g['agree_negative']}",
              f"- **Withheld rate** (disagree / OR-positive): {pct(g['withheld_rate'])}"]
    else:
        L += ["**Still missing** -- run gate_eval measure."]
    fi = load(R / "gate" / "gate_false_issuance.json")
    if fi:
        L += [f"- **False-issuance rate with gate**: {pct(fi['with_gate']['false_issuance_rate'])} "
              f"({fi['with_gate']['unsupported']}/{fi['with_gate']['issued']})",
              f"- False-issuance rate without gate (OR rule, ablation): {pct(fi['without_gate_OR_rule']['false_issuance_rate'])} "
              f"({fi['without_gate_OR_rule']['unsupported']}/{fi['without_gate_OR_rule']['issued']})",
              f"- Withheld events that were real violations: {fi['withheld_that_were_real_violations']}/{fi['withheld_events']}"]
    else:
        L += ["- False-issuance rate: **still missing** -- fill human_label in review_sheet.csv, run gate_eval score."]

    a = load(REPO_ROOT / "evaluation" / "anpr_bench" / "anpr_results.json")
    L += ["", "## Table 5 -- ANPR recognition", ""]
    if a:
        ts, lg = a["two_stage_yolo_easyocr"], a["legacy_cascade_easyocr"]
        L += [f"- Labelled readable plates: {a['labelled_plates']}; YOLOv8 localisation recall on them: "
              f"{pct(a['localisation_recall_on_readable_plates'])}",
              f"- Two-stage (YOLOv8 plate + EasyOCR): exact match {pct(ts['exact_match_accuracy'])}, CER {ts['CER']:.3f}, "
              f"no-read {pct(ts['no_read_rate'])}",
              f"- Legacy (cascade + EasyOCR): exact match {pct(lg['exact_match_accuracy'])}, CER {lg['CER']:.3f}"]
    else:
        L += ["**Still missing** -- anpr_benchmark make-sheet, label, score."]

    pc = load(R / "perclass" / "perclass.json")
    L += ["", "## Table 3 -- per-class detector metrics", ""]
    if pc:
        L += ["| Model | Class | P | R | mAP50 | mAP50-95 |", "|---|---|---|---|---|---|"]
        for r in pc["rows"]:
            L.append(f"| {r['model']} | {r['class']} | {r['precision']:.3f} | {r['recall']:.3f} | {r['mAP50']:.3f} | {r['mAP50-95']:.3f} |")
    else:
        L += ["**Still missing** -- needs the validation splits (perclass_val on Kaggle)."]
    for p in sorted((R / "seed_sweep").glob("*/seed_sweep.json")) if (R / "seed_sweep").exists() else []:
        s = load(p)
        L += [f"- Seed sweep {Path(s['weights']).name} ({len(s['runs'])} seeds): " +
              ", ".join(f"{k} {v['mean']:.3f} ± {v['sd']:.3f}" for k, v in s["summary"].items())]

    L += ["", "## Throughput", ""]
    tp = sorted((R / "throughput").glob("*.json")) if (R / "throughput").exists() else []
    for p in tp:
        s = load(p)
        L.append(f"- {Path(s['video']).name} on {s['device']}{' + OCR' if s['npr'] else ''}: {s['fps_mean']:.2f} FPS "
                 f"({s['ms_per_frame_mean']:.0f} ms/frame, p95 {s['ms_per_frame_p95']:.0f} ms)")
    if not tp:
        L.append("**Still missing** -- run evaluation.throughput.")

    v = load(REPO_ROOT / "evaluation" / "violation_bench" / "violation_results.json")
    L += ["", "## Violation-level accuracy (legacy vs current decision rules, single frame)", ""]
    if v:
        L += ["| Rule | Rules | n | Precision | Recall | F1 |", "|---|---|---|---|---|---|"]
        f = lambda x: "n/a" if x is None else f"{x:.3f}"
        for rule in ("no_helmet", "triple", "no_seatbelt"):
            r = v.get(rule)
            if isinstance(r, dict):
                for which in ("legacy", "current"):
                    m = r[which]
                    L.append(f"| {rule} | {which} | {m['n']} | {f(m['precision'])} | {f(m['recall'])} | {f(m['F1'])} |")
    else:
        L += ["**Still missing** -- violation_eval make-sheet, label, score."]

    fs = load(R / "fog" / "fog_synthetic.json")
    fl = load(R / "fog" / "fog_labelled.json")
    L += ["", "## Fog / visibility", ""]
    if fs:
        L += [f"- Synthetic (scattering model, {fs['frames']} real frames): " +
              "; ".join(f"t={t}: {r['mean_score']:.2f}" for t, r in fs["by_transmission"].items())]
    L += [f"- Real labelled fog frames: accuracy {fl['accuracy']:.3f}, AUC {fl.get('fog_vs_not_auc')}" if fl
          else "- Real labelled fog footage: **still missing** (fog_eval labelled)."]
    ae = load(R / "accident_eval.json")
    L += ["", "## Accident detection", "",
          (f"- {ae['clips']} clips / {ae['hours']:.2f} h: precision {ae['precision']}, recall {ae['recall']}, "
           f"false alarms/h {ae['false_alarms_per_hour']}, mean time-to-detect {ae['mean_time_to_detect_s']} s")
          if ae else "- **Still missing** -- needs labelled crash clips (accident_eval)."]
    pv = load(R / "provenance.json")
    if pv:
        L += ["", "## Training provenance (from the checkpoints)", "",
              "| Model | Base | Epochs | Batch | Optimizer | Seed | Ultralytics |", "|---|---|---|---|---|---|---|"]
        for r in pv:
            L.append(f"| {Path(r['file']).name} | {r['base_model']} | {r['epochs']} | {r['batch']} | {r['optimizer']} | "
                     f"{r['seed']} | {r['ultralytics_version']} |")

    d = load(R / "db_metrics.json")
    if d:
        s = d["speed"]
        L += ["", "## Runtime DB", "", f"- Speed estimates {s['estimates']}, >200 km/h {pct(s['frac_gt_200'])}, "
              f">400 km/h {pct(s['frac_gt_400'])}, max {s['max_kmh']} km/h"]
    out = R / "RESULTS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
