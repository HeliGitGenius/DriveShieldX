"""Compatibility CLI wrapper for the advanced pipeline.

Examples:
    python -m detection.pipeline --source path/to/video.mp4 --camera-id 1
    python -m detection.pipeline --source rtsp://user:pass@ip/stream --source-type rtsp --camera-id 2
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import init_database
from detection.advanced_pipeline import AdvancedOverspeedPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="DriveShieldX advanced monitoring pipeline")
    parser.add_argument("--source", required=True, help="Video file, RTSP URL, webcam index, or directory of frames")
    parser.add_argument("--camera-id", type=int, default=1)
    parser.add_argument("--source-type", default="file", choices=["file", "rtsp", "webcam", "directory"])
    parser.add_argument("--tracker", default="bytetrack", choices=["bytetrack", "centroid"])
    parser.add_argument("--enable-npr", action="store_true")
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--frame-skip", type=int, default=1)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--resize-width", type=int, default=0)
    args = parser.parse_args()

    init_database()
    pipeline = AdvancedOverspeedPipeline(
        camera_id=args.camera_id,
        source=args.source,
        source_type=args.source_type,
        tracker_mode=args.tracker,
        enable_npr=args.enable_npr,
        gpu_enabled=args.gpu,
        frame_skip=args.frame_skip,
        resize_width=(args.resize_width or None),
    )
    for _ in pipeline.run_generator(max_frames=args.max_frames):
        pass


if __name__ == "__main__":
    main()
