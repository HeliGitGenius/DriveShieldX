"""Fog / visibility monitoring (paper Sec. 4.5, Fig. 1 "Fog Detection ->
Road-Safety Zone Alert").

Per-frame measurement (road region = lower 2/3 of the frame, so sky does not
dominate):
  dark_channel  mean of the dark channel (He et al., CVPR 2009): haze raises the
                minimum RGB intensity in local patches.
  contrast      RMS contrast = std(gray)/mean(gray); haze flattens it.
  edge_density  fraction of Canny edge pixels; haze removes fine edges.
  brightness    mean luminance; very dark frames are reported as 'low_light'
                instead of being mistaken for clear or foggy.

Absolute metrics differ a lot between cameras (a flat grey car park has almost
no edges even on a clear day), so the score is computed RELATIVE to each
camera's own clear-weather baseline (rolling robust statistics, persisted in
FOG_BASELINE, or set with "calibrate now" in the dashboard):

  rel_dark     = (dark - base_dark) / (1 - base_dark) / 0.5          (clipped 0..1)
  rel_contrast = contrast / base_contrast                             (clipped 0..1)
  rel_edge     = edge_density / base_edge                             (clipped 0..1)
  fog_score    = 0.40 rel_dark + 0.35 (1 - rel_contrast) + 0.25 (1 - rel_edge)
  visibility_index = 100 * (1 - fog_score)

Until a baseline exists (first readings of a new camera) an absolute score with
generic normalisation ranges is used instead.

Level thresholds were derived from the atmospheric scattering model
I = J t + A (1 - t) applied to real project frames (tests/test_fog.py): the
relative score is ~0.22 at t = 0.85, ~0.43 at t = 0.70, ~0.67 at t = 0.50 and
~0.82 at t = 0.35 on every camera tried. With t = exp(-beta d) at a 50 m
reference depth and meteorological visibility V = 3.912 / beta this gives
  clear < 0.30 (V > ~1 km) | light_haze < 0.55 | moderate_fog < 0.75 (V ~250-450 m)
  | dense_fog (V < ~250 m).
These are model-derived defaults; validate on real labelled fog footage with
evaluation/fog_eval.py before relying on them.

The monitor runs on its own thread and only ever looks at the newest frame, so
it never adds latency to the violation path. Readings are smoothed (EMA) and a
zone advisory is raised only after `on_hits` consecutive moderate/dense
readings, and cleared after `off_hits` consecutive clear/light readings
(hysteresis, no flicker).
"""
from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import cv2
import numpy as np

LEVELS = ("clear", "light_haze", "moderate_fog", "dense_fog")


@dataclass
class FogConfig:
    thresholds: tuple = (
        float(os.environ.get("DSX_FOG_T1", 0.30)),
        float(os.environ.get("DSX_FOG_T2", 0.55)),
        float(os.environ.get("DSX_FOG_T3", 0.75)),
    )
    weights: tuple = (0.40, 0.35, 0.25)          # dark channel, (1-contrast), (1-edges)
    dark_range: tuple = (0.05, 0.55)            # absolute-mode normalisation ranges
    contrast_range: tuple = (0.10, 0.60)
    edge_range: tuple = (0.01, 0.12)
    baseline_min_readings: int = 20
    baseline_window: int = 300
    low_light_brightness: float = 0.18          # mean luminance (0..1)
    patch: int = 15
    work_width: int = 320
    ema_alpha: float = 0.3
    interval_s: float = 1.0
    on_hits: int = 3
    off_hits: int = 5
    advisory_factor: Dict[str, float] = None    # fraction of the zone limit

    def __post_init__(self):
        if self.advisory_factor is None:
            self.advisory_factor = {"moderate_fog": 0.75, "dense_fog": 0.5}


def _norm(v: float, lo: float, hi: float) -> float:
    return float(np.clip((v - lo) / (hi - lo), 0.0, 1.0))


