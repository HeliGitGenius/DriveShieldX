from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional

import cv2
import numpy as np

from backend.alerts import send_email_alert, send_sms_alert
from backend.event_bus import BUS
from backend.logger import get_logger
from database.db_manager import (
    close_session,
    create_session,
    get_config,
    get_system_events,
    get_unissued_violations,
    insert_rule_violation,
    insert_gate_review,
    insert_speed_record,
    insert_violation,
    list_zones,
    log_system_event,
    upsert_stream_status,
    upsert_vehicle,
    update_session_progress,
)
from detection.npr import NumberPlateRecognizer
from detection.multi_tracker import HybridTracker, TrackResult
from detection.rule_violations import RuleViolationDetector
from detection.speed_estimator import SpeedEstimator
from detection.fog_monitor import FogMonitor
from detection.accident_monitor import AccidentMonitor
from detection.zone_utils import find_matching_zone, zone_from_record

logger = get_logger("pipeline")

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

CLASS_COLORS = {
    "car": (0, 255, 0),
    "bus": (255, 165, 0),
    "truck": (255, 0, 0),
    "bike": (0, 255, 255),
    "unknown": (180, 180, 180),
}

SEV_COLORS = {
    "low": (0, 255, 255),
    "medium": (0, 165, 255),
    "high": (0, 0, 255),
    "extreme": (128, 0, 128),
}


class CaptureWorker(threading.Thread):
    def __init__(
        self,
        source: str,
        frame_queue: queue.Queue,
        stop_event: threading.Event,
        source_type: str = "file",
    ) -> None:
        super().__init__(daemon=True)

        self.source = source
        self.source_type = source_type
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.cap: Optional[cv2.VideoCapture] = None
        self.source_fps: float = 0.0
        self.frames_dropped = 0
    def _open(self) -> Optional[cv2.VideoCapture]:
        cap = cv2.VideoCapture(
            int(self.source) if str(self.source).isdigit() else self.source
        )

        return cap if cap.isOpened() else None

    def run(self) -> None:
        reconnects = 0
        self.cap = self._open()
        if self.cap is not None:
            fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
            self.source_fps = fps if 1.0 <= fps <= 240.0 else 0.0
        while not self.stop_event.is_set():

            if self.cap is None:

                if self.source_type == "rtsp" and reconnects < 5:
                    time.sleep(1.0)
                    reconnects += 1
                    self.cap = self._open()
                    continue

                break

            ok, frame = self.cap.read()

            if not ok:

                if self.source_type == "rtsp" and reconnects < 5:
                    reconnects += 1
                    time.sleep(0.5)

                    self.cap.release()
                    self.cap = self._open()

                    continue

                break

            if self.source_type == "file":
                # Recorded video: never drop frames -- speed maths assumes a
                # constant time step between processed frames.
                while not self.stop_event.is_set():
                    try:
                        self.frame_queue.put(frame, timeout=0.5)
                        break
                    except queue.Full:
                        continue
                continue
            try:
                self.frame_queue.put(frame, timeout=0.5)
            except queue.Full:
                self.frames_dropped += 1  # live stream: keep up with real time
                continue
        if self.cap is not None:
            self.cap.release()


@dataclass
class PipelineResult:
    frame_index: int
    tracked: int
    violations: int
    fps: float
    annotated_frame: Optional[np.ndarray]
    latest_records: List[Dict]
    latest_events: List[Dict]
    latest_violation: Optional[Dict]


_DEMO_STATES = [
    "MH",
    "DL",
    "KA",
    "GJ",
    "TN",
    "UP",
    "RJ",
    "WB",
]

_DEMO_SERIES = [
    "AB",
    "CD",
    "EF",
    "GH",
    "JK",
    "LM",
    "PQ",
    "RS",
]


def _cuda_available() -> bool:

    try:
        import torch

        return bool(torch.cuda.is_available())

    except Exception:
        return False


