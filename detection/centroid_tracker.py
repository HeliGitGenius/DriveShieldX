"""
Centroid-Based Vehicle Tracker
Tracks vehicles across frames using Euclidean distance matching.
Based on: Rathore et al. (2019) – Vehicle speed estimation using centroid method.
"""

import numpy as np
from collections import OrderedDict
from typing import Dict, List, Tuple, Optional


class CentroidTracker:
    """
    Tracks vehicle centroids across video frames.
    Each vehicle is assigned a unique tracking ID.
    Maintains a history of centroid positions per vehicle ID.
    """

    def __init__(self, max_disappeared: int = 30, max_distance: float = 80.0):
        """
        Args:
            max_disappeared: Frames a vehicle can be missing before deregistered.
            max_distance: Max pixel distance to match a detection to existing track.
        """
        self.next_object_id = 0
        self.objects: OrderedDict[int, np.ndarray] = OrderedDict()   # id -> centroid
        self.disappeared: OrderedDict[int, int] = OrderedDict()       # id -> frame count
        self.centroid_history: Dict[int, List[Tuple[float, float]]] = {}  # id -> list of (cx,cy)
        self.vehicle_types: Dict[int, str] = {}                        # id -> class label

        self.max_disappeared = max_disappeared
        self.max_distance = max_distance

    def register(self, centroid: np.ndarray, vehicle_type: str = "car") -> int:
        """Register a new object with a unique ID."""
        obj_id = self.next_object_id
        self.objects[obj_id] = centroid
        self.disappeared[obj_id] = 0
        self.centroid_history[obj_id] = [(float(centroid[0]), float(centroid[1]))]
        self.vehicle_types[obj_id] = vehicle_type
        self.next_object_id += 1
        return obj_id

    def deregister(self, obj_id: int):
        """Remove a tracked object."""
        del self.objects[obj_id]
        del self.disappeared[obj_id]
        # Keep history for final logging

    def update(self, rects: List[Tuple], vehicle_types: List[str] = None) -> OrderedDict:
        """
        Update tracker with new detections.

        Args:
            rects: List of bounding boxes [x1, y1, x2, y2]
            vehicle_types: Corresponding class labels

        Returns:
            OrderedDict of {object_id: centroid}
        """
        if vehicle_types is None:
            vehicle_types = ["car"] * len(rects)

        # No detections this frame
        if len(rects) == 0:
            for obj_id in list(self.disappeared.keys()):
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    self.deregister(obj_id)
            return self.objects

        # Compute centroids for all detections
        input_centroids = np.zeros((len(rects), 2), dtype="float")
        for i, (x1, y1, x2, y2) in enumerate(rects):
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            input_centroids[i] = (cx, cy)

        # No existing tracks → register all
        if len(self.objects) == 0:
            for i in range(len(input_centroids)):
                self.register(input_centroids[i], vehicle_types[i])
            return self.objects

        # Match detections to existing tracks using Euclidean distance
        object_ids = list(self.objects.keys())
        object_centroids = list(self.objects.values())

        D = np.linalg.norm(
            np.array(object_centroids)[:, np.newaxis] - input_centroids[np.newaxis, :],
            axis=2
        )

        rows = D.min(axis=1).argsort()
        cols = D.argmin(axis=1)[rows]

        used_rows = set()
        used_cols = set()

        for (row, col) in zip(rows, cols):
            if row in used_rows or col in used_cols:
                continue
            if D[row, col] > self.max_distance:
                continue

            obj_id = object_ids[row]
            self.objects[obj_id] = input_centroids[col]
            self.disappeared[obj_id] = 0
            self.centroid_history[obj_id].append(
                (float(input_centroids[col][0]), float(input_centroids[col][1]))
            )
            self.vehicle_types[obj_id] = vehicle_types[col]
            used_rows.add(row)
            used_cols.add(col)

        # Handle unmatched existing tracks
        unused_rows = set(range(D.shape[0])) - used_rows
        for row in unused_rows:
            obj_id = object_ids[row]
            self.disappeared[obj_id] += 1
            if self.disappeared[obj_id] > self.max_disappeared:
                self.deregister(obj_id)

        # Register new detections
        unused_cols = set(range(D.shape[1])) - used_cols
        for col in unused_cols:
            self.register(input_centroids[col], vehicle_types[col])

        return self.objects

    def get_recent_positions(self, obj_id: int, n: int = 5) -> List[Tuple[float, float]]:
        """Get last n centroid positions for a tracked vehicle."""
        history = self.centroid_history.get(obj_id, [])
        return history[-n:] if len(history) >= n else history

    def get_all_history(self) -> Dict[int, List[Tuple[float, float]]]:
        return self.centroid_history
