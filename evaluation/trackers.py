"""Tracker adapters that all consume the SAME cached detection stream.

Protocol (paper Sec. 6.3): the detector runs once per sequence, its output is
cached, and the identical stream is replayed through each association method,
so any difference is attributable to association alone.

Each adapter: reset(); update(dets, frame) -> np.ndarray (M,5) = id,x1,y1,x2,y2
where dets is (N,6) = x1,y1,x2,y2,conf,cls in image pixels.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class BaseAdapter:
    name = "base"
    needs_frame = False

    def reset(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def update(self, dets: np.ndarray, frame: Optional[np.ndarray], shape) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ByteTrack -- the deployed tracker (Ultralytics implementation, bytetrack.yaml)
# ---------------------------------------------------------------------------
class ByteTrackAdapter(BaseAdapter):
    name = "ByteTrack"

    def __init__(self) -> None:
        from ultralytics.utils import YAML, IterableSimpleNamespace
        from ultralytics.utils.checks import check_yaml

        self._cfg = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
        self.reset()

    def reset(self) -> None:
        from ultralytics.trackers.byte_tracker import BYTETracker

        self.tracker = BYTETracker(args=self._cfg)

    def update(self, dets, frame, shape):
        from ultralytics.engine.results import Boxes

        data = dets[:, :6] if len(dets) else np.zeros((0, 6))
        boxes = Boxes(np.asarray(data, dtype=np.float32), orig_shape=shape[:2])
        out = self.tracker.update(boxes.cpu().numpy() if hasattr(boxes, "cpu") else boxes, frame)
        if out is None or len(out) == 0:
            return np.zeros((0, 5))
        out = np.asarray(out, dtype=float)
        # rows: x1,y1,x2,y2,id,score,cls,idx. Clip to the image exactly as Results.boxes does
        # in the deployed model.track() call, so replay == deployed output (verified).
        h, w = shape[:2]
        out[:, [0, 2]] = out[:, [0, 2]].clip(0, w)
        out[:, [1, 3]] = out[:, [1, 3]].clip(0, h)
        return np.stack([out[:, 4], out[:, 0], out[:, 1], out[:, 2], out[:, 3]], axis=1)


# ---------------------------------------------------------------------------
# Centroid associator -- the deployed fallback (detection/centroid_tracker.py)
# ---------------------------------------------------------------------------
class CentroidAdapter(BaseAdapter):
    """Wraps the project's own CentroidTracker with the deployed parameters
    (HybridTracker: max_disappeared=25, max_distance=100).

    Only tracks that were matched/registered in the current frame are reported,
    with their detection box. (The app additionally draws a fixed 40x40 box for
    tracks that are 'disappeared' but not yet deleted; scoring those phantom boxes
    would only add false positives, so they are excluded for all trackers alike.)
    """

    name = "Centroid"

    def __init__(self, max_disappeared: int = 25, max_distance: float = 100.0) -> None:
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance
        self.reset()

    def reset(self) -> None:
        from detection.centroid_tracker import CentroidTracker

        self.tracker = CentroidTracker(max_disappeared=self.max_disappeared, max_distance=self.max_distance)

    def update(self, dets, frame, shape):
        rects = [tuple(map(float, d[:4])) for d in dets]
        objects = self.tracker.update(rects, ["car"] * len(rects))
        by_centroid = {}
        for r in rects:
            by_centroid[(round((r[0] + r[2]) / 2.0, 4), round((r[1] + r[3]) / 2.0, 4))] = r
        rows = []
        for oid, c in objects.items():
            if self.tracker.disappeared.get(oid, 1) != 0:
                continue
            key = (round(float(c[0]), 4), round(float(c[1]), 4))
            r = by_centroid.get(key)
            if r is None:
                continue
            rows.append((oid + 1, *r))
        return np.asarray(rows, dtype=float).reshape(-1, 5)


# ---------------------------------------------------------------------------
# DeepSORT -- implemented here as the comparison baseline (Wojke et al. [5])
# ---------------------------------------------------------------------------
class DeepSortAdapter(BaseAdapter):
    """DeepSORT via the `deep-sort-realtime` package: Kalman + Hungarian with a
    MobileNetV2 appearance embedding (weights ship with the package, CPU-only ok).
    Parameters follow the original paper defaults (max_age=30, n_init=3,
    max_cosine_distance=0.2, nn_budget=100)."""

    name = "DeepSORT"
    needs_frame = True

    def __init__(self, max_age: int = 30, n_init: int = 3, max_cosine_distance: float = 0.2,
                 nn_budget: int = 100, embedder: str = "mobilenet", gpu: bool = False) -> None:
        self.kw = dict(max_age=max_age, n_init=n_init, max_cosine_distance=max_cosine_distance,
                       nn_budget=nn_budget, embedder=embedder, embedder_gpu=gpu, half=gpu)
        self.reset()

    def reset(self) -> None:
        from deep_sort_realtime.deepsort_tracker import DeepSort

        self.tracker = DeepSort(**self.kw)

    def update(self, dets, frame, shape):
        raw = [([float(d[0]), float(d[1]), float(d[2] - d[0]), float(d[3] - d[1])], float(d[4]), int(d[5]))
               for d in dets]
        tracks = self.tracker.update_tracks(raw, frame=frame)
        rows = []
        for t in tracks:
            if not t.is_confirmed() or t.time_since_update > 0:
                continue
            box = t.to_ltrb(orig=True)
            if box is None:
                box = t.to_ltrb()
            rows.append((int(t.track_id), *map(float, box)))
        return np.asarray(rows, dtype=float).reshape(-1, 5)


# ---------------------------------------------------------------------------
# Ablation: no tracker (per-frame detection only) -- every box gets a new ID
# ---------------------------------------------------------------------------
class NoTrackerAdapter(BaseAdapter):
    name = "NoTracker(per-frame)"

    def reset(self) -> None:
        self._next = 1

    def __init__(self) -> None:
        self.reset()

    def update(self, dets, frame, shape):
        rows = []
        for d in dets:
            rows.append((self._next, *map(float, d[:4])))
            self._next += 1
        return np.asarray(rows, dtype=float).reshape(-1, 5)


ADAPTERS = {
    "bytetrack": ByteTrackAdapter,
    "centroid": CentroidAdapter,
    "deepsort": DeepSortAdapter,
    "notracker": NoTrackerAdapter,
}
