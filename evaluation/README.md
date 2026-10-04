# DriveShieldX evaluation suite

Every script here measures something the paper marks **not measured / not run**.
Nothing is estimated: if the data for an experiment is missing, `make_report`
lists it as missing.

| Paper item (red) | Script | Data it needs | Where it runs |
|---|---|---|---|
| Table 4: MOTA, IDF1, HOTA, IDSW, Assoc. FPS for ByteTrack / Centroid / DeepSORT | `cache_detections` → `run_tracking_eval` | UA-DETRAC images + XML (`C:\datasets`) | laptop CPU (~30–60 min for 10 seqs) |
| DeepSORT "not implemented" | `trackers.DeepSortAdapter` (deep-sort-realtime, MobileNetV2 ReID) | — | — |
| Ablation "without ByteTrack" | `notracker` row of the tracking table | UA-DETRAC | laptop |
| Mean ± s.d. + paired tests over sequences (Sec. 5.5) | inside `run_tracking_eval` (`stats.py`) | UA-DETRAC | laptop |
| Speed error vs ground-truth trajectories, implausible-estimate rate | inside `run_tracking_eval` | UA-DETRAC | laptop |
| Gate withheld rate | `gate_eval measure` | your two-wheeler videos | laptop |
| False-issuance rate with / without gate (ablation) | `gate_eval score` | `review_sheet.csv` judged by a person | laptop |
| Table 5: ANPR exact-match accuracy, CER | `anpr_benchmark make-sheet` → label → `score` | your videos + typed true plate strings | laptop |
| Table 3: per-class P / R / mAP | `perclass_val` | the Kaggle validation splits | Kaggle |
| Table 3: mean ± s.d. over seeds | `seed_sweep` | training splits + GPU | Kaggle |
| Throughput (CPU / GPU) | `throughput` | any video | laptop / Kaggle |
| DB statistics | `db_metrics` | `database/overspeed.db` | anywhere |
| Violation P / R / F1, legacy vs current rules (helmet, triple-riding, seat belt) | `violation_eval make-sheet` → label → `score` | your videos + y/n labels | laptop |
| Fog detector | `fog_eval synthetic` / `fog_eval labelled` | frames (+ real fog frames sorted into level folders) | anywhere |
| Accident detector P / R / time-to-detect | `accident_eval` | clips + crash start/end times | laptop |
| Table 2 hyper-parameters | `training.inspect_checkpoint` | `models/*.pt` | anywhere |

## Code changes that make the paper true

1. **Disagreement gate** (`detection/rule_violations.py`): the two helmet models used to be
   combined with OR (disagreement ⇒ violation issued). Now: both > θ ⇒ issue, both ≤ θ ⇒ clear,
   disagreement ⇒ `GATE_REVIEW` table (officer review queue), **no challan**. θ = 0.15, the same
   threshold the code already used. `DRIVESHIELDX_GATE=0` restores the old rule for the ablation.
   Tests: `python -m pytest tests/test_gate.py -q`.
2. **Two-stage ANPR** (`detection/advanced_pipeline.py`, `detection/npr.py`): the YOLOv8 plate model
   (`plate_best_v2.pt`) was loaded but never used for OCR. It now localises the plate first
   (`locate_plate`), and the crop is deblurred/sharpened and read by EasyOCR (`read_plate_crop`);
   the old Haar-cascade path remains as fallback. Taller-than-wide plate boxes are rejected.
3. **Review queue UI**: Rule Violations page shows pending gate reviews with confirm/reject.

## Quick start (Windows, project venv active)

```powershell
pip install -r evaluation\requirements-eval.txt
powershell -ExecutionPolicy Bypass -File evaluation\run_all.ps1 -Datasets "C:\datasets" -Videos "C:\...\triple_riding_video_1.mp4","C:\...\seat_belt_video_1.mp4"
```

Then: fill `true_text` in `evaluation\anpr_bench\labels.csv` and `human_label` in
`evaluation\results\gate\review_sheet.csv`, and run

```powershell
python -m evaluation.anpr_benchmark score --bench evaluation\anpr_bench
python -m evaluation.gate_eval score --sheet evaluation\results\gate\review_sheet.csv
python -m evaluation.make_report
```

## Protocol notes

- Tracking: the deployed detector (COCO `yolov8n.pt`, classes car/motorcycle/bus/truck, imgsz 640)
  runs **once** per sequence; the cached stream (conf ≥ 0.35, the deployed threshold) is replayed
  through every tracker. Replayed ByteTrack output was verified identical to the app's
  `model.track(..., tracker="bytetrack.yaml")` output.
- Sequences are chosen by a fixed rule (`pick_spread` over the sorted list) and written to
  `evaluation/cache/sequences.json` **before** any tracker runs.
- Tracker boxes lying ≥ 50 % inside UA-DETRAC ignored regions are dropped before scoring.
- Scores: TrackEval (reference HOTA/CLEAR/Identity code), cross-checked against py-motmetrics
  (`check_*` columns in `per_sequence.csv`).
- Speed on UA-DETRAC: tracker trajectories vs matched ground-truth trajectories, same estimator
  and the same s_px = 0.045 — this isolates detection+tracking error; it is **not** an error
  against true km/h (no calibrated reference speeds exist).
