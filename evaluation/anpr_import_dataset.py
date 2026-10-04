"""Build an ANPR benchmark sheet from a public, already-labelled Indian plate dataset.

Our own CCTV-style footage turned out to have no human-readable plates (0/400), so
recognition accuracy could not be measured on it. This script uses a dataset whose
true plate strings were recorded by its authors, so nobody has to type labels:

  Number Plate Identification Dataset (Bapatla Engineering College, Andhra Pradesh)
  1,700 smartphone photos of Indian vehicles + an Excel sheet of the true plate numbers.
  DOI 10.5281/zenodo.13954136, licence CC-BY-4.0 (cite it; images stay out of git).

It writes the same layout `anpr_benchmark score` already reads:
  <out>/vehicles/<image>.jpg     vehicle crop (largest vehicle; whole photo if none found)
  <out>/labels.csv               true_text pre-filled from the dataset's sheet

Note for reporting: these are close-range phone photos, not pole-mounted CCTV, so the
number measures the recognition stage under good conditions only.

Usage:
  python -m evaluation.anpr_import_dataset --images C:\\data\\number_plate --sheet C:\\data\\number_plate\\labels.xlsx
  python -m evaluation.anpr_benchmark score --bench evaluation\\anpr_bench_dataset

Held-out set (photos never used while tuning the reader):
  python -m evaluation.anpr_import_dataset --images ... --sheet ... --max-rows 0 \\
      --exclude-bench evaluation\\anpr_bench_dataset --out evaluation\\anpr_bench_holdout
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path

import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
# Indian registration: state(2 letters) + district(1-2 digits) + series(0-3 letters) + number(1-4 digits);
# Bharat series: 2 digits + BH + 4 digits + 1-2 letters.
PLATE_RE = re.compile(r"^([A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}|\d{2}BH\d{4}[A-Z]{1,2})$")
SOURCE_NAME = "Zenodo 10.5281/zenodo.13954136 (CC-BY-4.0)"


def norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def read_table(path: Path) -> list[list[str]]:
    """All rows of the label sheet as strings (Excel or CSV), header row included."""
    if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        import pandas as pd
        frames = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
        rows = []
        for df in frames.values():
            rows += df.fillna("").astype(str).values.tolist()
        return rows
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [list(r) for r in csv.reader(f)]


def find_columns(rows: list[list[str]], stems: set[str]) -> tuple[int, int]:
    """Guess which column holds image names and which holds plate numbers, by content."""
    width = max((len(r) for r in rows), default=0)
    img_hits = [0] * width
    plate_hits = [0] * width
    for r in rows:
        for j, v in enumerate(r):
            if Path(str(v).strip()).stem.lower() in stems:
                img_hits[j] += 1
            if PLATE_RE.match(norm(v)):
                plate_hits[j] += 1
    if not width or max(img_hits) == 0:
        raise SystemExit("Could not find a column of image names in the sheet that match the image folder.")
    ic = img_hits.index(max(img_hits))
    plate_hits[ic] = -1
    if max(plate_hits) <= 0:
        raise SystemExit("Could not find a column of plate numbers in the sheet.")
    return ic, plate_hits.index(max(plate_hits))


def pair_labels(images: list[Path], rows: list[list[str]]) -> tuple[list[tuple[Path, str]], dict]:
    by_stem = {p.stem.lower(): p for p in images}
    ic, pc = find_columns(rows, set(by_stem))
    pairs, seen = [], set()
    stats = {"sheet_rows": len(rows), "no_image": 0, "bad_plate_text": 0, "duplicate": 0}
    for r in rows:
        if max(ic, pc) >= len(r):
            continue
        stem = Path(str(r[ic]).strip()).stem.lower()
        if stem not in by_stem:
            stats["no_image"] += 1
            continue
        text = norm(r[pc])
        if not PLATE_RE.match(text):
            stats["bad_plate_text"] += 1
            continue
        if stem in seen:
            stats["duplicate"] += 1
            continue
        seen.add(stem)
        pairs.append((by_stem[stem], text))
    stats["paired"] = len(pairs)
    return pairs, stats


def shrink(img, max_side: int):
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img


def build(pairs, out: Path, detect_vehicle, locate_plate, max_side: int = 1920) -> list[dict]:
    """detect_vehicle(img) -> (x1,y1,x2,y2) of the largest vehicle or None;
    locate_plate(img, box) -> ((x1,y1,x2,y2), conf) or None."""
    vdir = out / "vehicles"
    vdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for path, text in pairs:
        img = cv2.imread(str(path))  # OpenCV applies the EXIF rotation of phone photos
        if img is None:
            continue
        img = shrink(img, max_side)
        h, w = img.shape[:2]
        box = detect_vehicle(img)
        how = "largest vehicle"
        if box is None:
            box, how = (0, 0, w, h), "whole photo (no vehicle box)"
        x1, y1, x2, y2 = (int(v) for v in box)
        veh = img[max(0, y1):y2, max(0, x1):x2]
        if veh.size == 0:
            continue
        name = f"{path.stem}.jpg"
        cv2.imwrite(str(vdir / name), veh)
        loc = locate_plate(img, (x1, y1, x2, y2))
        if loc is not None:
            (px1, py1, px2, py2), pconf = loc
            rel = f"{int(px1) - x1} {int(py1) - y1} {int(px2) - x1} {int(py2) - y1}"
        else:
            rel, pconf = "", 0.0
        rows.append({"vehicle_file": name, "source": f"{SOURCE_NAME}: {path.name}", "frame": 0,
                     "vehicle_box": f"{x1} {y1} {x2} {y2}", "crop_used": how,
                     "plate_box_in_vehicle": rel, "plate_conf": round(float(pconf), 4),
                     "true_text": text, "labeller": "dataset authors"})
    with open(out / "labels.csv", "w", newline="", encoding="utf-8") as f:
        fields = list(rows[0].keys()) if rows else ["vehicle_file", "true_text"]
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(rows)
    return rows


def _deployed_models():
    from ultralytics import YOLO
    from detection.rule_violations import RuleViolationDetector
    from evaluation.anpr_benchmark import VEHICLE_FOR_PLATES
    from evaluation.common import DEPLOYED, model_path

    det = YOLO(model_path(DEPLOYED["vehicle_model"]))
    rv = RuleViolationDetector()

    def detect_vehicle(img):
        r = det.predict(img, conf=DEPLOYED["vehicle_conf"], imgsz=640, classes=VEHICLE_FOR_PLATES, verbose=False)[0]
        boxes = r.boxes.xyxy.cpu().numpy() if len(r.boxes) else []
        if not len(boxes):
            return None
        return max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))

    return detect_vehicle, rv.locate_plate


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True, help="folder with the dataset photos (searched recursively)")
    ap.add_argument("--sheet", required=True, help="the dataset's Excel/CSV of image name + plate number")
    ap.add_argument("--out", default="evaluation/anpr_bench_dataset")
    ap.add_argument("--max-rows", type=int, default=300, help="random sample size (0 = all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--exclude-bench", action="append", default=[],
                    help="skip photos already used in this bench folder (repeatable) -- builds a held-out set")
    a = ap.parse_args()

    images = [p for p in Path(a.images).rglob("*") if p.suffix.lower() in IMG_EXT]
    if not images:
        raise SystemExit(f"No images found under {a.images}")
    pairs, stats = pair_labels(images, read_table(Path(a.sheet)))
    print(f"images found: {len(images)} | sheet: {stats}")
    if not pairs:
        raise SystemExit("No image matched a plate number in the sheet.")
    used = set()
    for b in a.exclude_bench:
        with open(Path(b) / "labels.csv", newline="", encoding="utf-8") as f:
            used |= {Path(r["vehicle_file"]).stem.lower() for r in csv.DictReader(f)}
    if used:
        before = len(pairs)
        pairs = [t for t in pairs if t[0].stem.lower() not in used]
        print(f"held-out: dropped {before - len(pairs)} photos already used in {a.exclude_bench}")
    pairs.sort(key=lambda t: t[0].name)
    random.Random(a.seed).shuffle(pairs)
    if a.max_rows:
        pairs = pairs[: a.max_rows]
    detect_vehicle, locate_plate = _deployed_models()
    rows = build(pairs, Path(a.out), detect_vehicle, locate_plate)
    whole = sum(r["crop_used"].startswith("whole") for r in rows)
    print(f"wrote {len(rows)} rows to {Path(a.out) / 'labels.csv'} "
          f"({whole} used the whole photo because no vehicle was detected).")
    print(f"next: python -m evaluation.anpr_benchmark score --bench {a.out}")


if __name__ == "__main__":
    main()
