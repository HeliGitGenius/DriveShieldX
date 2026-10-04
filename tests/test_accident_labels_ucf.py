"""UCF-Crime timing file -> accident_eval labels (frames to seconds, windows, normals)."""
from pathlib import Path

from evaluation import accident_labels_ucf as u


def test_parse_and_build(tmp_path):
    txt = tmp_path / "ann.txt"
    txt.write_text("RoadAccidents002_x264.mp4  RoadAccidents  300  450  900  960\n"
                   "RoadAccidents010_x264.mp4  RoadAccidents  60  90  -1  -1\n"
                   "Normal_Videos_003_x264.mp4  Normal  -1  -1  -1  -1\n"
                   "Robbery050_x264.mp4  Robbery  10  20  -1  -1\n")
    ann = u.parse_annotations(txt)
    assert ann["RoadAccidents002_x264.mp4"] == ("RoadAccidents", [(300, 450), (900, 960)])
    files = {n: tmp_path / n for n in ann}
    rows = u.build_rows(ann, files, 5, 5, pre=30, post=30, normal_s=60, seed=0,
                        info=lambda p: (30.0, 100.0))
    two = [r for r in rows if r["video"].endswith("RoadAccidents002_x264.mp4")]
    assert [(r["accident_start_s"], r["accident_end_s"]) for r in two] == [("10.00", "15.00"), ("30.00", "32.00")]
    assert two[0]["clip_start_s"] == "0.00" and two[0]["clip_end_s"] == "62.00"
    normal = [r for r in rows if not r["accident_start_s"]]
    assert len(normal) == 1 and normal[0]["clip_end_s"] == "60.00"
    assert not any("Robbery" in r["video"] for r in rows)


def test_dev_set_never_overlaps_test(tmp_path):
    ann = {"RoadAccidents002_x264.mp4": ("RoadAccidents", [(300, 450)]),
           "Normal_Videos_003_x264.mp4": ("Normal", []), "Normal_Videos_006_x264.mp4": ("Normal", [])}
    names = list(ann) + ["RoadAccidents050_x264.mp4", "RoadAccidents051_x264.mp4"]
    files = {n: tmp_path / n for n in names}
    exclude = {str(tmp_path / "Normal_Videos_003_x264.mp4")}
    rows = u.build_dev_rows(ann, files, exclude, 5, 5, max_s=120, normal_s=60, seed=1, info=lambda p: (30.0, 90.0))
    vids = {Path(r["video"]).name for r in rows}
    assert vids == {"RoadAccidents050_x264.mp4", "RoadAccidents051_x264.mp4", "Normal_Videos_006_x264.mp4"}
    crash = [r for r in rows if r["accident_start_s"]]
    assert crash[0]["accident_end_s"] == "90.00" == crash[0]["clip_end_s"]
