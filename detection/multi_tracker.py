from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

from detection.centroid_tracker import CentroidTracker
from detection.vehicle_detector import VEHICLE_CLASSES


@dataclass
class TrackResult:
    tracker_id: str
    bbox: Tuple[int, int, int, int]
    centroid: Tuple[float, float]
    vehicle_type: str
    confidence: float


class HybridTracker:
    """ByteTrack for vehicles + separate person detection for rule analysis.

    Vehicles:
        car, bike, bus, truck

    Persons:
        Detected separately using YOLO class 0.

    Persons are NOT added to the vehicle tracker because they should
    never participate in speed/ANPR/vehicle database processing.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        conf: float = 0.35,
        device: str = "cpu",
        tracker_mode: str = "bytetrack",
    ) -> None:

        self.conf = conf
        self.device = device
        self.tracker_mode = tracker_mode

        self.model = None

        self.centroid = CentroidTracker(
            max_disappeared=25,
            max_distance=100,
        )

        self._load_model(model_path)

    # ------------------------------------------------------------------
    # YOLO MODEL
    # ------------------------------------------------------------------

    def _load_model(self, model_path: str) -> None:
        try:
            from ultralytics import YOLO

            self.model = YOLO(model_path)
            self.model.to(self.device)

            print(
                f"[HybridTracker] YOLO model loaded: {model_path}"
            )

        except Exception as e:
            print(
                f"[HybridTracker] Failed to load YOLO model: {e}"
            )
            self.model = None

    # ------------------------------------------------------------------
    # STATUS
    # ------------------------------------------------------------------

    @property
    def using_bytetrack(self) -> bool:
        return (
            self.model is not None
            and self.tracker_mode == "bytetrack"
        )

    # ------------------------------------------------------------------
    # VEHICLE TRACKING
    # ------------------------------------------------------------------

    def track(self, frame) -> List[TrackResult]:

        # ==============================================================
        # BYTE TRACK PATH
        # ==============================================================

        if self.using_bytetrack:

            results = self.model.track(
                frame,
                persist=True,
                verbose=False,
                conf=self.conf,
                imgsz=640,
                tracker="bytetrack.yaml",

                # IMPORTANT:
                # Only vehicles are tracked here.
                # COCO class 0 (person) is intentionally excluded.
                classes=list(VEHICLE_CLASSES.keys()),
            )

            tracks: List[TrackResult] = []

            for result in results:

                ids = result.boxes.id

                if ids is None:
                    continue

                for i, box in enumerate(result.boxes):

                    cls_id = int(box.cls[0])

                    if cls_id not in VEHICLE_CLASSES:
                        continue

                    x1, y1, x2, y2 = map(
                        int,
                        box.xyxy[0].tolist(),
                    )

                    track_id = str(
                        int(ids[i].item())
                    )

                    centroid = (
                        (x1 + x2) / 2.0,
                        (y1 + y2) / 2.0,
                    )

                    tracks.append(
                        TrackResult(
                            tracker_id=track_id,
                            bbox=(x1, y1, x2, y2),
                            centroid=centroid,
                            vehicle_type=VEHICLE_CLASSES[cls_id],
                            confidence=float(box.conf[0]),
                        )
                    )

            return tracks

        # ==============================================================
        # FALLBACK CENTROID TRACKER
        # ==============================================================

        if self.model is None:

            from detection.vehicle_detector import VehicleDetector

            detector = VehicleDetector(

                yolo_model="yolov8n.pt",
                confidence=self.conf,
            )

            self.model = detector  # type: ignore[assignment]

        # Vehicle detection
        if hasattr(self.model, "detect"):

            bboxes, classes, confs = self.model.detect(frame)

        else:

            results = self.model(
                frame,
                verbose=False,
                conf=self.conf,
            )

            bboxes = []
            classes = []
            confs = []

            for result in results:

                for box in result.boxes:

                    cls_id = int(box.cls[0])

                    if cls_id not in VEHICLE_CLASSES:
                        continue

                    bboxes.append(
                        tuple(
                            map(
                                int,
                                box.xyxy[0].tolist(),
                            )
                        )
                    )

                    classes.append(
                        VEHICLE_CLASSES[cls_id]
                    )

                    confs.append(
                        float(box.conf[0])
                    )

        # Update centroid tracker
        objects = self.centroid.update(
            bboxes,
            classes,
        )

        reverse_bbox = {}

        for bbox, cls, conf in zip(
            bboxes,
            classes,
            confs,
        ):

            x1, y1, x2, y2 = bbox

            c = np.array(
                [
                    (x1 + x2) / 2.0,
                    (y1 + y2) / 2.0,
                ]
            )

            reverse_bbox[
                (
                    float(c[0]),
                    float(c[1]),
                )
            ] = (
                bbox,
                cls,
                conf,
            )

        tracks: List[TrackResult] = []

        for obj_id, c in objects.items():

            key = (
                float(c[0]),
                float(c[1]),
            )

            fallback_bbox = (
                int(c[0] - 20),
                int(c[1] - 20),
                int(c[0] + 20),
                int(c[1] + 20),
            )

            bbox, cls, conf = reverse_bbox.get(
                key,
                (
                    fallback_bbox,
                    self.centroid.vehicle_types.get(
                        obj_id,
                        "car",
                    ),
                    0.7,
                ),
            )

            tracks.append(
                TrackResult(
                    tracker_id=str(obj_id),
                    bbox=bbox,
                    centroid=key,
                    vehicle_type=cls,
                    confidence=conf,
                )
            )

        return tracks

    # ------------------------------------------------------------------
    # PERSON DETECTION
    # ------------------------------------------------------------------

    def detect_persons(
        self,
        frame,
    ) -> List[Tuple[int, int, int, int]]:
        """Detect persons for triple-riding analysis.

        COCO class 0 = person.

        This is deliberately separate from vehicle tracking.
        Persons are returned only as bounding boxes and are NOT
        registered as vehicles.
        """

        if self.model is None:
            return []

        try:

            # ----------------------------------------------------------
            # Normal Ultralytics YOLO model
            # ----------------------------------------------------------

            if hasattr(self.model, "predict"):

                results = self.model.predict(
                    frame,
                    verbose=False,
                    conf=self.conf,
                    imgsz=640,

                    # COCO class 0 = person
                    classes=[0],
                )

                person_boxes: List[
                    Tuple[int, int, int, int]
                ] = []

                for result in results:

                    for box in result.boxes:

                        cls_id = int(box.cls[0])

                        if cls_id != 0:
                            continue

                        x1, y1, x2, y2 = map(
                            int,
                            box.xyxy[0].tolist(),
                        )

                        person_boxes.append(
                            (
                                x1,
                                y1,
                                x2,
                                y2,
                            )
                        )

                return person_boxes

        except Exception as e:

            print(
                f"[HybridTracker] Person detection failed: {e}"
            )

            return []

        return []

    # ------------------------------------------------------------------
    # TRACK HISTORY
    # ------------------------------------------------------------------

    def get_recent_positions(
        self,
        tracker_id: str,
        n: int = 8,
    ):
        try:

            obj_id = int(tracker_id)

            return self.centroid.get_recent_positions(
                obj_id,
                n,
            )

        except Exception:

            return []