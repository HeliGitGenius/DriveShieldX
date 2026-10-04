"""
Speed Estimation Module
Converts pixel displacement between frames to real-world speed (km/h).

Formula:
    pixel_distance = Euclidean distance between centroid positions
    real_distance_m = pixel_distance * pixel_to_meter_scale
    time_seconds = frame_count_diff / FPS
    speed_kmh = (real_distance_m / time_seconds) * 3.6
"""

import numpy as np
from typing import List, Tuple, Optional


class SpeedEstimator:
    """
    Estimates vehicle speed using centroid displacement and calibration.
    """

    def __init__(self, fps: float = 25.0, pixel_to_meter: float = 0.045, smoothing_window: int = 5):
        """
        Args:
            fps: Video frames per second (UA-DETRAC = 25 FPS)
            pixel_to_meter: Calibration factor (pixels → meters)
                            Derived from known road width: lane_width_m / lane_width_pixels
                            UA-DETRAC highway: ~3.5m lane / ~78 pixels ≈ 0.045 m/px
            smoothing_window: Number of recent positions to average over for stability
        """
        self.fps = fps
        self.pixel_to_meter = pixel_to_meter
        self.smoothing_window = smoothing_window
        self._speed_history = {}   # vehicle_id -> [speed values]

    def set_calibration(self, pixel_to_meter: float):
        self.pixel_to_meter = pixel_to_meter

    def estimate_speed(
        self,
        positions: List[Tuple[float, float]],
        vehicle_id: int = -1
    ) -> Optional[float]:
        """
        Estimate speed from recent centroid positions.

        Args:
            positions: List of (cx, cy) — last N positions of a vehicle
            vehicle_id: For internal smoothing history

        Returns:
            Speed in km/h, or None if insufficient data
        """
        if len(positions) < 2:
            return None

        # Use last `smoothing_window` positions
        recent = positions[-self.smoothing_window:]

        total_pixel_dist = 0.0
        for i in range(1, len(recent)):
            dx = recent[i][0] - recent[i-1][0]
            dy = recent[i][1] - recent[i-1][1]
            total_pixel_dist += np.sqrt(dx**2 + dy**2)

        frame_intervals = len(recent) - 1
        time_seconds = frame_intervals / self.fps
        if time_seconds <= 0:
            return None

        real_dist_m = total_pixel_dist * self.pixel_to_meter
        speed_ms = real_dist_m / time_seconds
        speed_kmh = speed_ms * 3.6

        # Smooth via moving average
        if vehicle_id >= 0:
            if vehicle_id not in self._speed_history:
                self._speed_history[vehicle_id] = []
            self._speed_history[vehicle_id].append(speed_kmh)
            # Keep only last 10 readings
            self._speed_history[vehicle_id] = self._speed_history[vehicle_id][-10:]
            speed_kmh = float(np.mean(self._speed_history[vehicle_id]))

        return round(speed_kmh, 2)

    def is_overspeeding(self, speed_kmh: float, speed_limit: float) -> bool:
        return speed_kmh > speed_limit

    def get_speed_history(self, vehicle_id: int) -> List[float]:
        return self._speed_history.get(vehicle_id, [])


class CalibrationHelper:
    """
    Helps compute pixel_to_meter from known road distances.
    For UA-DETRAC: measure lane markings in pixels, compare to standard lane width.
    """

    STANDARD_LANE_WIDTH_M = 3.5   # meters (standard highway lane)

    @staticmethod
    def compute_scale(lane_width_pixels: float, lane_width_m: float = None) -> float:
        """
        Compute pixel_to_meter scale from lane measurement.

        Args:
            lane_width_pixels: Measured pixel width of a lane in the video
            lane_width_m: Real-world width (default: 3.5m)

        Returns:
            pixel_to_meter scale factor
        """
        if lane_width_m is None:
            lane_width_m = CalibrationHelper.STANDARD_LANE_WIDTH_M
        return lane_width_m / lane_width_pixels

    @staticmethod
    def describe(scale: float) -> str:
        return f"1 pixel ≈ {scale:.4f} meters | 1 meter ≈ {1/scale:.1f} pixels"
