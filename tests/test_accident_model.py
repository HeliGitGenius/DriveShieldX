"""Crash-model plumbing: window labels, alarm logic, resampling, live update (no training)."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detection import accident_classifier as ac  # noqa: E402


def test_alarm_needs_consecutive_windows_and_respects_cooldown():
    times = [0.5 * i for i in range(20)]
    probs = [0.2, 0.9, 0.3, 0.9, 0.95, 0.96, 0.2] + [0.1] * 13
    assert ac.events_from_probs(probs, times, 0.8, 2, cooldown_s=60) == [2.0]
    assert ac.events_from_probs([0.9] * 400, [0.5 * i for i in range(400)], 0.8, 2, cooldown_s=60) == [0.5, 60.5, 120.5, 180.5]


def test_training_windows_positive_near_crash_ambiguous_dropped():
    sys.path.insert(0, str(ROOT / "training"))
    import train_accident_model as tm
    w = dict(tm.window_labels(160, 10.0, 12.0, ac, 2))      # 20 s at 8 fps, crash 10-12 s
    mid = lambda s: (s + ac.CLIP_LEN / 2) / ac.FPS
    assert all(9.5 <= mid(s) <= 13.0 for s, y in w.items() if y == 1) and any(y == 1 for y in w.values())
    assert all(mid(s) - 1 < 7.0 or mid(s) + 1 > 15.0 for s, y in w.items() if y == 0)
    assert all(y == 0 for _, y in tm.window_labels(64, None, None, ac, 2))


def test_resample_video_to_8fps(tmp_path):
    import cv2
    p = str(tmp_path / "v.mp4")
    w = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 240))
    for _ in range(90):
        w.write(np.zeros((240, 320, 3), np.uint8))
    w.release()
    fr = ac.resample_video(p)
    assert fr.shape[1:] == (128, 171, 3) and 23 <= len(fr) <= 25          # 3 s at 8 fps
    assert 7 <= len(ac.resample_video(p, 1.0, 2.0)) <= 9


def test_live_update_raises_one_alert():
    torch = pytest.importorskip("torch")

    class Always(torch.nn.Module):
        def forward(self, x):
            return torch.tensor([[0.0, 5.0]] * x.shape[0])

    clf = ac.AccidentVideoClassifier.__new__(ac.AccidentVideoClassifier)
    clf.model, clf.device, clf.threshold, clf.consecutive, clf.cooldown_s = Always(), "cpu", 0.5, 2, 60.0
    from collections import deque
    clf._buf, clf._next_t, clf._since, clf._run, clf._quiet_until = deque(maxlen=ac.CLIP_LEN), 0.0, 0, 0, -1e9
    frame = np.zeros((240, 320, 3), np.uint8)
    alerts = [t / 30 for t in range(30 * 10) if clf.update(frame, t / 30)]
    assert len(alerts) == 1 and 2.0 <= alerts[0] <= 3.5


def test_pipeline_dispatches_when_crash_model_fires(monkeypatch):
    """The live pipeline turns a crash-model alert into an accident record + dispatch."""
    from types import SimpleNamespace
    from detection.advanced_pipeline import AdvancedOverspeedPipeline
    from database import safety_db
    import backend.dispatch as dispatch
    calls = {}
    monkeypatch.setattr(safety_db, "insert_accident", lambda *a, **k: calls.setdefault("insert", a) and 7 or 7)
    monkeypatch.setattr(dispatch, "dispatch_accident", lambda *a, **k: calls.setdefault("dispatch", a) and {"sent": [1]} or {"sent": [1]})
    import detection.advanced_pipeline as ap
    monkeypatch.setattr(ap, "log_system_event", lambda *a, **k: None)

    class Fire:
        threshold, consecutive = 0.95, 2

        def update(self, frame, t):
            return True

    fake = SimpleNamespace(accident_classifier=Fire(), accident_monitor=None, frame_count=120, effective_fps=30.0,
                           camera_id=1, session_id=2, accident_count=0, _save_snapshot=lambda f, n: "snap.jpg",
                           _publish=lambda e: calls.setdefault("event", e))
    tracks = [SimpleNamespace(tracker_id=5, bbox=(0, 0, 10, 10))]
    AdvancedOverspeedPipeline._handle_accidents(fake, np.zeros((10, 10, 3), np.uint8), tracks)
    assert fake.accident_count == 1 and "dispatch" in calls
    assert calls["insert"][4]["detector"] == "learned R3D-18 crash model"
