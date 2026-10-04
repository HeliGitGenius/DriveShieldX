"""Learned crash detector: a video model that looks at the whole picture.

The rule-based AccidentMonitor needs stable vehicle tracks, which low-resolution
CCTV (e.g. 320x240) does not give. This model instead classifies short windows of
video as crash / no crash:

  frames resampled to FPS (8), resized to SIZE (128x171), a window of CLIP_LEN (16)
  frames = 2 s, centre-cropped to 112, fed to R3D-18 (Kinetics-400 pretrained,
  fine-tuned on labelled crash windows; training/train_accident_model.py).

An alert is raised when the crash probability is >= threshold for `consecutive`
windows in a row (a window every STEP frames = 0.5 s); then a cooldown applies.
The threshold is chosen on validation videos during training and stored in the
checkpoint. Only used when a checkpoint exists (DSX_ACCIDENT_MODEL).
"""
from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional

import cv2
import numpy as np

FPS = 8.0
SIZE = (128, 171)          # (height, width) after resize
CROP = 112
CLIP_LEN = 16
STEP = 4                   # frames between windows (0.5 s at 8 fps)
MEAN = np.array([0.43216, 0.394666, 0.37645], np.float32)   # Kinetics normalisation (torchvision)
STD = np.array([0.22803, 0.22145, 0.216989], np.float32)


def resize_frame(img: np.ndarray) -> np.ndarray:
    """BGR frame -> RGB uint8 at SIZE."""
    return cv2.resize(img, (SIZE[1], SIZE[0]), interpolation=cv2.INTER_AREA)[:, :, ::-1]


def to_tensor_batch(clips: np.ndarray, crop: str = "center", rng: Optional[np.random.Generator] = None):
    """clips: (N, T, H, W, 3) uint8 RGB -> torch float (N, 3, T, CROP, CROP), normalised."""
    import torch
    n, t, h, w, _ = clips.shape
    if crop == "random":
        rng = rng or np.random.default_rng()
        y, x = int(rng.integers(0, h - CROP + 1)), int(rng.integers(0, w - CROP + 1))
    else:
        y, x = (h - CROP) // 2, (w - CROP) // 2
    c = clips[:, :, y:y + CROP, x:x + CROP, :].astype(np.float32) / 255.0
    c = (c - MEAN) / STD
    return torch.from_numpy(np.ascontiguousarray(c.transpose(0, 4, 1, 2, 3)))


def build_model(pretrained: bool = True):
    import torch.nn as nn
    from torchvision.models.video import R3D_18_Weights, r3d_18
    m = r3d_18(weights=R3D_18_Weights.KINETICS400_V1 if pretrained else None)
    m.fc = nn.Linear(m.fc.in_features, 2)
    return m


def resample_video(path: str, start_s: float = 0.0, end_s: Optional[float] = None) -> np.ndarray:
    """Read a video (or the window start_s..end_s) at FPS, resized. Returns (N, H, W, 3) uint8 RGB."""
    cap = cv2.VideoCapture(path)
    src = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if start_s:
        cap.set(cv2.CAP_PROP_POS_MSEC, start_s * 1000)
    frames, i, next_t = [], 0, 0.0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        t = i / src
        i += 1
        if end_s is not None and start_s + t >= end_s:
            break
        if t + 1e-6 >= next_t:
            frames.append(resize_frame(img))
            next_t += 1.0 / FPS
    cap.release()
    return np.stack(frames) if frames else np.zeros((0, SIZE[0], SIZE[1], 3), np.uint8)


def window_starts(n_frames: int) -> List[int]:
    return list(range(0, max(0, n_frames - CLIP_LEN + 1), STEP))


def events_from_probs(probs: List[float], times: List[float], threshold: float, consecutive: int,
                      cooldown_s: float = 60.0) -> List[float]:
    """Alert times: prob >= threshold for `consecutive` windows in a row, then cooldown."""
    out, run, quiet_until = [], 0, -1e9
    for p, t in zip(probs, times):
        run = run + 1 if p >= threshold else 0
        if run >= consecutive and t >= quiet_until:
            out.append(t)
            quiet_until = t + cooldown_s
            run = 0
    return out


class AccidentVideoClassifier:
    def __init__(self, checkpoint: str, device: Optional[str] = None) -> None:
        import torch
        ck = torch.load(checkpoint, map_location="cpu", weights_only=False)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = build_model(pretrained=False)
        self.model.load_state_dict({k: (v.float() if v.is_floating_point() else v) for k, v in ck["state_dict"].items()})
        self.model.eval().to(self.device)
        self.threshold = float(ck.get("threshold", 0.5))
        self.consecutive = int(ck.get("consecutive", 2))
        self.cooldown_s = float(ck.get("cooldown_s", 60.0))
        self.info = {k: v for k, v in ck.items() if k != "state_dict"}
        # live use
        self._buf: Deque[np.ndarray] = deque(maxlen=CLIP_LEN)
        self._next_t = 0.0
        self._since = 0
        self._run = 0
        self._quiet_until = -1e9

    def probs(self, clips: np.ndarray, batch: int = 16) -> List[float]:
        import torch
        out = []
        with torch.no_grad():
            for i in range(0, len(clips), batch):
                x = to_tensor_batch(clips[i:i + batch]).to(self.device)
                out += torch.softmax(self.model(x), 1)[:, 1].float().cpu().tolist()
        return out

    def score_frames(self, frames: np.ndarray, t0: float = 0.0):
        """frames at FPS -> (window end times in seconds, crash probabilities)."""
        starts = window_starts(len(frames))
        if not starts:
            return [], []
        clips = np.stack([frames[s:s + CLIP_LEN] for s in starts])
        return [t0 + (s + CLIP_LEN) / FPS for s in starts], self.probs(clips)

    def detect_video(self, path: str, start_s: float = 0.0, end_s: Optional[float] = None) -> List[float]:
        times, probs = self.score_frames(resample_video(path, start_s, end_s), start_s)
        return events_from_probs(probs, times, self.threshold, self.consecutive, self.cooldown_s)

    def update(self, frame_bgr: np.ndarray, t_s: float) -> bool:
        """Live: feed every frame with its timestamp; True when an alert should be raised."""
        if t_s + 1e-6 < self._next_t:
            return False
        self._next_t = t_s + 1.0 / FPS
        self._buf.append(resize_frame(frame_bgr))
        self._since += 1
        if len(self._buf) < CLIP_LEN or self._since < STEP:
            return False
        self._since = 0
        p = self.probs(np.stack(self._buf)[None])[0]
        self._run = self._run + 1 if p >= self.threshold else 0
        if self._run >= self.consecutive and t_s >= self._quiet_until:
            self._run = 0
            self._quiet_until = t_s + self.cooldown_s
            return True
        return False
