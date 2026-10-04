"""Accident detection from tracked trajectories (paper Sec. 4.5, Fig. 1
"Accident Detection (Trajectory + Overlap Cues) -> Police + Hospital Dispatch").

It consumes the SAME per-frame tracks the violation path already produces
(no extra network), so it adds only a few microseconds per frame.

Motion is measured in box-heights per second, which makes the cues roughly
independent of perspective and camera resolution:
    v_t = || c_t - c_{t-k} || / (k * mean box height) * fps

Cues (all thresholds in AccidentConfig, tunable per camera):
  sudden_stop   : moving (>= v_move) in the window before, near-still (<= v_stop)
                  in the window after -- a hard deceleration.
  reversal      : heading changes by > reversal_deg while moving fast before
                  and after (spin-out / bounce-back).
  impact        : IoU with another vehicle jumps from < iou_before to >= iou_contact
                  within a few frames AND at least one of the pair decelerates.
  stalled       : still for >= stall_s while surrounding traffic keeps moving
                  (median speed of other tracks >= v_move) -- a vehicle stopped in
                  a moving lane. At a red light everyone stops, so it does not fire.

Scoring / confirmation:
  impact + (sudden_stop | reversal)  -> 0.9
  sudden_stop followed by stalled    -> 0.75
  reversal at speed                  -> 0.5 (candidate only)
  stalled for >= 2 * stall_s         -> 0.5 (candidate only)
An incident is CONFIRMED when its score >= confirm_score and the involved
vehicle(s) stay still for confirm_s seconds afterwards (a crash leaves stopped
vehicles; a hard brake that drives on is dropped). Each confirmed incident is
emitted once; the same tracks are then suppressed for cooldown_s.

ID-switch robustness (crashes occlude vehicles and the tracker often gives them new
IDs): an impact is remembered for impact_memory_s, so a stop or a vehicle lost from
tracking shortly after it still makes a candidate (0.85); and with spatial_confirm a
candidate is confirmed by any vehicle standing still at the crash spot, whatever its ID.

Thresholds are engineering defaults, not learned. Validate on labelled clips
with evaluation/accident_eval.py (event precision / recall / time-to-detect).
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class AccidentConfig:
    window_s: float = 0.6        # velocity window
    v_move: float = 0.8          # box-heights / s considered "moving"
    v_stop: float = 0.12         # box-heights / s considered "still"
    reversal_deg: float = 120.0
    iou_contact: float = 0.25
    iou_before: float = 0.05
    contact_window_s: float = 0.5
    stall_s: float = 4.0
    confirm_s: float = 2.0
    confirm_score: float = 0.75
    cooldown_s: float = 60.0
    history_s: float = 10.0
    min_box_h: float = 12.0      # ignore tiny, far-away boxes
    # Robustness to tracker ID switches (crashes cause occlusion and new IDs):
    # Defaults = the measured zero-false-alarm operating point (UCF-Crime test: 1/23 crashes,
    # 0 false alarms in 0.51 h). The recall-oriented point chosen on a separate dev set
    # (detector conf 0.20, impact_memory_s=1.5, iou_contact=0.15, v_move=0.5) found 3/23
    # but raised 11.8 false alarms per hour, so it is NOT the default for live dispatch.
    impact_memory_s: float = 0.0  # an impact still counts if a stop / vanish follows within this time
    spatial_confirm: bool = False # confirm on ANY still vehicle at the crash spot, not only the same IDs
    vanish_s: float = 0.4         # a track unseen this long right after an impact counts as "lost in the crash"


@dataclass
class _Track:
    pts: Deque[Tuple[int, float, float, float]] = field(default_factory=deque)   # frame, cx, cy, h
    boxes: Deque[Tuple[int, Tuple[float, float, float, float]]] = field(default_factory=deque)
    last_frame: int = -1


@dataclass
class Incident:
    track_ids: Tuple[str, ...]
    frame: int
    score: float
    cues: Dict[str, object]
    status: str = "candidate"
    boxes: Tuple[Tuple[float, float, float, float], ...] = ()   # where it happened


def _near(a, b, factor: float = 1.5) -> bool:
    """Boxes overlap or their centres are within `factor` mean box widths."""
    if _iou(a, b) > 0:
        return True
    ca = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
    cb = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    w = ((a[2] - a[0]) + (b[2] - b[0])) / 2
    return math.hypot(ca[0] - cb[0], ca[1] - cb[1]) <= factor * w


def _iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class AccidentMonitor:
    def __init__(self, fps: float = 25.0, cfg: AccidentConfig = AccidentConfig()) -> None:
        self.fps = max(1.0, float(fps))
        self.cfg = cfg
        self.tracks: Dict[str, _Track] = defaultdict(_Track)
        self.pending: Dict[Tuple[str, ...], Incident] = {}
        self.cooldown: Dict[str, int] = {}
        self.frame = 0
        self.stats: Dict[str, int] = defaultdict(int)
        self._stop_marks: Dict[str, int] = {}
        self._impacts: Dict[Tuple[str, str], Tuple[int, float, Tuple, Tuple]] = {}   # pair -> frame, iou, boxes

    # ------------------------------------------------------------ helpers
    def _k(self, seconds: float) -> int:
        return max(2, int(round(seconds * self.fps)))

    def _velocity(self, t: _Track, end_offset: int = 0) -> Optional[np.ndarray]:
        """Velocity (box-heights/s) over the window ending `end_offset` frames ago."""
        k = self._k(self.cfg.window_s)
        pts = list(t.pts)
        end = len(pts) - 1 - end_offset
        start = end - k
        if start < 0 or end < 0:
            return None
        f0, x0, y0, h0 = pts[start]
        f1, x1, y1, h1 = pts[end]
        df = f1 - f0
        if df <= 0:
            return None
        h = max(self.cfg.min_box_h, (h0 + h1) / 2)
        return np.array([x1 - x0, y1 - y0]) / h / df * self.fps

    def _still_for(self, t: _Track) -> float:
        """Seconds the track has stayed within detection jitter of its current
        position (0.15 box-heights + v_stop drift)."""
        pts = list(t.pts)
        if len(pts) < 2:
            return 0.0
        fx, x, y, h = pts[-1]
        h = max(self.cfg.min_box_h, h)
        still = 0.0
        for f0, x0, y0, _ in reversed(pts[:-1]):
            dt = (fx - f0) / self.fps
            if math.hypot(x - x0, y - y0) / h > 0.15 + self.cfg.v_stop * dt:
                return still
            still = dt
        return still

    # ------------------------------------------------------------ main entry
    def update(self, frame_no: int, tracks: Sequence[Tuple[str, Tuple[float, float, float, float]]]) -> List[Incident]:
        """tracks: [(track_id, (x1,y1,x2,y2)), ...] for the current frame.
        Returns incidents CONFIRMED on this frame (each emitted once)."""
        self.frame = frame_no
        cfg = self.cfg
        horizon = self._k(cfg.history_s)
        cur = {}
        for tid, box in tracks:
            tid = str(tid)
            x1, y1, x2, y2 = map(float, box)
            h = y2 - y1
            if h < cfg.min_box_h:
                continue
            t = self.tracks[tid]
            t.pts.append((frame_no, (x1 + x2) / 2, (y1 + y2) / 2, h))
            t.boxes.append((frame_no, (x1, y1, x2, y2)))
            while t.pts and frame_no - t.pts[0][0] > horizon:
                t.pts.popleft()
            while t.boxes and frame_no - t.boxes[0][0] > horizon:
                t.boxes.popleft()
            t.last_frame = frame_no
            cur[tid] = (x1, y1, x2, y2)
        # forget tracks not seen for a while
        for tid in [k for k, t in self.tracks.items() if frame_no - t.last_frame > horizon]:
            del self.tracks[tid]

        k = self._k(cfg.window_s)
        speeds = {}
        per_track_cues: Dict[str, Dict[str, object]] = {}
        for tid in cur:
            t = self.tracks[tid]
            v_now = self._velocity(t, 0)
            v_before = self._velocity(t, k)
            s_now = float(np.linalg.norm(v_now)) if v_now is not None else None
            s_before = float(np.linalg.norm(v_before)) if v_before is not None else None
            speeds[tid] = s_now
            cues: Dict[str, object] = {}
            if s_now is not None and s_before is not None:
                if s_before >= cfg.v_move and s_now <= cfg.v_stop:
                    cues["sudden_stop"] = round(s_before - s_now, 3)
                if s_before >= cfg.v_move and s_now >= cfg.v_move:
                    cosang = float(np.dot(v_now, v_before) / (s_now * s_before))
                    ang = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
                    if ang >= cfg.reversal_deg:
                        cues["reversal"] = round(ang, 1)
            per_track_cues[tid] = cues

        moving = [s for s in speeds.values() if s is not None]
        flow = float(np.median(moving)) if moving else 0.0
        for tid in cur:
            still = self._still_for(self.tracks[tid])
            others = [s for o, s in speeds.items() if o != tid and s is not None]
            if still >= cfg.stall_s and others and float(np.median(others)) >= cfg.v_move:
                per_track_cues[tid]["stalled"] = round(still, 1)

        # impact: sudden IoU jump between two vehicles
        ids = list(cur)
        kc = self._k(cfg.contact_window_s)
        impacts: List[Tuple[str, str, float]] = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                iou_now = _iou(cur[a], cur[b])
                if iou_now < cfg.iou_contact:
                    continue
                ba = dict(self.tracks[a].boxes)
                bb = dict(self.tracks[b].boxes)
                past = [f for f in ba if f in bb and frame_no - kc * 2 <= f <= frame_no - kc]
                if past and min(_iou(ba[f], bb[f]) for f in past) < cfg.iou_before:
                    impacts.append((a, b, round(iou_now, 3)))

        # build candidates
        for a, b, iou_now in impacts:
            self._impacts[(a, b)] = (frame_no, iou_now, cur[a], cur[b])
            ca, cb = per_track_cues[a], per_track_cues[b]
            if any(c in ca or c in cb for c in ("sudden_stop", "reversal")):
                self._candidate((a, b), 0.9, {"impact_iou": iou_now, a: ca, b: cb, "flow": round(flow, 3)},
                                boxes=(cur[a], cur[b]))
        # impact remembered for impact_memory_s: a later hard stop, or a vehicle lost in the
        # crash (occlusion / ID switch), still makes it a crash candidate
        mem = self._k(cfg.impact_memory_s) if cfg.impact_memory_s > 0 else 0
        lost = self._k(cfg.vanish_s)
        for (a, b), (f0, iou0, box_a, box_b) in list(self._impacts.items()):
            if frame_no - f0 > mem:
                del self._impacts[(a, b)]
                continue
            if frame_no == f0 or tuple(sorted((a, b))) in self.pending:
                continue
            ca, cb = per_track_cues.get(a, {}), per_track_cues.get(b, {})
            stopped = [t for t, c in ((a, ca), (b, cb)) if "sudden_stop" in c or "reversal" in c]
            vanished = [t for t in (a, b) if t not in cur and frame_no - self.tracks[t].last_frame >= lost]
            if stopped or vanished:
                self._candidate((a, b), 0.85, {"impact_iou": iou0, "after_impact_s": round((frame_no - f0) / self.fps, 2),
                                               "stopped": stopped, "lost": vanished, "flow": round(flow, 3)},
                                boxes=(box_a, box_b))
        for tid, c in per_track_cues.items():
            if "stalled" in c and self._had_sudden_stop(tid):
                self._candidate((tid,), 0.75, {tid: c, "after_sudden_stop": True, "flow": round(flow, 3)})
            elif "reversal" in c:
                self._candidate((tid,), 0.5, {tid: c, "flow": round(flow, 3)})
            elif "stalled" in c and float(c["stalled"]) >= 2 * cfg.stall_s:
                self._candidate((tid,), 0.5, {tid: c, "flow": round(flow, 3)})
            if "sudden_stop" in c:
                self._stop_marks[tid] = frame_no

        return self._confirm(cur)

    # ------------------------------------------------------------ internals
    def _had_sudden_stop(self, tid: str) -> bool:
        f = self._stop_marks.get(tid)
        return f is not None and self.frame - f <= self._k(self.cfg.stall_s + 2 * self.cfg.window_s + 1)

    def _candidate(self, ids: Tuple[str, ...], score: float, cues: Dict[str, object], boxes: Tuple = ()) -> None:
        key = tuple(sorted(ids))
        if any(self.cooldown.get(t, -1) >= self.frame for t in key):
            return
        # a multi-vehicle candidate absorbs weaker single-vehicle ones for the same tracks
        for other in [k for k in self.pending if k != key and set(k) < set(key)]:
            del self.pending[other]
        if any(set(key) < set(k) for k in self.pending):
            return
        inc = self.pending.get(key)
        if inc is None or score > inc.score:
            if not boxes:
                boxes = tuple(self.tracks[t].boxes[-1][1] for t in key if self.tracks[t].boxes)
            self.pending[key] = Incident(track_ids=key, frame=self.frame, score=score, cues=cues, boxes=tuple(boxes))
            self.stats["candidates"] += 1

    def _confirm(self, cur: Dict[str, tuple]) -> List[Incident]:
        out = []
        cfg = self.cfg
        for key, inc in list(self.pending.items()):
            age = (self.frame - inc.frame) / self.fps
            if inc.score < cfg.confirm_score:
                if age > cfg.confirm_s * 2:
                    del self.pending[key]
                continue
            present = [t for t in key if t in cur]
            still = [self._still_for(self.tracks[t]) for t in present]
            ok = bool(present) and min(still) >= cfg.confirm_s
            if not ok and cfg.spatial_confirm and inc.boxes and age >= cfg.confirm_s:
                # the crashed vehicles may have new IDs now: any vehicle standing still at the spot
                at_spot = [t for t, b in cur.items() if any(_near(b, ib, 1.0) for ib in inc.boxes)]
                if any(self._still_for(self.tracks[t]) >= cfg.confirm_s for t in at_spot):
                    ok = True
                    inc.cues = {**inc.cues, "confirmed_by": "vehicle still at the crash spot"}
            if ok:
                inc.status = "confirmed"
                if still:
                    inc.cues = {**inc.cues, "still_after_s": round(min(still), 1)}
                out.append(inc)
                for t in key:
                    self.cooldown[t] = self.frame + self._k(cfg.cooldown_s)
                del self.pending[key]
            elif age > cfg.confirm_s + cfg.stall_s:
                del self.pending[key]          # drove on -> not a crash
                self.stats["dropped"] += 1
        out = self._merge_nearby(out, cur)
        self.stats["confirmed"] += len(out)
        return out

    def _merge_nearby(self, incs: List[Incident], cur: Dict[str, tuple]) -> List[Incident]:
        """Stopped vehicles of the same crash confirming together -> one incident."""
        merged: List[Incident] = []
        for inc in sorted(incs, key=lambda i: -i.score):
            boxes = [cur[t] for t in inc.track_ids if t in cur]
            host = None
            for m in merged:
                mb = [cur[t] for t in m.track_ids if t in cur]
                if any(_near(a, b) for a in boxes for b in mb):
                    host = m
                    break
            if host is None:
                merged.append(inc)
            else:
                host.track_ids = tuple(sorted(set(host.track_ids) | set(inc.track_ids)))
                host.cues = {**inc.cues, **host.cues}
        return merged
