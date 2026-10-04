"""
Vehicle Detector Module
Primary: YOLOv8 (Ultralytics) for real detections
Fallback: OpenCV background subtraction when YOLO model unavailable

Supports UA-DETRAC dataset frames (.jpg/.png sequences or .mp4).
"""

import cv2
import numpy as np
import os
from typing import List, Tuple, Optional

# YOLOv8 class mapping (COCO dataset classes relevant to traffic)
VEHICLE_CLASSES = {
    2: "car",
    3: "bike",
    5: "bus",
    7: "truck"
}


class YOLODetector:
    """
    YOLOv8-based vehicle detector.
    Loads pre-trained YOLOv8n model and filters for vehicle classes.
    """

    def __init__(self, model_path: str = "yolov8n.pt", confidence: float = 0.4, device: str = "cpu"):
        self.confidence = confidence
        self.device = device
        self.model = None
        self._load_model(model_path)

    def _load_model(self, model_path: str):
        try:
            from ultralytics import YOLO
            self.model = YOLO(model_path)
            self.model.to(self.device)
            print(f"[YOLO] Model loaded: {model_path}")
        except ImportError:
            print("[YOLO] ultralytics not available — using fallback detector")
            self.model = None
        except Exception as e:
            print(f"[YOLO] Failed to load model: {e} — using fallback")
            self.model = None

    def detect(self, frame: np.ndarray) -> Tuple[List[Tuple], List[str], List[float]]:
        """
        Detect vehicles in a frame.
        Returns: (bboxes, class_names, confidences)
            bboxes: List of [x1, y1, x2, y2]
        """
        if self.model is None:
            return [], [], []

        results = self.model(frame, verbose=False, conf=self.confidence)
        bboxes, classes, confs = [], [], []

        for result in results:
            for box in result.boxes:
                cls_id = int(box.cls[0])
                if cls_id in VEHICLE_CLASSES:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    bboxes.append((x1, y1, x2, y2))
                    classes.append(VEHICLE_CLASSES[cls_id])
                    confs.append(float(box.conf[0]))

        return bboxes, classes, confs

    def is_loaded(self) -> bool:
        return self.model is not None


class MOGDetector:
    """
    Fallback vehicle detector using MOG2 background subtraction.
    Works without YOLOv8 — good for UA-DETRAC fixed camera scenes.
    Detects moving blobs and treats them as vehicles.
    """

    def __init__(self, min_area: int = 2000, max_area: int = 80000):
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=50, detectShadows=True
        )
        self.min_area = min_area
        self.max_area = max_area
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def detect(self, frame: np.ndarray) -> Tuple[List[Tuple], List[str], List[float]]:
        """Detect moving vehicle blobs."""
        fg_mask = self.bg_subtractor.apply(frame)
        # Remove shadows (value 127) and noise
        _, thresh = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, self.kernel, iterations=2)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_DILATE, self.kernel, iterations=3)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        bboxes, classes, confs = [], [], []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if self.min_area <= area <= self.max_area:
                x, y, w, h = cv2.boundingRect(cnt)
                # Filter non-vehicle-like aspect ratios
                aspect = w / float(h)
                if 0.5 <= aspect <= 5.0:
                    bboxes.append((x, y, x + w, y + h))
                    classes.append("car")    # MOG can't classify — default to car
                    confs.append(0.7)

        return bboxes, classes, confs


class VehicleDetector:
    """
    Unified detector: tries YOLOv8 first, falls back to MOG2.
    """

    def __init__(self, yolo_model: str = "yolov8n.pt", confidence: float = 0.4):
        self.yolo = YOLODetector(yolo_model, confidence)
        self.mog = MOGDetector()
        self.using_yolo = self.yolo.is_loaded()
        print(f"[Detector] Using: {'YOLOv8' if self.using_yolo else 'MOG2 Background Subtraction'}")

    def detect(self, frame: np.ndarray) -> Tuple[List[Tuple], List[str], List[float]]:
        if self.using_yolo:
            bboxes, classes, confs = self.yolo.detect(frame)
            if len(bboxes) == 0:
                # Fallback if YOLO returns nothing
                bboxes, classes, confs = self.mog.detect(frame)
        else:
            bboxes, classes, confs = self.mog.detect(frame)
        return bboxes, classes, confs

    def detector_name(self) -> str:
        return "YOLOv8" if self.using_yolo else "MOG2 (fallback)"
