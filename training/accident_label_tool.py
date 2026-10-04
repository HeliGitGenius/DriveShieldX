"""Mark crash times on UCF-Crime RoadAccidents videos for training the crash model.

Shows only crash videos that are NOT in the test labels, so the 23 test crashes are
never seen. For each video: play it, read the time of impact from the player and type it
EXACTLY as the player shows it (0:12 = twelve seconds, 1:05 = sixty-five seconds),
type when the vehicles have stopped, check that the two pictures show the crash,
press "Save". Use "No clear crash" when you cannot see one.
Plain seconds also work (12 or 12.5); "0.12" is refused because it is ambiguous.
Answers are saved after every video, so you can stop and continue later.

  streamlit run training/accident_label_tool.py --server.port 8503 -- --videos C:\\data\\ucf\\videos --exclude C:\\data\\ucf\\accident_labels.csv --out C:\\data\\ucf\\accident_train_labels.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2

FIELDS = ["video", "accident_start_s", "accident_end_s", "status"]


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", required=True)
    ap.add_argument("--exclude", required=True, help="test label csv (these videos are hidden)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hint", help="old label file whose times were typed as minutes.seconds and rounded "
                                   "(e.g. 0.1 for 0:12): shows a one-picture-per-second strip around that range")
    return ap.parse_args(sys.argv[1:])


def hint_centre(value: str):
    """An old mis-typed time like '0.1' (from '0:1x' typed as 0.1x and rounded to one decimal)
    -> centre second of the 10-second range it came from (12 s +- 5)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    minutes = int(v)
    tens = round((v - minutes) * 10)
    return minutes * 60 + tens * 10


def fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}".replace(".0", "") if sec % 1 else f"{int(sec // 60)}:{int(sec % 60):02d}"


def load(out: Path) -> dict:
    if not out.exists():
        return {}
    with open(out, newline="", encoding="utf-8") as f:
        return {r["video"]: r for r in csv.DictReader(f)}


def save_rows(out: Path, rows: dict) -> None:
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows.values())
    tmp.replace(out)


def candidates(videos: Path, exclude: Path) -> list[str]:
    with open(exclude, newline="", encoding="utf-8-sig") as f:
        test = {Path(r["video"]).name for r in csv.DictReader(f)}
    return sorted(str(p.resolve()) for p in videos.rglob("RoadAccidents*")
                  if p.suffix.lower() in {".mp4", ".avi", ".mkv"} and p.name not in test)


def thumb_at(video: str, t: float):
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, img = cap.read()
    cap.release()
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if ok else None


def frame_at(video: str, t: float):
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, img = cap.read()
    cap.release()
    if not ok:
        return None
    img = cv2.resize(img, (img.shape[1] * 2, img.shape[0] * 2))
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def parse_time(text: str):
    """'0:12' / '1:05' / '1:05.5' (player format) or '12' / '12.5' seconds -> seconds.
    Returns (seconds or None, error message or None)."""
    t = (text or "").strip().lower()
    for suffix in ("seconds", "second", "secs", "sec", "s"):
        if t.endswith(suffix) and t[: -len(suffix)].strip().replace(".", "", 1).replace(":", "", 1).isdigit():
            t = t[: -len(suffix)].strip()
            break
    if not t:
        return None, "empty"
    try:
        if ":" in t:
            m, sec = t.split(":", 1)
            m, sec = int(m), float(sec)
            if not 0 <= sec < 60:
                return None, f"'{t}': seconds after ':' must be 0-59"
            return m * 60 + sec, None
        v = float(t)
    except ValueError:
        return None, f"'{t}' is not a time. Type it like the player shows it, e.g. 0:12"
    if "." in t and v < 1:
        return None, f"'{t}' looks like minutes.seconds. For twelve seconds type 0:12 (or 12)"
    return v, None


def video_bytes(video: str) -> bytes:
    return Path(video).read_bytes()


def duration(video: str) -> float:
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return n / fps


def main() -> None:
    import streamlit as st

    a = args()
    out = Path(a.out)
    st.set_page_config(page_title="Crash time labelling", layout="wide")
    vids = st.cache_data(candidates)(Path(a.videos), Path(a.exclude))
    rows = load(out)
    todo = [v for v in vids if v not in rows]
    done = sum(1 for r in rows.values() if r.get("status") == "crash")
    st.title("Crash time labelling (training videos only)")
    st.caption(f"{done} crashes labelled · {len(rows) - done} skipped · {len(todo)} left · "
               f"test videos hidden · saved to {out}")
    if not todo:
        st.success("All training crash videos are done.")
        return
    vid = todo[0]
    dur = st.cache_data(duration)(vid)
    st.subheader(f"{Path(vid).name}  ({dur:.0f} s)")
    left, right = st.columns([3, 2])
    with left:
        st.video(st.cache_data(video_bytes)(vid))
        st.caption("Play the video and read the time on the player when the vehicles first touch.")
    hint = load(Path(a.hint)).get(vid) if a.hint and Path(a.hint).exists() else None
    if hint and hint.get("status") == "crash":
        c0, c1 = hint_centre(hint["accident_start_s"]), hint_centre(hint["accident_end_s"])
        if c0 is not None and c1 is not None:
            lo, hi = max(0, c0 - 6), min(int(dur), max(c0, c1) + 8)
            st.markdown(f"**Quick mode:** your earlier entry puts this crash around **{fmt(c0)}**. "
                        "Click **Start** under the picture where the vehicles first touch and **End** under "
                        "the one where they have stopped. If it is not in these pictures, type the times instead.")

            def setter(key, value):
                st.session_state[key] = value

            secs = list(range(lo, hi + 1))
            for r in range(0, len(secs), 8):
                cols = st.columns(8)
                for col, sec in zip(cols, secs[r:r + 8]):
                    img = st.cache_data(thumb_at)(vid, float(sec))
                    if img is not None:
                        col.image(img, caption=fmt(sec), use_container_width=True)
                    b1, b2 = col.columns(2)
                    b1.button("Start", key=f"qs_{vid}_{sec}", on_click=setter, args=(f"start_{vid}", fmt(sec)))
                    b2.button("End", key=f"qe_{vid}_{sec}", on_click=setter, args=(f"end_{vid}", fmt(sec + 0.9)))
    with right:
        st.markdown("Type times **exactly as the player shows them**: `0:12` = 12 s, `1:05` = 65 s.")
        t_start = st.text_input("Crash starts at (first contact)", key=f"start_{vid}", placeholder="e.g. 0:12")
        t_end = st.text_input("Crash ends at (vehicles stopped)", key=f"end_{vid}", placeholder="e.g. 0:15")
        start, e1 = parse_time(t_start)
        end, e2 = parse_time(t_end)
        for v, e in ((start, e1), (end, e2)):
            if e and e != "empty":
                st.error(e)
        if start is not None and end is not None:
            st.info(f"= {start:.1f} s to {end:.1f} s  (check the two pictures below show the crash)")
        c = st.columns(2)
        save = c[0].button("Save", type="primary", key=f"save_{vid}")
        skip = c[1].button("No clear crash", key=f"skip_{vid}")
        if save:
            problem = None
            if start is None or end is None:
                problem = "Type both times first."
            elif end - start < 0.5:
                problem = "End must be at least half a second after start."
            elif dur and end > dur + 0.5:
                problem = f"End is after the video ends ({dur:.0f} s)."
            if problem:
                st.error(problem)
            else:
                rows[vid] = {"video": vid, "accident_start_s": f"{start:.2f}", "accident_end_s": f"{end:.2f}",
                             "status": "crash"}
                save_rows(out, rows)
                st.rerun()
        if skip:
            rows[vid] = {"video": vid, "accident_start_s": "", "accident_end_s": "", "status": "skip"}
            save_rows(out, rows)
            st.rerun()
        p = st.columns(2)
        for col, t, label in ((p[0], start, "start"), (p[1], end, "end")):
            if t is not None:
                img = st.cache_data(frame_at)(vid, t)
                if img is not None:
                    col.image(img, caption=f"{label}: {t:.1f} s")
    st.caption("Start = first contact. End = vehicles at rest (2-5 s later if unclear). "
               "Unsure, too dark, or no crash visible -> No clear crash.")


if __name__ == "__main__":
    main()
