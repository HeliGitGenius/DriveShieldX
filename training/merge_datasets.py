"""Merge several YOLO-format datasets into one, remapping class names onto a
single target list and dropping exact-duplicate images (how the merged_helmet /
merged_seatbelt / tw_merged sets were assembled on Kaggle).

Usage:
  python -m training.merge_datasets --out data/merged_helmet --classes helmet no_helmet ^
      --src C:\\data\\helmet_A\\data.yaml C:\\data\\helmet_B\\data.yaml ^
      --map "With Helmet=helmet" "Without Helmet=no_helmet" "head=no_helmet"
Classes that are not mapped and not already in --classes are dropped.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

import yaml

SPLITS = ("train", "val", "test")


def load_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def split_dirs(cfg: dict, root: Path, split: str):
    rel = cfg.get(split) or (cfg.get("valid") if split == "val" else None)
    if not rel:
        return None, None
    img = (root / cfg.get("path", ".") / rel) if not Path(rel).is_absolute() else Path(rel)
    img = img.resolve()
    lab = Path(str(img).replace(f"{Path('images')}", "labels")) if "images" in str(img) else img.parent / "labels"
    return img, lab


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", nargs="+", required=True, help="data.yaml of each source dataset")
    ap.add_argument("--out", required=True)
    ap.add_argument("--classes", nargs="+", required=True)
    ap.add_argument("--map", nargs="*", default=[], help='"source name=target name"')
    a = ap.parse_args()
    target = {n: i for i, n in enumerate(a.classes)}
    mapping = {k.strip().lower(): v.strip() for k, v in (m.split("=", 1) for m in a.map)}
    out = Path(a.out)
    seen = set()
    stats = {"images": 0, "duplicates": 0, "boxes": 0, "dropped_boxes": 0}
    for s_idx, src in enumerate(a.src):
        src = Path(src)
        cfg = load_yaml(src)
        names = cfg["names"] if isinstance(cfg["names"], list) else [cfg["names"][k] for k in sorted(cfg["names"])]
        remap = {}
        for i, n in enumerate(names):
            t = mapping.get(n.strip().lower(), n if n in target else None)
            if t in target:
                remap[i] = target[t]
        for split in SPLITS:
            img_dir, lab_dir = split_dirs(cfg, src.parent, split)
            if not img_dir or not img_dir.exists():
                continue
            (out / "images" / split).mkdir(parents=True, exist_ok=True)
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
            for img in img_dir.iterdir():
                if img.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp"):
                    continue
                h = hashlib.md5(img.read_bytes()).hexdigest()
                if h in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(h)
                lines = []
                lab = lab_dir / f"{img.stem}.txt"
                if lab.exists():
                    for ln in lab.read_text().splitlines():
                        parts = ln.split()
                        if not parts:
                            continue
                        c = int(float(parts[0]))
                        if c in remap:
                            lines.append(" ".join([str(remap[c])] + parts[1:]))
                            stats["boxes"] += 1
                        else:
                            stats["dropped_boxes"] += 1
                stem = f"s{s_idx}_{img.stem}"
                shutil.copy(img, out / "images" / split / f"{stem}{img.suffix.lower()}")
                (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines))
                stats["images"] += 1
    (out / "data.yaml").write_text(yaml.safe_dump({"path": str(out.resolve()), "train": "images/train",
                                                   "val": "images/val", "test": "images/test",
                                                   "names": {i: n for n, i in target.items()}}), encoding="utf-8")
    print(stats, "->", out / "data.yaml")


if __name__ == "__main__":
    main()
