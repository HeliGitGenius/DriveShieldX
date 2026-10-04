"""MOTA / IDF1 / HOTA / IDSW scoring with the reference TrackEval implementation
(the code used by the MOTChallenge benchmark), plus a py-motmetrics cross-check.

Definitions (paper Sec. 5.4):
  MOTA = 1 - sum_t(FN_t + FP_t + IDSW_t) / sum_t GT_t               [Bernardin & Stiefelhagen 2008]
  IDF1 = 2 IDTP / (2 IDTP + IDFP + IDFN)                             [Ristani et al. 2016]
  HOTA = sqrt(DetA * AssA), averaged over IoU thresholds 0.05..0.95   [Luiten et al. 2021]
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from evaluation.detrac import ioa, iou_matrix


def filter_ignored(trk: Dict[int, np.ndarray], ignored: np.ndarray, thr: float = 0.5) -> Dict[int, np.ndarray]:
    """Drop tracker boxes that lie mostly (IoA >= thr) inside UA-DETRAC ignored regions."""
    if len(ignored) == 0:
        return trk
    out = {}
    for f, rows in trk.items():
        if len(rows) == 0:
            out[f] = rows
            continue
        keep = ioa(rows[:, 1:5], ignored) < thr
        out[f] = rows[keep]
    return out


def build_trackeval_data(gt: Dict[int, np.ndarray], trk: Dict[int, np.ndarray], num_frames: int) -> dict:
    """Convert per-frame (id,x1,y1,x2,y2) arrays to TrackEval's preprocessed format."""
    gt_ids_all = sorted({int(i) for f in gt.values() for i in f[:, 0]}) if gt else []
    tr_ids_all = sorted({int(i) for f in trk.values() for i in f[:, 0]}) if trk else []
    gmap = {g: k for k, g in enumerate(gt_ids_all)}
    tmap = {t: k for k, t in enumerate(tr_ids_all)}
    data = {"gt_ids": [], "tracker_ids": [], "similarity_scores": [], "num_timesteps": num_frames,
            "num_gt_ids": len(gmap), "num_tracker_ids": len(tmap), "num_gt_dets": 0, "num_tracker_dets": 0,
            "seq": "seq"}
    for f in range(1, num_frames + 1):
        g = gt.get(f, np.zeros((0, 5)))
        t = trk.get(f, np.zeros((0, 5)))
        data["gt_ids"].append(np.array([gmap[int(i)] for i in g[:, 0]], dtype=int))
        data["tracker_ids"].append(np.array([tmap[int(i)] for i in t[:, 0]], dtype=int))
        data["similarity_scores"].append(iou_matrix(g[:, 1:5], t[:, 1:5]))
        data["num_gt_dets"] += len(g)
        data["num_tracker_dets"] += len(t)
    return data


_METRICS = None


def _metrics():
    global _METRICS
    if _METRICS is None:
        import io
        import contextlib

        # TrackEval still uses the numpy aliases removed in numpy>=1.24; restore them.
        for alias, typ in (("float", float), ("int", int), ("bool", bool)):
            if not hasattr(np, alias):
                setattr(np, alias, typ)
        with contextlib.redirect_stdout(io.StringIO()):
            from trackeval.metrics import CLEAR, HOTA, Identity
        _METRICS = {"HOTA": HOTA(), "CLEAR": CLEAR({"THRESHOLD": 0.5, "PRINT_CONFIG": False}),
                    "Identity": Identity({"THRESHOLD": 0.5, "PRINT_CONFIG": False})}
    return _METRICS


def eval_sequence(gt: Dict[int, np.ndarray], trk: Dict[int, np.ndarray], num_frames: int) -> dict:
    data = build_trackeval_data(gt, trk, num_frames)
    return {k: m.eval_sequence(data) for k, m in _metrics().items()}


def combine(per_seq: Dict[str, dict]) -> dict:
    out = {}
    for k, m in _metrics().items():
        out[k] = m.combine_sequences({s: r[k] for s, r in per_seq.items()})
    return out


def summarize(res: dict) -> Dict[str, float]:
    """Flatten the headline numbers (percent scale like the MOT benchmarks)."""
    h, c, i = res["HOTA"], res["CLEAR"], res["Identity"]
    return {
        "MOTA": 100 * float(c["MOTA"]),
        "IDF1": 100 * float(i["IDF1"]),
        "HOTA": 100 * float(np.mean(h["HOTA"])),
        "DetA": 100 * float(np.mean(h["DetA"])),
        "AssA": 100 * float(np.mean(h["AssA"])),
        "IDSW": int(c["IDSW"]),
        "MOTP": 100 * float(c["MOTP"]),
        "FP": int(c["CLR_FP"]),
        "FN": int(c["CLR_FN"]),
        "Frag": int(c["Frag"]),
        "MT": int(c["MT"]),
        "ML": int(c["ML"]),
        "GT_dets": int(c["CLR_TP"] + c["CLR_FN"]),
    }


def motmetrics_crosscheck(gt: Dict[int, np.ndarray], trk: Dict[int, np.ndarray], num_frames: int) -> Dict[str, float]:
    """Independent implementation (py-motmetrics) of MOTA/IDF1/IDSW for sanity checking."""
    import motmetrics as mm

    acc = mm.MOTAccumulator(auto_id=False)
    for f in range(1, num_frames + 1):
        g = gt.get(f, np.zeros((0, 5)))
        t = trk.get(f, np.zeros((0, 5)))
        dist = 1.0 - iou_matrix(g[:, 1:5], t[:, 1:5])
        dist[dist > 0.5] = np.nan
        acc.update([int(x) for x in g[:, 0]], [int(x) for x in t[:, 0]], dist, frameid=f)
    mh = mm.metrics.create()
    s = mh.compute(acc, metrics=["mota", "idf1", "num_switches"], name="x")
    return {"MOTA": 100 * float(s["mota"].iloc[0]), "IDF1": 100 * float(s["idf1"].iloc[0]),
            "IDSW": int(s["num_switches"].iloc[0])}
