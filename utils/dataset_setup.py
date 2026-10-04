"""
UA-DETRAC Dataset Setup Script
Downloads frames from Kaggle mirror and prepares them for the pipeline.

INSTRUCTIONS:
1. Download UA-DETRAC from: https://www.kaggle.com/datasets/bratjay/ua-detrac-orig
   OR: https://detrac-db.rit.albany.edu/
2. Place the zip/extracted folder in: data/
3. Run this script: python utils/dataset_setup.py

The UA-DETRAC dataset structure:
    DETRAC-Train-Images/
        MVI_20011/              ← sequence folders
            img00001.jpg
            img00002.jpg
            ...
        MVI_20012/
        ...

Recommended sequences for highway overspeed detection:
    MVI_20011, MVI_20012, MVI_20032, MVI_20034  → Tianhe, China highway
    MVI_39761, MVI_39771                        → Intersection scenes
"""

import os
import sys
import shutil
import zipfile
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data", "frames")
SNAPSHOTS_DIR = os.path.join(PROJECT_ROOT, "snapshots")


def verify_sequence(seq_path: str) -> int:
    """Count valid frames in a sequence directory."""
    if not os.path.isdir(seq_path):
        return 0
    frames = [f for f in os.listdir(seq_path) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    return len(frames)


def extract_detrac_zip(zip_path: str, target_dir: str):
    """Extract UA-DETRAC zip to target directory."""
    print(f"[Setup] Extracting {zip_path} → {target_dir}")
    os.makedirs(target_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(target_dir)
    print("[Setup] Extraction complete.")


def prepare_sequence(detrac_root: str, sequence: str, out_dir: str, max_frames: int = 500):
    """
    Copy frames from a UA-DETRAC sequence to the project data directory.
    
    Args:
        detrac_root: Path to UA-DETRAC root (containing DETRAC-Train-Images/)
        sequence: Sequence name e.g. MVI_20011
        out_dir: Output directory for frames
        max_frames: Maximum frames to copy
    """
    # Try multiple possible paths
    candidates = [
        os.path.join(detrac_root, sequence),
        os.path.join(detrac_root, "DETRAC-Train-Images", sequence),
        os.path.join(detrac_root, "Insight-" + sequence),
    ]

    src_path = None
    for c in candidates:
        if os.path.isdir(c):
            src_path = c
            break

    if src_path is None:
        print(f"[ERROR] Sequence {sequence} not found in {detrac_root}")
        print(f"  Tried: {candidates}")
        return False

    frames = sorted([
        f for f in os.listdir(src_path)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ])[:max_frames]

    dest = os.path.join(out_dir, sequence)
    os.makedirs(dest, exist_ok=True)

    print(f"[Setup] Copying {len(frames)} frames: {sequence} → {dest}")
    for fname in frames:
        shutil.copy2(os.path.join(src_path, fname), os.path.join(dest, fname))

    print(f"[Setup] Done. {len(frames)} frames ready.")
    return True


def scan_detrac_sequences(detrac_root: str):
    """List all available sequences in UA-DETRAC dataset."""
    sequences = []
    for root_candidate in [
        detrac_root,
        os.path.join(detrac_root, "DETRAC-Train-Images"),
    ]:
        if os.path.isdir(root_candidate):
            for d in sorted(os.listdir(root_candidate)):
                full_path = os.path.join(root_candidate, d)
                if os.path.isdir(full_path) and d.startswith("MVI_"):
                    n = verify_sequence(full_path)
                    sequences.append((d, n, full_path))

    return sequences


def print_project_structure():
    """Print expected project directory structure."""
    print("""
=== Expected Project Structure ===
overspeed_detection/
├── database/
│   ├── schema.sql          ← ER Diagram schema
│   ├── db_manager.py       ← All DB operations
│   └── overspeed.db        ← SQLite DB (auto-created)
├── detection/
│   ├── vehicle_detector.py ← YOLOv8 + MOG2 fallback
│   ├── centroid_tracker.py ← Centroid-based tracking
│   ├── speed_estimator.py  ← Pixel→speed conversion
│   └── pipeline.py         ← Main processing loop
├── dashboard/
│   └── app.py              ← Streamlit dashboard
├── utils/
│   └── dataset_setup.py    ← This file
├── data/
│   └── frames/
│       └── MVI_20011/      ← UA-DETRAC frames here
│           ├── img00001.jpg
│           └── ...
├── snapshots/              ← Auto-saved violation frames
├── config/
└── requirements.txt
""")


def main():
    parser = argparse.ArgumentParser(description="UA-DETRAC Dataset Setup")
    parser.add_argument("--detrac-root", help="Path to UA-DETRAC dataset root")
    parser.add_argument("--sequence", default="MVI_20011", help="Sequence to prepare")
    parser.add_argument("--zip", help="Path to UA-DETRAC zip file")
    parser.add_argument("--scan", action="store_true", help="List available sequences")
    parser.add_argument("--max-frames", type=int, default=500)
    args = parser.parse_args()

    print_project_structure()

    if args.zip:
        extract_detrac_zip(args.zip, os.path.join(PROJECT_ROOT, "data"))

    if args.scan and args.detrac_root:
        sequences = scan_detrac_sequences(args.detrac_root)
        print(f"\n[Found {len(sequences)} sequences in {args.detrac_root}]")
        for name, count, path in sequences:
            print(f"  {name}: {count} frames — {path}")
        return

    if args.detrac_root:
        success = prepare_sequence(
            args.detrac_root, args.sequence,
            DATA_DIR, args.max_frames
        )
        if success:
            seq_out = os.path.join(DATA_DIR, args.sequence)
            print(f"\n[Ready] Run pipeline:")
            print(f"  python -m detection.pipeline --source {seq_out}")
    else:
        print("\n[INFO] No dataset path provided.")
        print("Download UA-DETRAC from:")
        print("  https://www.kaggle.com/datasets/bratjay/ua-detrac-orig")
        print("  https://detrac-db.rit.albany.edu/")
        print("\nThen run:")
        print("  python utils/dataset_setup.py --detrac-root /path/to/ua-detrac --scan")
        print("  python utils/dataset_setup.py --detrac-root /path/to/ua-detrac --sequence MVI_20011")


if __name__ == "__main__":
    main()
