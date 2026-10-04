"""Dispersion and significance, exactly as fixed in paper Sec. 5.5:
mean +/- sample s.d.; paired two-sided t-test when the paired differences pass
Shapiro-Wilk (alpha=0.05), Wilcoxon signed-rank otherwise; Holm-Bonferroni
correction across the family of comparisons; exact p-values reported."""
from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Sequence

import numpy as np


def mean_sd(x: Sequence[float]) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return float("nan"), float("nan")
    return float(x.mean()), float(x.std(ddof=1)) if len(x) > 1 else float("nan")


def paired_test(a: Sequence[float], b: Sequence[float]) -> Dict[str, float | str]:
    from scipy import stats

    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    n = len(d)
    if n < 3:
        return {"test": "n<3 (no test)", "p": float("nan"), "n": n}
    if np.allclose(d, 0):
        return {"test": "identical", "p": 1.0, "n": n, "mean_diff": 0.0}
    sw_p = float(stats.shapiro(d).pvalue) if n >= 3 else float("nan")
    if sw_p >= 0.05:
        p = float(stats.ttest_rel(a, b).pvalue)
        test = "paired t-test"
    else:
        p = float(stats.wilcoxon(a, b, zero_method="wilcox", alternative="two-sided").pvalue)
        test = "Wilcoxon signed-rank"
    return {"test": test, "p": p, "shapiro_p": sw_p, "n": n, "mean_diff": float(d.mean())}


def holm(pvals: List[float]) -> List[float]:
    p = np.asarray(pvals, float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        val = min(1.0, (m - rank) * p[idx])
        running = max(running, val)
        adj[idx] = running
    return adj.tolist()


def compare_all(per_seq: Dict[str, Dict[str, float]], metric: str) -> List[dict]:
    """per_seq[tracker][sequence] -> value. Pairs are matched by sequence name."""
    trackers = list(per_seq)
    rows = []
    for t1, t2 in combinations(trackers, 2):
        seqs = sorted(set(per_seq[t1]) & set(per_seq[t2]))
        r = paired_test([per_seq[t1][s] for s in seqs], [per_seq[t2][s] for s in seqs])
        r.update({"metric": metric, "A": t1, "B": t2})
        rows.append(r)
    ps = [r["p"] for r in rows]
    finite = [i for i, p in enumerate(ps) if np.isfinite(p)]
    if finite:
        adj = holm([ps[i] for i in finite])
        for i, a in zip(finite, adj):
            rows[i]["p_holm"] = a
    return rows
