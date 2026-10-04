"""Labelling tool for the two human ground-truth sheets.

    streamlit run evaluation/label_tool.py

- Gate sheet  (evaluation/results/gate/review_sheet.csv): look at the rider crop and answer
  "is the rider (or pillion) without a helmet?"  -> violation / no_violation / unclear
- Plate sheet (evaluation/anpr_bench/labels.csv): read the number plate in the vehicle crop
  and type it exactly (letters and digits only), or mark it UNREADABLE.

The model's own scores and verdicts are deliberately NOT shown, so the labels are an
independent ground truth. Each labeller gets every 3rd row (split by row number), progress is
saved to the CSV after every answer, and nothing else in the project is touched.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
from pathlib import Path

import cv2
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SHEETS = {
    "Gate sheet (helmet: violation / no violation)": {
        "csv": ROOT / "evaluation/results/gate/review_sheet.csv", "img_dir": ROOT / "evaluation/results/gate/crops",
        "img_col": "crop", "label_col": "human_label"},
    "Plate sheet (type the number plate)": {
        "csv": ROOT / "evaluation/anpr_bench/labels.csv", "img_dir": ROOT / "evaluation/anpr_bench/vehicles",
        "img_col": "vehicle_file", "label_col": "true_text"},
}
TEAM = ["Heli (rows 1, 4, 7, ...)", "Nisha (rows 2, 5, 8, ...)", "Tanisha (rows 3, 6, 9, ...)", "Anyone (all unlabelled)"]


def load(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        rows, fields = list(r), list(r.fieldnames or [])
    if "labeller" not in fields:
        fields.append("labeller")
        for row in rows:
            row["labeller"] = ""
    return rows, fields


def save(path: Path, rows, fields) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)          # atomic: a crash never leaves a half-written sheet


def main() -> None:
    st.set_page_config(page_title="DriveShieldX labelling", layout="wide")
    st.title("DriveShieldX · ground-truth labelling")
    sheet_name = st.sidebar.radio("Sheet", list(SHEETS))
    cfg = SHEETS[sheet_name]
    who = st.sidebar.radio("I am", TEAM)
    if not cfg["csv"].is_file():
        st.error(f"{cfg['csv']} not found. Run the evaluation first.")
        return
    rows, fields = load(cfg["csv"])
    k = TEAM.index(who)
    mine = [i for i in range(len(rows)) if k == 3 or i % 3 == k]
    todo = [i for i in mine if not rows[i][cfg["label_col"]].strip()]
    done_all = sum(bool(r[cfg["label_col"]].strip()) for r in rows)
    st.sidebar.metric("My rows left", f"{len(todo)} / {len(mine)}")
    st.sidebar.progress(done_all / max(1, len(rows)), text=f"Whole sheet: {done_all}/{len(rows)} labelled")
    if not todo:
        st.success("All your rows are labelled. Thank you!")
        return
    i = todo[0]
    row = rows[i]
    img_path = cfg["img_dir"] / row[cfg["img_col"]]
    left, right = st.columns([3, 2])
    with left:
        img = cv2.imread(str(img_path))
        if img is None:
            st.warning(f"Image missing: {img_path.name}. Mark it unclear / UNREADABLE.")
        else:
            if cfg["label_col"] == "true_text" and row.get("plate_box_in_vehicle", "").strip():
                x1, y1, x2, y2 = (int(float(v)) for v in row["plate_box_in_vehicle"].split())
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 255), 2)
                plate = img[max(0, y1):y2, max(0, x1):x2]
                if plate.size:
                    st.image(cv2.cvtColor(cv2.resize(plate, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC),
                                          cv2.COLOR_BGR2RGB), caption="Plate (enlarged 3x)")
            st.image(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), caption=f"Row {i + 1} · {img_path.name}",
                     use_container_width=True)
    with right:
        st.caption(f"Row {i + 1} of {len(rows)}")
        if cfg["label_col"] == "human_label":
            st.markdown("**Is the rider or pillion riding WITHOUT a helmet?**\n\n"
                        "- **Violation**: at least one person on this two-wheeler has no helmet\n"
                        "- **No violation**: everyone on it wears a helmet\n"
                        "- **Unclear**: you cannot tell (blur, cut off, not a two-wheeler)")
            c1, c2, c3 = st.columns(3)
            answer = None
            if c1.button("Violation", use_container_width=True, key=f"v{i}"):
                answer = "violation"
            if c2.button("No violation", use_container_width=True, key=f"n{i}"):
                answer = "no_violation"
            if c3.button("Unclear", use_container_width=True, key=f"u{i}"):
                answer = "unclear"
        else:
            st.markdown("**Type the number plate exactly as printed** (letters and digits only, e.g. "
                        "`MH12AB1234`). If you cannot read every character with confidence, press **Unreadable**.")
            text = st.text_input("Plate", key=f"t{i}").strip().upper()
            clean = re.sub(r"[^A-Z0-9]", "", text)
            c1, c2 = st.columns(2)
            answer = None
            if c1.button("Save plate", use_container_width=True, disabled=len(clean) < 4, key=f"s{i}"):
                answer = clean
            if c2.button("Unreadable", use_container_width=True, key=f"x{i}"):
                answer = "UNREADABLE"
        if answer:
            rows[i][cfg["label_col"]] = answer
            rows[i]["labeller"] = who.split(" ")[0]
            save(cfg["csv"], rows, fields)
            st.rerun()
        st.divider()
        st.caption("Rule: label only what you can see. Never guess, never look at the model's output. "
                   "Your answer is saved immediately; you can close this tab and continue later.")


if __name__ == "__main__":   # `streamlit run` executes the file as __main__
    main()
