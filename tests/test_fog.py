"""Fog monitor: response to the atmospheric scattering model on real project
frames, relative scoring, hysteresis and low-light handling. No weights needed."""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detection.fog_monitor import FogConfig, FogMonitor, add_synthetic_fog, measure  # noqa: E402

SNAPS = sorted((ROOT / "snapshots").glob("*.jpg"))


def frames():
    if SNAPS:
        return [cv2.imread(str(p)) for p in SNAPS[:: max(1, len(SNAPS) // 5)][:5]]
    rng = np.random.default_rng(0)
    return [(rng.random((540, 960, 3)) * 255).astype(np.uint8)]


@pytest.mark.parametrize("idx", range(5))
def test_score_increases_with_fog(idx):
    fs = frames()
    f = fs[idx % len(fs)]
    base_m = measure(f)
    base = {k: base_m[k] for k in ("dark_channel", "contrast", "edge_density")}
    scores = [measure(add_synthetic_fog(f, t) if t < 1 else f, baseline=base)["fog_score"]
              for t in (1.0, 0.85, 0.7, 0.5, 0.35, 0.2)]
    assert all(b >= a - 1e-6 for a, b in zip(scores, scores[1:])), scores
    assert scores[0] < 0.1 and scores[-1] > 0.8


def test_levels_follow_transmission():
    f = frames()[0]
    m = measure(f)
    base = {k: m[k] for k in ("dark_channel", "contrast", "edge_density")}
    assert measure(f, baseline=base)["level"] in ("clear", "low_light")
    assert measure(add_synthetic_fog(f, 0.3), baseline=base)["level"] == "dense_fog"


def test_hysteresis_and_advisory():
    events = []
    mon = FogMonitor(1, 60.0, on_event=lambda e, r: events.append((e, r["advisory_speed"])),
                     baseline={"dark_channel": 0.1, "contrast": 0.5, "edge_density": 0.1})
    fog = {"fog_score": 0.9, "brightness": 0.5, "dark_channel": 0.6, "contrast": 0.1, "edge_density": 0.01,
           "visibility_index": 10}
    clear = {**fog, "fog_score": 0.05, "dark_channel": 0.1, "contrast": 0.5, "edge_density": 0.1}
    for _ in range(2):
        assert mon.update(fog) is None          # needs on_hits=3 consecutive readings
    for _ in range(4):
        mon.update(fog)
    assert events and events[0][0] == "advisory_on" and events[0][1] == 30.0
    for _ in range(10):
        mon.update(clear)
    assert events[-1][0] == "advisory_off"


def test_low_light_is_not_fog():
    dark = np.full((540, 960, 3), 15, np.uint8)
    assert measure(dark)["level"] == "low_light"


def test_no_advisory_while_calibrating_then_learns_baseline():
    events = []
    mon = FogMonitor(1, 60.0, on_event=lambda e, r: events.append(e))
    flat = {"fog_score": 0.7, "brightness": 0.5, "dark_channel": 0.3, "contrast": 0.2, "edge_density": 0.01,
            "visibility_index": 30, "mode": "absolute"}
    for _ in range(25):
        mon.update(flat)
    assert events == [] and mon.baseline is not None
    assert mon.latest["level"] == "calibrating"
