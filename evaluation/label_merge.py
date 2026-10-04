"""Merge sheets labelled on different laptops into one.

    python -m evaluation.label_merge evaluation/anpr_bench/labels.csv  nisha_labels.csv tanisha_labels.csv
    python -m evaluation.label_merge evaluation/results/gate/review_sheet.csv  nisha_review.csv tanisha_review.csv

Rows are matched by their image file name; an empty label in the first (main) sheet is filled
from the others. Conflicting non-empty labels are reported, never overwritten.
"""
from __future__ import annotations

import sys
from pathlib import Path

from evaluation.label_tool import load, save

KEYS = {"true_text": "vehicle_file", "human_label": "crop"}


def merge(main_path: Path, others) -> None:
    rows, fields = load(main_path)
    label = "true_text" if "true_text" in fields else "human_label"
    key = KEYS[label]
    index = {r[key]: r for r in rows}
    filled = conflicts = 0
    for o in others:
        orow, _ = load(Path(o))
        for r in orow:
            tgt = index.get(r[key])
            if tgt is None or not r[label].strip():
                continue
            if not tgt[label].strip():
                tgt[label], tgt["labeller"] = r[label], r.get("labeller", "")
                filled += 1
            elif tgt[label].strip().upper() != r[label].strip().upper():
                conflicts += 1
                print(f"CONFLICT {r[key]}: main={tgt[label]!r} other={r[label]!r} (kept main)")
    save(main_path, rows, fields)
    left = sum(not r[label].strip() for r in rows)
    print(f"filled {filled} labels, {conflicts} conflicts, {left} rows still empty")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    merge(Path(sys.argv[1]), sys.argv[2:])
