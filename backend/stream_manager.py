from __future__ import annotations

import threading
from typing import Dict, Optional

from backend.event_bus import BUS
from backend.logger import get_logger
from detection.advanced_pipeline import AdvancedOverspeedPipeline

logger = get_logger("stream_manager")


class CameraWorker(threading.Thread):
    def __init__(self, camera_id: int, source: str, source_type: str = "rtsp", **kwargs):
        super().__init__(daemon=True)
        self.camera_id = camera_id
        self.source = source
        self.source_type = source_type
        self.kwargs = kwargs
        self._stop_event = threading.Event()
        self.pipeline: Optional[AdvancedOverspeedPipeline] = None

    def stop(self) -> None:
        self._stop_event.set()
        if self.pipeline:
            self.pipeline.request_stop()

    def run(self) -> None:
        try:
            self.pipeline = AdvancedOverspeedPipeline(camera_id=self.camera_id, source=self.source, source_type=self.source_type, event_callback=BUS.publish, **self.kwargs)
            self.pipeline.process(max_frames=self.kwargs.get("max_frames"))
        except Exception as exc:
            logger.exception("Camera worker failed")
            BUS.publish({"type": "worker_error", "camera_id": self.camera_id, "error": str(exc)})


class MultiCameraManager:
    def __init__(self) -> None:
        self.workers: Dict[int, CameraWorker] = {}

    def start_camera(self, camera_id: int, source: str, source_type: str = "rtsp", **kwargs) -> None:
        if camera_id in self.workers and self.workers[camera_id].is_alive():
            return
        worker = CameraWorker(camera_id=camera_id, source=source, source_type=source_type, **kwargs)
        self.workers[camera_id] = worker
        worker.start()

    def stop_camera(self, camera_id: int) -> None:
        worker = self.workers.get(camera_id)
        if worker:
            worker.stop()

    def stop_all(self) -> None:
        for cid in list(self.workers):
            self.stop_camera(cid)


MANAGER = MultiCameraManager()
