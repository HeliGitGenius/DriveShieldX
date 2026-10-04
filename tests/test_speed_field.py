"""Speed field test core: a vehicle at a known speed is measured correctly (no video needed)."""
import numpy as np

from evaluation.speed_field_test import analyse_track


def _track(kmh, fps=30.0, px_per_m=100.0, x0=100.0, frames=120):
    v_px = kmh / 3.6 * px_per_m / fps
    return [(f, x0 + v_px * f, 500 + np.sin(f) * 0.5) for f in range(frames)]


def test_known_speeds_are_recovered():
    a, b = (300.0, 500.0), (1300.0, 500.0)          # 1000 px = 10 m
    for kmh in (20, 30, 40):
        r = analyse_track(_track(kmh, frames=200), 30.0, a, b, 10.0)
        assert abs(r["gate_kmh"] - kmh) < 0.5
        assert abs(r["estimator_kmh"] - kmh) < 1.0
        assert r["estimator_readings"] > 5


def test_vehicle_moving_right_to_left_and_slanted_line():
    a, b = (1300.0, 520.0), (300.0, 480.0)
    pts = [(f, 1500 - 9.0 * f, 528 - 0.36 * f) for f in range(160)]
    r = analyse_track(pts, 30.0, a, b, 10.0)
    assert r["gate_kmh"] is not None and abs(r["gate_kmh"] - r["estimator_kmh"]) < 1.5


def test_vehicle_never_reaching_marker_b_has_no_gate_speed():
    r = analyse_track([(f, 100 + 2 * f, 500) for f in range(100)], 30.0, (300, 500), (1300, 500), 10.0)
    assert r["gate_kmh"] is None
