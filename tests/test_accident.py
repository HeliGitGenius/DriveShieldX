"""Accident monitor on synthetic trajectories (25 FPS)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detection.accident_monitor import AccidentMonitor  # noqa: E402

FPS = 25.0


def box(cx, cy, w=60, h=40):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def run(mon, frames):
    out = []
    for f, tracks in enumerate(frames, 1):
        out += mon.update(f, tracks)
    return out


def flow_car(tid, f, y=300, speed=4.0, x0=0):
    return (tid, box(x0 + speed * f, y))


def test_head_on_impact_then_stop_is_confirmed():
    frames = []
    for f in range(1, 200):
        if f < 65:
            a, b = 100 + 3 * f, 520 - 3 * f
        else:  # boxes overlap from f=65 (impact) and both stay put
            a, b = 100 + 3 * 65, 520 - 3 * 65
        frames.append([("A", box(a, 300)), ("B", box(b, 300)), flow_car("C", f, y=100), flow_car("D", f, y=500)])
    inc = run(AccidentMonitor(FPS), frames)
    assert len(inc) == 1 and set(inc[0].track_ids) == {"A", "B"} and inc[0].score >= 0.9


def test_hard_brake_then_stalled_in_moving_traffic():
    frames = []
    for f in range(1, 400):
        x = 100 + 5 * min(f, 80)
        frames.append([("A", box(x, 300)), flow_car("B", f, y=150), flow_car("C", f, y=450, x0=50)])
    inc = run(AccidentMonitor(FPS), frames)
    assert len(inc) == 1 and inc[0].track_ids == ("A",)


def test_red_light_everyone_stops_no_accident():
    frames = []
    for f in range(1, 400):
        frames.append([(t, box(50 + 5 * min(f, 80), y)) for t, y in (("A", 100), ("B", 250), ("C", 400))])
    assert run(AccidentMonitor(FPS), frames) == []


def test_brake_and_drive_on_no_accident():
    frames = []
    for f in range(1, 300):
        x = 100 + 5 * f if f < 60 else (400 if f < 80 else 400 + 5 * (f - 80))
        frames.append([("A", box(x, 300)), flow_car("B", f, y=120)])
    assert run(AccidentMonitor(FPS), frames) == []


def test_normal_flow_no_accident():
    frames = [[flow_car(t, f, y=y) for t, y in (("A", 100), ("B", 250), ("C", 400))] for f in range(1, 300)]
    assert run(AccidentMonitor(FPS), frames) == []


def test_touching_boxes_stopping_together_merge_into_one_incident():
    frames = []
    for f in range(1, 300):
        a, b = (100 + 3 * min(f, 60), 520 - 3 * min(f, 60))
        frames.append([("A", box(a, 300)), ("B", box(b, 300)), flow_car("C", f, y=100), flow_car("D", f, y=500)])
    inc = run(AccidentMonitor(FPS), frames)
    assert len(inc) == 1 and set(inc[0].track_ids) == {"A", "B"}


def _crash_with_id_switch():
    frames = []
    for f in range(1, 260):
        if f < 65:
            tr = [("A", box(100 + 3 * f, 300)), ("B", box(520 - 3 * f, 300))]
        elif f < 75:   # impact: the tracker loses both cars for a moment
            tr = [("A", box(100 + 3 * 65, 300))] if f < 68 else []
        else:          # they come back with NEW ids and stay where they crashed
            tr = [("A2", box(100 + 3 * 65, 302)), ("B2", box(520 - 3 * 65, 298))]
        frames.append(tr + [flow_car("C", f, y=100), flow_car("D", f, y=500)])
    return frames


def test_crash_confirmed_even_when_tracker_switches_ids():
    from detection.accident_monitor import AccidentConfig
    robust = AccidentConfig(impact_memory_s=1.5, spatial_confirm=True)
    inc = run(AccidentMonitor(FPS, robust), _crash_with_id_switch())
    assert len(inc) == 1 and inc[0].score >= 0.85


def test_old_behaviour_missed_the_id_switch_crash():
    from detection.accident_monitor import AccidentConfig
    old = AccidentConfig(impact_memory_s=0.0, spatial_confirm=False)
    assert run(AccidentMonitor(FPS, old), _crash_with_id_switch()) == []


def test_defaults_are_the_zero_false_alarm_point():
    from detection.accident_monitor import AccidentConfig
    c = AccidentConfig()
    assert c.impact_memory_s == 0.0 and c.spatial_confirm is False


def test_overtake_with_brief_occlusion_is_not_a_crash():
    frames = []
    for f in range(1, 300):
        a = ("A", box(50 + 6 * f, 300))
        b = ("B", box(200 + 3 * f, 305))
        tr = [a] if 45 <= f < 60 else [a, b]          # B hidden behind A while being overtaken
        frames.append(tr + [flow_car("C", f, y=100)])
    from detection.accident_monitor import AccidentConfig
    assert run(AccidentMonitor(FPS), frames) == []
    assert run(AccidentMonitor(FPS, AccidentConfig(impact_memory_s=1.5, spatial_confirm=True)), frames) == []


def test_eval_replay_and_score_on_cached_tracks():
    from detection.accident_monitor import AccidentConfig
    from evaluation.accident_eval import replay, score
    crash = {"fps": FPS, "first": 0,
             "frames": [[f, [[t, list(b)] for t, b in tr]] for f, tr in enumerate(_crash_with_id_switch(), 1)]}
    calm = {"fps": FPS, "first": 0,
            "frames": [[f, [["C", list(flow_car("C", f, y=100)[1])]]] for f in range(1, 200)]}
    gt = {"crash.mp4": [(2.4, 3.2)], "calm.mp4": []}
    dets, durs = {}, {}
    for v, tr in (("crash.mp4", crash), ("calm.mp4", calm)):
        dets[v], durs[v] = replay(tr, AccidentConfig(impact_memory_s=1.5, spatial_confirm=True))
    r = score(gt, dets, durs, tolerance=10)
    assert r["TP"] == 1 and r["FP"] == 0 and r["crashes_detected"] == "1/1"
    assert r["normal_clips_with_false_alarm"] == "0/1"