def _demo_plate_for(tracker_id) -> str:

    h = abs(hash(str(tracker_id)))

    state = _DEMO_STATES[h % len(_DEMO_STATES)]

    rto = (h // 7) % 99 + 1

    series = _DEMO_SERIES[(h // 13) % len(_DEMO_SERIES)]

    number = (h // 17) % 9000 + 1000

    return f"{state}{rto:02d}{series}{number}"


class AdvancedOverspeedPipeline:

    def __init__(
        self,
        camera_id: int,
        source: str,
        source_type: str = "file",
        model_path: str = "yolov8n.pt",
        event_callback: Optional[Callable[[dict], None]] = None,
        tracker_mode: Optional[str] = None,
        enable_npr: Optional[bool] = None,
        gpu_enabled: Optional[bool] = None,
        frame_skip: Optional[int] = None,
        batch_mode: Optional[bool] = None,
        resize_width: Optional[int] = None,
        demo_plate_mode: bool = False,
    ) -> None:

        self.camera_id = camera_id
        self.source = source
        self.source_type = source_type

        self.event_callback = event_callback or BUS.publish

        self.resize_width = resize_width

        self.demo_plate_mode = demo_plate_mode

        cfg = get_config(camera_id) or {}

        self.speed_limit = float(
            cfg.get("speed_limit", 60.0)
        )

        self.pixel_scale = float(
            cfg.get("pixel_to_meter_scale", 0.045)
        )

        self.tracker_mode = (
            tracker_mode
            or cfg.get("tracker_mode", "bytetrack")
        )

        self.enable_npr = bool(
            cfg.get("enable_npr", 0)
            if enable_npr is None
            else enable_npr
        )

        requested_gpu = bool(
            cfg.get("gpu_enabled", 0)
            if gpu_enabled is None
            else gpu_enabled
        )

        self.gpu_enabled = (
            requested_gpu
            and _cuda_available()
        )

        if requested_gpu and not self.gpu_enabled:
            logger.warning(
                "GPU requested but no CUDA device found — running on CPU instead."
            )

        self.frame_skip = max(
            1,
            int(
                cfg.get("frame_skip", 1)
                if frame_skip is None
                else frame_skip
            ),
        )

        self.batch_mode = bool(
            cfg.get("batch_mode", 0)
            if batch_mode is None
            else batch_mode
        )

        self.zones = [
            zone_from_record(r)
            for r in list_zones(camera_id)
        ]

        # ---------------------------------------------------------
        # MAIN VEHICLE TRACKER
        # ---------------------------------------------------------

        self.tracker = HybridTracker(
            model_path=model_path,
            device=(
                "cuda:0"
                if self.gpu_enabled
                else "cpu"
            ),
            tracker_mode=self.tracker_mode,
        )

        # ---------------------------------------------------------
        # PERSON DETECTOR
        #
        # IMPORTANT:
        # yolov8n.pt is COCO-trained and class 0 = person.
        #
        # We use it ONLY for person counting.
        # ---------------------------------------------------------

        self.person_model = None

        try:

            from ultralytics import YOLO

            self.person_model = YOLO("yolov8n.pt")

            self.person_model.to(
                "cuda:0"
                if self.gpu_enabled
                else "cpu"
            )

            logger.info(
                "Person detector loaded from yolov8n.pt"
            )

        except Exception as exc:

            logger.warning(
                "Could not load person detector: %s",
                exc,
            )

            self.person_model = None

        # ---------------------------------------------------------
        # OTHER DETECTORS
        # ---------------------------------------------------------

        self.npr = (
            NumberPlateRecognizer(
                use_gpu=self.gpu_enabled
            )
            if self.enable_npr
            else None
        )

        self.rule_detector = RuleViolationDetector()

        # ---------------------------------------------------------
        # SPEED TRACKING
        # ---------------------------------------------------------

        self.speed_estimators: Dict[
            str,
            SpeedEstimator
        ] = {}

        self.track_histories: Dict[
            str,
            List[tuple]
        ] = {}

        self.plate_histories: Dict[
            str,
            List[tuple]
        ] = {}

        self.plate_speed_estimators: Dict[
            str,
            SpeedEstimator
        ] = {}

        self.tracker_to_plate: Dict[
            str,
            str
        ] = {}

        self._ocr_tick: Dict[
            str,
            int
        ] = {}

        # ---------------------------------------------------------
        # VIOLATIONS
        # ---------------------------------------------------------

        self.logged_violations: set = set()

        self.stop_event = threading.Event()

        self.frame_queue: queue.Queue = queue.Queue(
            maxsize=8
        )

        self.capture_worker: Optional[
            CaptureWorker
        ] = None

        self.session_id: Optional[int] = None

        self.frame_count = 0

        self.violation_count = 0

        self.rule_violation_count = 0

        self.last_fps = 0.0

        # ---------------------------------------------------------
        # SPEED FIXES (paper Sec. 6.4 / 9)
        #   * frame rate read from the source instead of a fixed 25.0
        #   * plausibility gate: estimates above MAX_PLAUSIBLE_KMH are
        #     tracking/calibration artefacts and never become violations
        # ---------------------------------------------------------
        self.default_fps = float(os.environ.get("DSX_DEFAULT_FPS", "25.0"))
        self.source_fps = self.default_fps
        self.max_plausible_kmh = float(os.environ.get("DSX_MAX_PLAUSIBLE_KMH", "200"))
        self.implausible_speeds = 0

        # ---------------------------------------------------------
        # ROAD-SAFETY LAYER (fog + accidents), same frames, own thread
        # ---------------------------------------------------------
        self.enable_fog = os.environ.get("DSX_ENABLE_FOG", "1") != "0"
        self.enable_accident = os.environ.get("DSX_ENABLE_ACCIDENT", "1") != "0"
        self.fog_monitor: Optional[FogMonitor] = None
        self.accident_monitor: Optional[AccidentMonitor] = None
        self.accident_count = 0

    # =========================================================
    # ROAD-SAFETY MONITORS
    # =========================================================

    @property
    def effective_fps(self) -> float:
        return max(1.0, self.source_fps / max(1, self.frame_skip))

    def _start_safety_monitors(self) -> None:
        try:
            from database import safety_db
        except Exception as exc:  # pragma: no cover
            logger.warning("Safety layer unavailable: %s", exc)
            return

        if self.enable_fog:
            def on_fog(event: str, reading: dict) -> None:
                try:
                    safety_db.insert_fog_event(self.camera_id, self.session_id, reading, event,
                                               reading.get("advisory_speed"))
                    if event == "advisory_on":
                        msg = (f"{reading['level'].replace('_', ' ')} on camera {self.camera_id}: "
                               f"visibility index {reading['visibility_index']}, "
                               f"advisory speed {reading.get('advisory_speed')} km/h")
                        level = "warning"
                    else:
                        msg = f"Visibility restored on camera {self.camera_id}; fog advisory lifted"
                        level = "info"
                    log_system_event("fog", msg, level=level, camera_id=self.camera_id, payload=reading)
                    self._publish({"type": "fog", "camera_id": self.camera_id, "event": event, **reading})
                except Exception as exc:
                    logger.warning("Fog event handling failed: %s", exc)

            try:
                self.fog_monitor = FogMonitor(
                    self.camera_id, self.speed_limit, on_event=on_fog,
                    baseline=safety_db.get_fog_baseline(self.camera_id),
                    on_baseline=lambda b: safety_db.set_fog_baseline(self.camera_id, b),
                ).start()
            except Exception as exc:
                logger.warning("Fog monitor not started: %s", exc)

        if self.enable_accident:
            # Learned crash model (R3D-18) when its checkpoint exists: UCF-Crime held-out test
            # 7/23 crashes, 3.9 false alarms/h, vs 1/23 for the rule monitor. DSX_ACCIDENT_DETECTOR
            # = model (default when available) | rules | both.
            mode = os.environ.get("DSX_ACCIDENT_DETECTOR", "model").strip().lower()
            ckpt = os.environ.get("DSX_ACCIDENT_MODEL", str(Path(__file__).resolve().parents[1] / "models" / "accident_r3d18.pt"))
            self.accident_classifier = None
            if mode in ("model", "both") and Path(ckpt).is_file():
                try:
                    from detection.accident_classifier import AccidentVideoClassifier
                    self.accident_classifier = AccidentVideoClassifier(ckpt)
                    logger.info("Accident detection: learned R3D-18 model (%s)", ckpt)
                except Exception as exc:
                    logger.warning("Crash model not loaded, using the rule monitor: %s", exc)
            if self.accident_classifier is None or mode == "both":
                self.accident_monitor = AccidentMonitor(fps=self.effective_fps)

    def _stop_safety_monitors(self) -> None:
        if self.fog_monitor is not None:
            self.fog_monitor.stop()

    def _handle_accidents(self, frame, tracks) -> None:
        incidents = []
        clf = getattr(self, "accident_classifier", None)
        if clf is not None:
            try:
                if clf.update(frame, self.frame_count / max(1.0, float(self.effective_fps))):
                    from detection.accident_monitor import Incident
                    incidents.append(Incident(track_ids=tuple(str(tr.tracker_id) for tr in tracks[:4]),
                                              frame=self.frame_count, score=float(clf.threshold),
                                              cues={"detector": "learned R3D-18 crash model",
                                                    "threshold": clf.threshold, "consecutive_windows": clf.consecutive},
                                              status="confirmed"))
            except Exception as exc:
                logger.warning("Crash model step failed: %s", exc)
        if self.accident_monitor is not None:
            incidents += self.accident_monitor.update(
                self.frame_count, [(tr.tracker_id, tr.bbox) for tr in tracks])
        if not incidents:
            return
        from database import safety_db
        from backend.dispatch import dispatch_accident

        for inc in incidents:
            try:
                snap = self._save_snapshot(frame, "accident_" + "_".join(map(str, inc.track_ids)))
                aid = safety_db.insert_accident(self.camera_id, self.session_id, self.frame_count,
                                                list(inc.track_ids), inc.cues, inc.score, snap)
                self.accident_count += 1
                res = dispatch_accident(aid, self.camera_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        inc.score, inc.cues, list(inc.track_ids))
                log_system_event("accident", f"Accident #{aid} confirmed on camera {self.camera_id} "
                                 f"(vehicles {', '.join(inc.track_ids)}, score {inc.score:.2f}); "
                                 f"dispatched via {len(res['sent'])} channel(s)",
                                 level="error", camera_id=self.camera_id)
                self._publish({"type": "accident", "camera_id": self.camera_id, "accident_id": aid,
                               "tracks": list(inc.track_ids), "score": inc.score})
            except Exception as exc:
                logger.exception("Accident handling failed: %s", exc)

    # =========================================================
    # CONTROL
    # =========================================================

    def request_stop(self) -> None:
        self.stop_event.set()

    def _publish(self, event: dict) -> None:

        try:
            self.event_callback(event)

        except Exception:
            pass

    # =========================================================
    # SNAPSHOT
    # =========================================================

    def _save_snapshot(
        self,
        frame: np.ndarray,
        tracker_id: str,
    ) -> str:

        out = (
            SNAPSHOT_DIR
            / f"cam{self.camera_id}_"
              f"v{tracker_id}_"
              f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        )

        cv2.imwrite(
            str(out),
            frame
        )

        return str(out)

    # =========================================================
    # ZONE
    # =========================================================

    def _zone_for_centroid(self, centroid):

        return find_matching_zone(
            self.zones,
            centroid
        )

    # =========================================================
    # SPEED ESTIMATOR
    # =========================================================

    def _get_estimator(
        self,
        tracker_id: str,
        pixel_scale: float,
        fps: float,
        plate: Optional[str] = None,
    ) -> SpeedEstimator:

        if plate:

            key = plate.upper().strip()

            if key not in self.plate_speed_estimators:

                old = self.speed_estimators.get(
                    tracker_id
                )

                if old:

                    self.plate_speed_estimators[
                        key
                    ] = old

                else:

                    self.plate_speed_estimators[
                        key
                    ] = SpeedEstimator(
                        fps=fps,
                        pixel_to_meter=pixel_scale,
                        smoothing_window=5,
                    )

            est = self.plate_speed_estimators[key]

        else:

            if tracker_id not in self.speed_estimators:

                self.speed_estimators[
                    tracker_id
                ] = SpeedEstimator(
                    fps=fps,
                    pixel_to_meter=pixel_scale,
                    smoothing_window=5,
                )

            est = self.speed_estimators[
                tracker_id
            ]

        est.fps = fps
        est.pixel_to_meter = pixel_scale

        return est

    # =========================================================
    # PERSON DETECTION
    # =========================================================

    def _detect_person_boxes(
        self,
        frame: np.ndarray,
    ) -> List[tuple]:

        """
        Detect persons using COCO YOLOv8n.

        COCO class 0 = person.

        Returns:
            [
                (x1, y1, x2, y2),
                ...
            ]
        """

        if self.person_model is None:
            return []

        try:

            results = self.person_model.predict(
                source=frame,
                conf=0.30,
                imgsz=640,
                classes=[0],
                verbose=False,
            )

            person_boxes = []

            for result in results:

                if result.boxes is None:
                    continue

                for box in result.boxes:

                    cls_id = int(
                        box.cls[0]
                    )

                    if cls_id != 0:
                        continue

                    confidence = float(
                        box.conf[0]
                    )

                    if confidence < 0.30:
                        continue

                    x1, y1, x2, y2 = map(
                        int,
                        box.xyxy[0].tolist()
                    )

                    person_boxes.append(
                        (x1, y1, x2, y2)
                    )

            return person_boxes

        except Exception:

            logger.exception(
                "Person detection failed"
            )

            return []

    # =========================================================
    # DRAW
    # =========================================================

    def _draw_annotations(
        self,
        frame: np.ndarray,
        tracks: List[TrackResult],
        speeds: Dict[str, float],
        latest_meta: Dict[str, dict],
        person_boxes: Optional[List[tuple]] = None,
    ) -> np.ndarray:

        overlay = frame.copy()

        # -----------------------------------------------------
        # ZONES
        # -----------------------------------------------------

        for zone in self.zones:

            color = (
                (64, 255, 196)
                if zone.is_active
                else (128, 128, 128)
            )

            cv2.rectangle(
                overlay,
                (zone.x1, zone.y1),
                (zone.x2, zone.y2),
                color,
                2,
            )

            cv2.putText(
                overlay,
                f"{zone.name} | {zone.speed_limit} km/h",
                (
                    zone.x1 + 4,
                    max(18, zone.y1 + 18),
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
            )

        # -----------------------------------------------------
        # PERSON BOXES
        #
        # Drawn in a different style so you can visually verify
        # that the triple-riding logic is actually seeing people.
        # -----------------------------------------------------

        if person_boxes:

            for px1, py1, px2, py2 in person_boxes:

                cv2.rectangle(
                    overlay,
                    (px1, py1),
                    (px2, py2),
                    (255, 255, 0),
                    1,
                )

                cv2.putText(
                    overlay,
                    "person",
                    (px1, max(15, py1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (255, 255, 0),
                    1,
                )

        # -----------------------------------------------------
        # VEHICLES
        # -----------------------------------------------------

        for tr in tracks:

            x1, y1, x2, y2 = tr.bbox

            speed = speeds.get(
                tr.tracker_id
            )

            color = CLASS_COLORS.get(
                tr.vehicle_type,
                (0, 255, 0),
            )

            label = (
                f"V{tr.tracker_id} "
                f"[{tr.vehicle_type}]"
            )

            if speed is not None:

                label += (
                    f" {speed:.1f} km/h"
                )

            meta = latest_meta.get(
                tr.tracker_id,
                {},
            )

            if meta.get("severity"):

                color = SEV_COLORS[
                    meta["severity"]
                ]

                label += " !"

            # Rule text

            if meta.get("rule"):

                label += (
                    f" | {meta['rule']}"
                )

            cv2.rectangle(
                overlay,
                (x1, y1),
                (x2, y2),
                color,
                2,
            )

            cv2.circle(
                overlay,
                (
                    int(tr.centroid[0]),
                    int(tr.centroid[1]),
                ),
                5,
                color,
                -1,
            )

            if meta.get("plate_text"):

                label += (
                    f" | {meta['plate_text']}"
                )

            cv2.putText(
                overlay,
                label,
                (
                    x1,
                    max(20, y1 - 8),
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )

        # -----------------------------------------------------
        # TOP STATUS BAR
        # -----------------------------------------------------

        cv2.rectangle(
            overlay,
            (0, 0),
            (overlay.shape[1], 36),
            (0, 0, 0),
            -1,
        )

        cv2.putText(
            overlay,
            (
                f"Frame: {self.frame_count} | "
                f"FPS: {self.last_fps:.1f} | "
                f"Tracked: {len(tracks)} | "
                f"Violations: {self.violation_count} | "
                f"Rule Violations: {self.rule_violation_count} | "
                f"Speed Limit: {self.speed_limit} km/h"
            ),
            (8, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
        )

        # Road-safety status line (fog advisory / accidents)
        fog = self.fog_monitor.latest if self.fog_monitor is not None else None
        safety = []
        if fog:
            safety.append(f"Visibility: {fog['level'].replace('_', ' ')} ({fog['visibility_index']:.0f})")
            if self.fog_monitor.advisory_active:
                safety.append(f"FOG ADVISORY {self.fog_monitor.advisory_speed() or 0:.0f} km/h")
        if self.accident_count:
            safety.append(f"ACCIDENTS: {self.accident_count}")
        if safety:
            cv2.putText(overlay, " | ".join(safety), (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (0, 0, 255) if (self.accident_count or (fog and self.fog_monitor.advisory_active))
                        else (255, 255, 255), 2)

        return overlay

    # =========================================================
    # DIRECTORY FRAMES
    # =========================================================

    def _frames_from_directory(
        self,
    ) -> Generator[np.ndarray, None, None]:

        files = sorted(
            [
                p
                for p in Path(self.source).iterdir()
                if p.suffix.lower()
                in {
                    ".jpg",
                    ".jpeg",
                    ".png",
                }
            ]
        )

        for path in files:

            if self.stop_event.is_set():
                break

            frame = cv2.imread(
                str(path)
            )

            if frame is not None:
                yield frame

    # =========================================================
    # FRAME ITERATOR
    # =========================================================

    def _iter_frames(self):

        if os.path.isdir(self.source):

            yield from self._frames_from_directory()

            return

        self.capture_worker = CaptureWorker(
            self.source,
            self.frame_queue,
            self.stop_event,
            self.source_type,
        )

        self.capture_worker.start()

        while not self.stop_event.is_set():

            try:

                frame = self.frame_queue.get(
                    timeout=1.0
                )

                yield frame

            except queue.Empty:

                if (
                    self.capture_worker
                    and not self.capture_worker.is_alive()
                ):
                    break

                continue

    # =========================================================
    # PROCESS
    # =========================================================

    def process(
        self,
        max_frames: Optional[int] = None,
    ) -> List[Dict]:

        list(
            self.run_generator(
                max_frames=max_frames
            )
        )

        return get_unissued_violations(10)

    # =========================================================
    # MAIN PIPELINE
    # =========================================================

    def run_generator(
        self,
        max_frames: Optional[int] = None,
    ) -> Generator[
        PipelineResult,
        None,
        None,
    ]:

        source_name = (
            self.source
            if self.source_type != "webcam"
            else "0"
        )

        self.session_id = create_session(
            self.camera_id,
            source_name,
            self.source_type,
        )

        logger.info(
            "Session %s started",
            self.session_id,
        )

        self._publish(
            {
                "type": "session_started",
                "camera_id": self.camera_id,
                "session_id": self.session_id,
                "source": source_name,
            }
        )

        t_start = time.time()
        self._start_safety_monitors()
        _fps_resolved = False

        try:
            for idx, frame in enumerate(
                self._iter_frames(),
                start=1,
            ):

                if self.stop_event.is_set():
                    break

                # -------------------------------------------------
                # FRAME SKIP
                # -------------------------------------------------

                if not _fps_resolved:
                    _fps_resolved = True
                    worker_fps = getattr(self.capture_worker, "source_fps", 0.0) if self.capture_worker else 0.0
                    if worker_fps:
                        self.source_fps = worker_fps
                    if self.accident_monitor is not None:
                        self.accident_monitor.fps = self.effective_fps
                    logger.info("Source FPS %.2f (effective %.2f with frame_skip=%s)",
                                self.source_fps, self.effective_fps, self.frame_skip)

                if idx % self.frame_skip != 0:
                    continue
                # -------------------------------------------------
                # RESIZE
                # -------------------------------------------------

                target_w = (
                    self.resize_width
                    or 960
                )

                if frame.shape[1] > target_w:

                    scale = (
                        target_w
                        / frame.shape[1]
                    )

                    frame = cv2.resize(
                        frame,
                        (
                            target_w,
                            int(
                                frame.shape[0]
                                * scale
                            ),
                        ),
                        interpolation=cv2.INTER_AREA,
                    )

                self.frame_count += 1

                if (
                    max_frames
                    and self.frame_count > max_frames
                ):
                    break

                tick = time.time()

                # =================================================
                # VEHICLE TRACKING
                # =================================================

                tracks = self.tracker.track(
                    frame
                )

                # =================================================
                # PERSON DETECTION
                #
                # IMPORTANT:
                # This is separate from vehicle tracking.
                #
                # This gives the triple-riding detector the
                # actual person bounding boxes.
                # =================================================

                person_boxes = (
                    self._detect_person_boxes(
                        frame
                    )
                )

                logger.debug(
                    "Frame %s: %s vehicles, %s persons",
                    self.frame_count,
                    len(tracks),
                    len(person_boxes),
                )

                latest_meta: Dict[
                    str,
                    dict
                ] = {}

                latest_violation = None

                # Rider association for the whole frame (one person ->
                # one two-wheeler) before per-vehicle rule checks.
                try:
                    self.rule_detector.set_frame_context(
                        [(tr.tracker_id, tr.vehicle_type, tr.bbox) for tr in tracks],
                        person_boxes,
                    )
                except Exception as exc:
                    logger.debug("Rider association failed: %s", exc)

                # Fog (own thread, newest frame only) + accident cues
                if self.fog_monitor is not None:
                    self.fog_monitor.submit(frame)
                self._handle_accidents(frame, tracks)

                # =================================================
                # PROCESS EVERY VEHICLE
                # =================================================

                for tr in tracks:

                    zone = (
                        self._zone_for_centroid(
                            tr.centroid
                        )
                    )

                    applicable_limit = float(
                        zone.speed_limit
                        if zone
                        else self.speed_limit
                    )

                    applicable_scale = float(
                        zone.pixel_to_meter_scale
                        if (
                            zone
                            and zone.pixel_to_meter_scale
                        )
                        else self.pixel_scale
                    )

                    # =================================================
                    # ANPR
                    # =================================================

                    plate_text = None
                    plate_conf = 0.0

                    already_known = (
                        self.tracker_to_plate.get(
                            tr.tracker_id
                        )
                    )

                    if self.npr and not already_known:

                        self._ocr_tick[
                            tr.tracker_id
                        ] = (
                            self._ocr_tick.get(
                                tr.tracker_id,
                                0,
                            )
                            + 1
                        )

                        if (
                            self._ocr_tick[
                                tr.tracker_id
                            ]
                            % 3
                            == 0
                        ):

                            # Stage 1: YOLOv8 plate localisation (plate_best_v2.pt)
                            # Stage 2: deblur/sharpen + EasyOCR on that crop.
                            # Falls back to the older cascade/heuristic path.
                            plate_text, plate_conf = None, 0.0
                            located = self.rule_detector.locate_plate(
                                frame,
                                tr.bbox,
                            )
                            if located is not None:
                                plate_text, plate_conf = (
                                    self.npr.read_plate_crop(
                                        frame,
                                        located[0],
                                    )
                                )
                            if not plate_text:
                                plate_text, plate_conf = (
                                    self.npr.read_plate(
                                        frame,
                                        tr.bbox,
                                    )
                                )

                    # =================================================
                    # DEMO PLATE
                    # =================================================

                    if (
                        not plate_text
                        and not already_known
                        and self.demo_plate_mode
                    ):

                        plate_text = (
                            _demo_plate_for(
                                tr.tracker_id
                            )
                        )

                        plate_conf = 0.0

                    if plate_text:

                        norm_plate = (
                            plate_text
                            .upper()
                            .strip()
                        )

                        self.tracker_to_plate[
                            tr.tracker_id
                        ] = norm_plate

                    confirmed_plate = (
                        plate_text
                        or self.tracker_to_plate.get(
                            tr.tracker_id
                        )
                    )

                    # =================================================
                    # POSITION HISTORY
                    # =================================================

                    if confirmed_plate:

                        norm = (
                            confirmed_plate
                            .upper()
                            .strip()
                        )

                        if (
                            norm
                            not in self.plate_histories
                            and tr.tracker_id
                            in self.track_histories
                        ):

                            self.plate_histories[
                                norm
                            ] = list(
                                self.track_histories[
                                    tr.tracker_id
                                ]
                            )

                        hist = (
                            self.plate_histories
                            .setdefault(
                                norm,
                                [],
                            )
                        )

                    else:

                        hist = (
                            self.track_histories
                            .setdefault(
                                tr.tracker_id,
                                [],
                            )
                        )

                    hist.append(
                        tr.centroid
                    )

                    hist[:] = hist[-12:]

                    positions = (
                        hist
                        if self.tracker.using_bytetrack
                        else (
                            self.tracker.get_recent_positions(
                                tr.tracker_id,
                                n=12,
                            )
                            or hist
                        )
                    )

                    # =================================================
                    # SPEED
                    # =================================================

                    estimator = (
                        self._get_estimator(
                            tr.tracker_id,
                            applicable_scale,
                            self.effective_fps,
                            plate=confirmed_plate,
                        )
                    )

                    speed = (
                        estimator.estimate_speed(
                            positions,
                            vehicle_id=-1,
                        )
                        if len(positions) > 1
                        else None
                    )

                    # =================================================
                    # DATABASE VEHICLE
                    # =================================================

                    vehicle_id = upsert_vehicle(
                        self.session_id,
                        tr.tracker_id,
                        tr.vehicle_type,
                        confirmed_plate,
                        plate_conf
                        if plate_text
                        else None,
                    )

                    # =================================================
                    # DRIVESHIELDX RULE DETECTOR
                    #
                    # triple riding now gets REAL person boxes.
                    # =================================================

                    try:

                        new_rules = (
                            self.rule_detector.observe(
                                str(
                                    tr.tracker_id
                                ),
                                tr.vehicle_type,
                                frame,
                                tr.bbox,
                                person_boxes=person_boxes,
                            )
                        )

                        # -----------------------------------------
                        # Disagreement gate -> officer review queue
                        # (no challan, no notice for these events)
                        # -----------------------------------------
                        for rev in self.rule_detector.pop_reviews():
                            rev_snapshot = self._save_snapshot(
                                frame,
                                f"{tr.tracker_id}_review_{rev['rule']}",
                            )
                            insert_gate_review(
                                session_id=self.session_id,
                                camera_id=self.camera_id,
                                tracker_id=str(tr.tracker_id),
                                rule_type=rev["rule"],
                                p_standalone=rev["p_s"],
                                p_combined=rev["p_c"],
                                theta=rev["theta"],
                                snapshot_path=rev_snapshot,
                            )
                            log_system_event(
                                "review",
                                (
                                    f"{rev['rule']} withheld for officer review "
                                    f"(vehicle {confirmed_plate or tr.tracker_id}): "
                                    f"standalone={rev['p_s']}, combined={rev['p_c']}"
                                ),
                                level="info",
                                camera_id=self.camera_id,
                            )

                        for rule in new_rules:

                            snapshot = (
                                self._save_snapshot(
                                    frame,
                                    (
                                        f"{tr.tracker_id}_"
                                        f"{rule}"
                                    ),
                                )
                            )

                            insert_rule_violation(
                                session_id=self.session_id,
                                camera_id=self.camera_id,
                                tracker_id=str(
                                    tr.tracker_id
                                ),
                                rule_type=rule,
                                plate_text=confirmed_plate,
                                snapshot_path=snapshot,
                            )

                            self.rule_violation_count += 1

                            log_system_event(
                                "rule",
                                (
                                    f"{rule} confirmed for "
                                    f"vehicle "
                                    f"{confirmed_plate or tr.tracker_id}"
                                ),
                                level="warning",
                                camera_id=self.camera_id,
                            )

                            # -------------------------------------------------
                            # Publish rule violation
                            # -------------------------------------------------

                            self._publish(
                                {
                                    "type": "rule_violation",
                                    "camera_id": self.camera_id,
                                    "session_id": self.session_id,
                                    "tracker_id": str(
                                        tr.tracker_id
                                    ),
                                    "rule_type": rule,
                                    "plate_text": confirmed_plate,
                                }
                            )

                            # -------------------------------------------------
                            # Alerts
                            # -------------------------------------------------

                            rule_label = (
                                rule.replace(
                                    "_",
                                    " ",
                                )
                            )

                            sms_body = (
                                f"DriveShieldX: "
                                f"{rule_label} detected "
                                f"for vehicle "
                                f"{confirmed_plate or f'V{tr.tracker_id}'} "
                                f"at camera "
                                f"{self.camera_id}."
                            )

                            try:

                                send_sms_alert(
                                    confirmed_plate
                                    or f"track-{tr.tracker_id}",
                                    sms_body,
                                )

                            except Exception:
                                pass

                            try:

                                send_email_alert(
                                    "traffic-authority@local",
                                    (
                                        "DriveShieldX "
                                        f"{rule_label} violation"
                                    ),
                                    sms_body,
                                )

                            except Exception:
                                pass

                            latest_meta[
                                tr.tracker_id
                            ] = {
                                "plate_text": confirmed_plate,
                                "rule": rule,
                            }

                    except Exception:

                        logger.exception(
                            "Rule detector failed"
                        )

                    # =================================================
                    # SPEED VIOLATION
                    # =================================================

                    if (
                        speed is not None
                        and speed > self.max_plausible_kmh
                    ):
                        # Physically implausible (ID switch / scale error):
                        # never recorded as a speed or a violation.
                        self.implausible_speeds += 1
                        speed = None

                    if speed is not None:
                        speed_id = (
                            insert_speed_record(
                                vehicle_id,
                                speed,
                                applicable_limit,
                                zone_id=(
                                    zone.zone_id
                                    if zone
                                    else None
                                ),
                                centroid=tr.centroid,
                                source_fps=self.effective_fps,
                            )
                        )

                        severity = None

                        vio_key = (
                            confirmed_plate.upper()
                            if confirmed_plate
                            else (
                                self.session_id,
                                tr.tracker_id,
                            )
                        )

                        if (
                            speed
                            > applicable_limit
                            and vio_key
                            not in self.logged_violations
                        ):

                            snapshot = (
                                self._save_snapshot(
                                    frame,
                                    tr.tracker_id,
                                )
                            )

                            vio_id = (
                                insert_violation(
                                    speed_id,
                                    speed,
                                    applicable_limit,
                                    snapshot,
                                )
                            )

                            self.logged_violations.add(
                                vio_key
                            )

                            self.violation_count += 1

                            excess = (
                                speed
                                - applicable_limit
                            )

                            if excess > 40:
                                severity = "extreme"

                            elif excess > 25:
                                severity = "high"

                            elif excess > 10:
                                severity = "medium"

                            else:
                                severity = "low"

                            latest_violation = {
                                "violation_id": vio_id,
                                "vehicle_id": vehicle_id,
                                "tracker_id": tr.tracker_id,
                                "speed": speed,
                                "severity": severity,
                                "plate_text": confirmed_plate,
                                "snapshot": snapshot,
                            }

                            subject = (
                                "DriveShieldX violation detected: "
                                f"{confirmed_plate or f'V{vehicle_id}'}"
                            )

                            body = (
                                f"Vehicle "
                                f"{confirmed_plate or vehicle_id} "
                                f"detected at "
                                f"{speed:.1f} km/h "
                                f"(limit "
                                f"{applicable_limit:.1f}) "
                                f"at camera "
                                f"{self.camera_id}."
                            )

                            try:

                                send_email_alert(
                                    "traffic-authority@local",
                                    subject,
                                    body,
                                )

                            except Exception:
                                pass

                            try:

                                send_sms_alert(
                                    "authority-device",
                                    body,
                                )

                            except Exception:
                                pass

                            self._publish(
                                {
                                    "type": "violation",
                                    "camera_id": self.camera_id,
                                    "session_id": self.session_id,
                                    "violation_id": vio_id,
                                    "speed": speed,
                                    "limit": applicable_limit,
                                    "severity": severity,
                                    "plate_text": confirmed_plate,
                                }
                            )

                        latest_meta[
                            tr.tracker_id
                        ] = {
                            **latest_meta.get(
                                tr.tracker_id,
                                {},
                            ),
                            "speed": speed,
                            "severity": severity,
                            "plate_text": confirmed_plate,
                        }

                    else:

                        latest_meta[
                            tr.tracker_id
                        ] = {
                            **latest_meta.get(
                                tr.tracker_id,
                                {},
                            ),
                            "plate_text": confirmed_plate,
                        }

                # =================================================
                # FPS
                # =================================================

                elapsed = (
                    time.time()
                    - tick
                )

                self.last_fps = (
                    1.0 / elapsed
                    if elapsed > 0
                    else 0.0
                )

                # =================================================
                # DATABASE STATUS
                # =================================================

                update_session_progress(
                    self.session_id,
                    self.frame_count,
                )

                upsert_stream_status(
                    self.camera_id,
                    True,
                    self.frame_count,
                    self.last_fps,
                    elapsed * 1000.0,
                )

                # =================================================
                # ANNOTATED FRAME
                # =================================================

                annotated = (
                    self._draw_annotations(
                        frame,
                        tracks,
                        {
                            k: v.get("speed")
                            for k, v
                            in latest_meta.items()
                            if v.get("speed")
                            is not None
                        },
                        latest_meta,
                        person_boxes=person_boxes,
                    )
                )

                # =================================================
                # DASHBOARD DATA
                # =================================================

                latest_records = (
                    get_unissued_violations(8)
                )

                latest_events = (
                    get_system_events(8)
                )

                # =================================================
                # FRAME EVENT
                # =================================================

                self._publish(
                    {
                        "type": "frame",
                        "camera_id": self.camera_id,
                        "session_id": self.session_id,
                        "frame_index": self.frame_count,
                        "tracked": len(tracks),
                        "persons": len(person_boxes),
                        "violations": self.violation_count,
                        "rule_violations": self.rule_violation_count,
                        "fps": self.last_fps,
                    }
                )

                yield PipelineResult(
                    self.frame_count,
                    len(tracks),
                    self.violation_count,
                    self.last_fps,
                    annotated,
                    latest_records,
                    latest_events,
                    latest_violation,
                )

            # =====================================================
            # COMPLETED
            # =====================================================

            close_session(
                self.session_id,
                "completed",
                notes=(
                    f"Completed in "
                    f"{time.time() - t_start:.1f}s; "
                    f"source_fps={self.source_fps:.2f}; "
                    f"implausible_speeds_rejected={self.implausible_speeds}; "
                    f"accidents={self.accident_count}"
                ),
            )

            upsert_stream_status(
                self.camera_id,
                False,
                self.frame_count,
                self.last_fps,
                0.0,
            )

            self._publish(
                {
                    "type": "session_completed",
                    "camera_id": self.camera_id,
                    "session_id": self.session_id,
                    "frames": self.frame_count,
                }
            )

        except Exception as exc:

            logger.exception(
                "Pipeline failed"
            )

            if self.session_id:

                close_session(
                    self.session_id,
                    "error",
                    notes=str(exc),
                )

            upsert_stream_status(
                self.camera_id,
                False,
                self.frame_count,
                self.last_fps,
                0.0,
                str(exc),
            )

            log_system_event(
                "monitor",
                (
                    "Monitoring stopped due "
                    f"to error: {exc}"
                ),
                level="error",
                camera_id=self.camera_id,
            )

            self._publish(
                {
                    "type": "error",
                    "camera_id": self.camera_id,
                    "session_id": self.session_id,
                    "error": str(exc),
                }
            )

            raise

        finally:

            self._stop_safety_monitors()