def relative_score(m: Dict[str, float], base: Dict[str, float], cfg: FogConfig = FogConfig()) -> float:
    wd, wc, we = cfg.weights
    rd = np.clip((m["dark_channel"] - base["dark_channel"]) / max(1e-3, 1 - base["dark_channel"]) / 0.5, 0, 1)
    rc = np.clip(m["contrast"] / max(1e-4, base["contrast"]), 0, 1)
    re = np.clip(m["edge_density"] / max(1e-4, base["edge_density"]), 0, 1)
    return float(wd * rd + wc * (1 - rc) + we * (1 - re))


def measure(frame: np.ndarray, cfg: FogConfig = FogConfig(), baseline: Optional[Dict[str, float]] = None) -> Dict[str, float | str]:
    """Visibility measurement for one BGR frame."""
    h, w = frame.shape[:2]
    s = cfg.work_width / float(w)
    small = cv2.resize(frame, (cfg.work_width, max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    road = small[small.shape[0] // 3:, :]
    img = road.astype(np.float32) / 255.0
    dark = cv2.erode(img.min(axis=2), np.ones((cfg.patch, cfg.patch), np.uint8))
    dark_channel = float(dark.mean())
    gray = cv2.cvtColor(road, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean()) / 255.0
    contrast = float(gray.std()) / max(1.0, float(gray.mean()))
    edges = cv2.Canny(gray, 50, 150)
    edge_density = float((edges > 0).mean())
    raw = {"dark_channel": dark_channel, "contrast": contrast, "edge_density": edge_density}
    if baseline:
        fog_score, mode = relative_score(raw, baseline, cfg), "relative"
    else:
        wd, wc, we = cfg.weights
        fog_score = (wd * _norm(dark_channel, *cfg.dark_range)
                     + wc * (1 - _norm(contrast, *cfg.contrast_range))
                     + we * (1 - _norm(edge_density, *cfg.edge_range)))
        mode = "absolute"
    return {"fog_score": round(fog_score, 4), "visibility_index": round(100 * (1 - fog_score), 1),
            "dark_channel": round(dark_channel, 4), "contrast": round(contrast, 4),
            "edge_density": round(edge_density, 4), "brightness": round(brightness, 4),
            "mode": mode, "level": classify(fog_score, brightness, cfg)}


def classify(fog_score: float, brightness: float, cfg: FogConfig = FogConfig()) -> str:
    if brightness < cfg.low_light_brightness:
        return "low_light"
    t1, t2, t3 = cfg.thresholds
    if fog_score < t1:
        return "clear"
    if fog_score < t2:
        return "light_haze"
    if fog_score < t3:
        return "moderate_fog"
    return "dense_fog"


def add_synthetic_fog(frame: np.ndarray, transmission: float, airlight: float = 0.9) -> np.ndarray:
    """Koschmieder / atmospheric scattering model I = J t + A (1 - t). Used by tests."""
    j = frame.astype(np.float32) / 255.0
    out = j * transmission + airlight * (1 - transmission)
    return np.clip(out * 255, 0, 255).astype(np.uint8)


class FogMonitor:
    """Background visibility monitor for one camera."""

    def __init__(self, camera_id: int, speed_limit: float, on_event: Optional[Callable[[str, dict], None]] = None,
                 cfg: FogConfig = FogConfig(), baseline: Optional[Dict[str, float]] = None,
                 on_baseline: Optional[Callable[[Dict[str, float]], None]] = None) -> None:
        self.camera_id = camera_id
        self.speed_limit = speed_limit
        self.cfg = cfg
        self.on_event = on_event
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.ema: Optional[float] = None
        self.latest: Optional[dict] = None
        self.advisory_active = False
        self.advisory_level: Optional[str] = None
        self._bad = 0
        self._good = 0
        self.readings = 0
        self.baseline: Optional[Dict[str, float]] = baseline
        self.on_baseline = on_baseline
        self._history: list = []   # raw metrics of non-foggy readings, for the rolling baseline

    # -- threading
    def start(self) -> "FogMonitor":
        self._thread = threading.Thread(target=self._run, name=f"fog-cam{self.camera_id}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def submit(self, frame: np.ndarray) -> None:
        """Non-blocking: keep only the newest frame."""
        try:
            if self._q.full():
                self._q.get_nowait()
            self._q.put_nowait(frame)
        except (queue.Full, queue.Empty):
            pass

    def _run(self) -> None:
        last = 0.0
        while not self._stop.is_set():
            try:
                frame = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            now = time.time()
            if now - last < self.cfg.interval_s:
                continue
            last = now
            try:
                self.update(measure(frame, self.cfg, self.baseline))
            except Exception as exc:  # never let the monitor kill the pipeline
                print(f"[FogMonitor] {exc}")

    # -- state machine (pure, unit-tested)
    def update(self, reading: dict) -> Optional[str]:
        self.readings += 1
        a = self.cfg.ema_alpha
        self.ema = reading["fog_score"] if self.ema is None else a * reading["fog_score"] + (1 - a) * self.ema
        level = classify(self.ema, reading["brightness"], self.cfg)
        calibrating = reading.get("mode") == "absolute"
        if calibrating and level != "low_light":
            # No clear-weather baseline yet: absolute scores are camera-dependent
            # (a flat car park looks 'foggy'), so report but never raise advisories.
            level = "calibrating"
        reading = {**reading, "smoothed_score": round(self.ema, 4), "level": level}
        self.latest = reading
        if calibrating:
            if reading["fog_score"] < 0.95 and level != "low_light":  # skip only near-white-out frames
                self._learn_baseline({**reading, "level": "clear"}, bad=False)
            return None
        bad = level in ("moderate_fog", "dense_fog")
        self._bad = self._bad + 1 if bad else 0
        self._good = self._good + 1 if not bad else 0
        event = None
        if bad and self._bad >= self.cfg.on_hits and (not self.advisory_active or level != self.advisory_level):
            self.advisory_active, self.advisory_level = True, level
            event = "advisory_on"
        elif self.advisory_active and self._good >= self.cfg.off_hits:
            self.advisory_active, self.advisory_level = False, None
            event = "advisory_off"
        if event and self.on_event:
            self.on_event(event, {**reading, "advisory_speed": self.advisory_speed()})
        self._learn_baseline(reading, bad)
        return event

    def _learn_baseline(self, reading: dict, bad: bool) -> None:
        """Rolling clear-weather baseline: robust percentiles of recent non-foggy,
        non-dark readings (dark channel 20th pct, contrast/edges 80th pct)."""
        if bad or reading["level"] == "low_light":
            return
        self._history.append((reading["dark_channel"], reading["contrast"], reading["edge_density"]))
        self._history = self._history[-self.cfg.baseline_window:]
        n = len(self._history)
        if n >= self.cfg.baseline_min_readings and (self.baseline is None or n % 60 == 0):
            arr = np.asarray(self._history)
            self.set_baseline({"dark_channel": float(np.percentile(arr[:, 0], 20)),
                               "contrast": float(np.percentile(arr[:, 1], 80)),
                               "edge_density": float(np.percentile(arr[:, 2], 80))})

    def set_baseline(self, base: Dict[str, float]) -> None:
        self.baseline = base
        if self.on_baseline:
            try:
                self.on_baseline(base)
            except Exception as exc:
                print(f"[FogMonitor] baseline persist failed: {exc}")
        self.ema = None  # restart smoothing on the relative scale

    def calibrate(self, frame: np.ndarray) -> Dict[str, float]:
        """Operator action: 'this view is clear weather' -> baseline from this frame."""
        m = measure(frame, self.cfg)
        base = {k: m[k] for k in ("dark_channel", "contrast", "edge_density")}
        self.set_baseline(base)
        self.ema = None
        return base

    def advisory_speed(self) -> Optional[float]:
        if not self.advisory_active or self.advisory_level not in self.cfg.advisory_factor:
            return None
        return float(5 * round(self.speed_limit * self.cfg.advisory_factor[self.advisory_level] / 5))
