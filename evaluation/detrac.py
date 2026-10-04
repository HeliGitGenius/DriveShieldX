"""UA-DETRAC loader (XML annotations + image folders) and a generic MOT gt.txt loader.

UA-DETRAC layout (as on the project laptop, C:\\datasets):
    DETRAC-Images/MVI_20011/img00001.jpg ...
    DETRAC-Train-Annotations-XML/MVI_20011.xml
    DETRAC-Test-Annotations-XML/MVI_39031.xml

Each <frame num=".."> holds <target id=".."><box left top width height/> ...</target>.
<ignored_region> boxes mark areas that are not annotated; following the DETRAC
protocol, tracker boxes lying mostly inside them are discarded before scoring.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class Sequence_:
    name: str
    img_dir: Path
    num_frames: int
    # frame -> array (K,5): id, x1, y1, x2, y2
    gt: Dict[int, np.ndarray] = field(default_factory=dict)
    ignored: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))  # x1,y1,x2,y2
    fps: float = 25.0

    def frame_path(self, n: int) -> Path:
        p = self.img_dir / f"img{n:05d}.jpg"
        if p.exists():
            return p
        # generic MOT layout (000001.jpg)
        return self.img_dir / f"{n:06d}.jpg"


def parse_detrac_xml(xml_path: Path | str) -> Tuple[Dict[int, np.ndarray], np.ndarray, int]:
    root = ET.parse(str(xml_path)).getroot()
    ignored = []
    for reg in root.iter("ignored_region"):
        for b in reg.iter("box"):
            l, t = float(b.get("left")), float(b.get("top"))
            w, h = float(b.get("width")), float(b.get("height"))
            ignored.append((l, t, l + w, t + h))
    gt: Dict[int, np.ndarray] = {}
    max_frame = 0
    for fr in root.iter("frame"):
        n = int(fr.get("num"))
        max_frame = max(max_frame, n)
        rows = []
        for tg in fr.iter("target"):
            b = tg.find("box")
            if b is None:
                continue
            l, t = float(b.get("left")), float(b.get("top"))
            w, h = float(b.get("width")), float(b.get("height"))
            rows.append((int(tg.get("id")), l, t, l + w, t + h))
        gt[n] = np.asarray(rows, dtype=float).reshape(-1, 5)
    return gt, np.asarray(ignored, dtype=float).reshape(-1, 4), max_frame


def parse_mot_gt(gt_txt: Path | str) -> Dict[int, np.ndarray]:
    """MOTChallenge gt.txt: frame,id,x,y,w,h,conf,cls,vis (conf==0 rows ignored)."""
    arr = np.loadtxt(str(gt_txt), delimiter=",", ndmin=2)
    gt: Dict[int, List] = {}
    for r in arr:
        if len(r) > 6 and r[6] == 0:
            continue
        f, i, x, y, w, h = int(r[0]), int(r[1]), *r[2:6]
        gt.setdefault(f, []).append((i, x, y, x + w, y + h))
    return {f: np.asarray(v, dtype=float).reshape(-1, 5) for f, v in gt.items()}


def find_detrac_sequences(images_root: Path | str, ann_roots: Sequence[Path | str]) -> List[Tuple[str, Path, Path]]:
    images_root = Path(images_root)
    found = []
    xml_by_name: Dict[str, Path] = {}
    for ar in ann_roots:
        ar = Path(ar)
        if not ar.exists():
            continue
        for x in ar.rglob("*.xml"):
            xml_by_name[x.stem] = x
    for d in sorted(p for p in images_root.iterdir() if p.is_dir()):
        if d.name in xml_by_name:
            found.append((d.name, d, xml_by_name[d.name]))
    return found


def pick_spread(names: List[str], k: int) -> List[str]:
    """Deterministic, results-independent pick of k sequences spread over the sorted list."""
    names = sorted(names)
    if k >= len(names):
        return names
    idx = np.linspace(0, len(names) - 1, k).round().astype(int)
    return [names[i] for i in idx]


def load_detrac_sequence(name: str, img_dir: Path, xml_path: Path) -> Sequence_:
    gt, ignored, max_frame = parse_detrac_xml(xml_path)
    n_imgs = len(list(Path(img_dir).glob("*.jpg")))
    num = max(max_frame, n_imgs)
    return Sequence_(name=name, img_dir=Path(img_dir), num_frames=num, gt=gt, ignored=ignored, fps=25.0)


def ioa(boxes: np.ndarray, regions: np.ndarray) -> np.ndarray:
    """Intersection over box-area of each box with the union-approx (max over regions)."""
    if len(boxes) == 0 or len(regions) == 0:
        return np.zeros(len(boxes))
    x1 = np.maximum(boxes[:, None, 0], regions[None, :, 0])
    y1 = np.maximum(boxes[:, None, 1], regions[None, :, 1])
    x2 = np.minimum(boxes[:, None, 2], regions[None, :, 2])
    y2 = np.minimum(boxes[:, None, 3], regions[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area = np.clip(boxes[:, 2] - boxes[:, 0], 1e-9, None) * np.clip(boxes[:, 3] - boxes[:, 1], 1e-9, None)
    return (inter / area[:, None]).max(axis=1)


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    bb = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.clip(aa[:, None] + bb[None, :] - inter, 1e-9, None)
