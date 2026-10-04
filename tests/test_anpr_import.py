"""The dataset importer pairs photos with the authors' plate numbers and writes a sheet
that `anpr_benchmark score` can read. Detector calls are faked; no models needed."""
import csv

import cv2
import numpy as np
import pandas as pd

from evaluation import anpr_import_dataset as imp


def _photos(tmp_path, names):
    d = tmp_path / "imgs" / "sub"
    d.mkdir(parents=True)
    for n in names:
        cv2.imwrite(str(d / n), np.full((3000, 4000, 3), 120, np.uint8))
    return [p for p in (tmp_path / "imgs").rglob("*.jpg")]


def test_pairs_from_excel_by_content(tmp_path):
    imgs = _photos(tmp_path, ["IMG_001.jpg", "IMG_002.jpg", "IMG_003.jpg"])
    sheet = tmp_path / "labels.xlsx"
    pd.DataFrame({"S.No": [1, 2, 3, 4], "Image": ["IMG_001", "img_002.JPG", "IMG_003", "IMG_999"],
                  "Number Plate": ["AP 07 BX 1234", "ap39cd0001", "not visible", "TS09AB1111"]}
                 ).to_excel(sheet, index=False)
    pairs, stats = imp.pair_labels(imgs, imp.read_table(sheet))
    got = {p.name: t for p, t in pairs}
    assert got == {"IMG_001.jpg": "AP07BX1234", "IMG_002.jpg": "AP39CD0001"}
    assert stats["bad_plate_text"] == 1 and stats["no_image"] >= 1


def test_build_writes_score_ready_sheet(tmp_path):
    imgs = _photos(tmp_path, ["a.jpg", "b.jpg"])
    pairs = [(imgs[0], "AP07BX1234"), (imgs[1], "22BH1234AA")]
    calls = []

    def det(img):
        calls.append(img.shape)
        return (100, 100, 900, 700) if len(calls) == 1 else None

    def loc(img, box):
        return ((box[0] + 10, box[1] + 20, box[0] + 110, box[1] + 60), 0.9)

    out = tmp_path / "bench"
    rows = imp.build(pairs, out, det, loc, max_side=1920)
    assert max(calls[0][:2]) == 1920  # 4000x3000 photo downscaled before detection
    assert rows[0]["plate_box_in_vehicle"] == "10 20 110 60"
    assert rows[1]["crop_used"].startswith("whole")
    back = list(csv.DictReader(open(out / "labels.csv", encoding="utf-8")))
    assert [r["true_text"] for r in back] == ["AP07BX1234", "22BH1234AA"]
    assert all((out / "vehicles" / r["vehicle_file"]).is_file() for r in back)


def test_plate_pattern():
    ok = ["MH02AB1234", "AP7X123", "22BH1234AA", "DL1CAB1234"]
    bad = ["", "UNREADABLE", "1234", "NOTVISIBLE"]
    assert all(imp.PLATE_RE.match(s) for s in ok)
    assert not any(imp.PLATE_RE.match(s) for s in bad)
