# DriveShieldX -- measured results

Every number below was produced by the scripts in `evaluation/` on the data named next to it.

## Table 4 -- Multi-object tracking (UA-DETRAC)

Sequences (10): MVI_20012, MVI_20065, MVI_39761, MVI_40152, MVI_40212, MVI_40742, MVI_40775, MVI_40891, MVI_40992, MVI_63563  
Machine: Intel64 Family 6 Model 186 Stepping 3, GenuineIntel, torch 2.13.0+cpu, cuda=False

| Tracker | MOTA | IDF1 | HOTA | IDSW | Assoc. FPS | IDF1 mean±sd (per seq) |
|---|---|---|---|---|---|---|
| ByteTrack | 67.5 | 75.2 | 61.0 | 451 | 357 | 74.1 ± 11.3 |
| Centroid | 68.3 | 68.2 | 58.9 | 1583 | 4266 | 67.5 ± 10.7 |
| DeepSORT | 68.7 | 74.3 | 61.5 | 718 | 1 | 74.1 ± 8.4 |
| NoTracker(per-frame) | -6.7 | 0.7 | 6.0 | 93053 | 24627 | 0.9 ± 0.8 |

Paired tests (Holm-corrected):

- IDF1: ByteTrack vs Centroid -- paired t-test, mean diff 6.65, p=0.001422, p_Holm=0.002844
- IDF1: ByteTrack vs DeepSORT -- paired t-test, mean diff -0.02, p=0.9896, p_Holm=0.9896
- IDF1: ByteTrack vs NoTracker(per-frame) -- paired t-test, mean diff 73.18, p=7.969e-09, p_Holm=3.985e-08
- IDF1: Centroid vs DeepSORT -- paired t-test, mean diff -6.67, p=0.0002095, p_Holm=0.0006286
- IDF1: Centroid vs NoTracker(per-frame) -- paired t-test, mean diff 66.53, p=1.059e-08, p_Holm=4.238e-08
- IDF1: DeepSORT vs NoTracker(per-frame) -- paired t-test, mean diff 73.20, p=5.515e-10, p_Holm=3.309e-09
- MOTA: ByteTrack vs Centroid -- Wilcoxon signed-rank, mean diff -1.87, p=0.625, p_Holm=0.625
- MOTA: ByteTrack vs DeepSORT -- Wilcoxon signed-rank, mean diff -2.33, p=0.1934, p_Holm=0.5801
- MOTA: ByteTrack vs NoTracker(per-frame) -- Wilcoxon signed-rank, mean diff 73.83, p=0.001953, p_Holm=0.007812
- MOTA: Centroid vs DeepSORT -- paired t-test, mean diff -0.45, p=0.2711, p_Holm=0.5801
- MOTA: Centroid vs NoTracker(per-frame) -- paired t-test, mean diff 75.70, p=1.464e-09, p_Holm=8.786e-09
- MOTA: DeepSORT vs NoTracker(per-frame) -- paired t-test, mean diff 76.15, p=2.011e-09, p_Holm=1.006e-08
- HOTA: ByteTrack vs Centroid -- paired t-test, mean diff 1.57, p=0.1927, p_Holm=0.3853
- HOTA: ByteTrack vs DeepSORT -- Wilcoxon signed-rank, mean diff -1.53, p=0.2324, p_Holm=0.3853
- HOTA: ByteTrack vs NoTracker(per-frame) -- paired t-test, mean diff 52.24, p=9.752e-08, p_Holm=3.901e-07
- HOTA: Centroid vs DeepSORT -- paired t-test, mean diff -3.10, p=0.0003171, p_Holm=0.0009513
- HOTA: Centroid vs NoTracker(per-frame) -- paired t-test, mean diff 50.67, p=3.786e-08, p_Holm=1.893e-07
- HOTA: DeepSORT vs NoTracker(per-frame) -- paired t-test, mean diff 53.77, p=1.013e-08, p_Holm=6.081e-08

Speed from trajectories on UA-DETRAC (same estimator & s_px for tracker and GT):

- ByteTrack: MAE vs GT trajectory 3.00 km/h; >200 km/h 0.16%; max 446.1 km/h
- Centroid: MAE vs GT trajectory 4.01 km/h; >200 km/h 0.21%; max 435.9 km/h
- DeepSORT: MAE vs GT trajectory 3.93 km/h; >200 km/h 0.26%; max 555.0 km/h

## Disagreement gate

- Two-wheeler crops evaluated: 1204 (sources: C:/Users/helim/Downloads/triple_riding_video_1.mp4, C:/Users/helim/Downloads/seat_belt_video_1.mp4, C:/Users/helim/Downloads/seat_belt_video_2.mp4, C:/Users/helim/Downloads/seatbelt.mp4, C:/Users/helim/Downloads/15397236_1920_1080_60fps.mp4)
- agree-positive 95, disagree 191, agree-negative 918
- **Withheld rate** (disagree / OR-positive): 66.78%
- **False-issuance rate with gate**: 1.06% (1/94)
- False-issuance rate without gate (OR rule, ablation): 21.00% (59/281)
- Withheld events that were real violations: 129/187

## Table 5 -- ANPR recognition

**Still missing** -- anpr_benchmark make-sheet, label, score.

## Table 3 -- per-class detector metrics

**Still missing** -- needs the validation splits (perclass_val on Kaggle).

## Throughput

- 15397236_1920_1080_60fps.mp4 on cpu: 0.91 FPS (1099 ms/frame, p95 1818 ms)
- 15397236_1920_1080_60fps.mp4 on cpu + OCR: 0.52 FPS (1924 ms/frame, p95 4081 ms)
- seat_belt_video_1.mp4 on cpu: 1.14 FPS (877 ms/frame, p95 1153 ms)
- seat_belt_video_1.mp4 on cpu + OCR: 0.37 FPS (2679 ms/frame, p95 6188 ms)
- seat_belt_video_2.mp4 on cpu: 0.26 FPS (3889 ms/frame, p95 5033 ms)
- seat_belt_video_2.mp4 on cpu + OCR: 0.11 FPS (9507 ms/frame, p95 20131 ms)
- seatbelt.mp4 on cpu: 1.66 FPS (603 ms/frame, p95 990 ms)
- seatbelt.mp4 on cpu + OCR: 1.07 FPS (935 ms/frame, p95 3184 ms)
- triple_riding_video_1.mp4 on cpu: 1.01 FPS (993 ms/frame, p95 1574 ms)
- triple_riding_video_1.mp4 on cpu + OCR: 0.36 FPS (2766 ms/frame, p95 6594 ms)

## Violation-level accuracy (legacy vs current decision rules, single frame)

**Still missing** -- violation_eval make-sheet, label, score.

## Fog / visibility

- Synthetic (scattering model, 100 real frames): t=1.0: 0.00; t=0.85: 0.24; t=0.7: 0.44; t=0.5: 0.69; t=0.35: 0.82; t=0.2: 0.94
- Real labelled fog footage: **still missing** (fog_eval labelled).

## Accident detection

- **Still missing** -- needs labelled crash clips (accident_eval).

## Training provenance (from the checkpoints)

| Model | Base | Epochs | Batch | Optimizer | Seed | Ultralytics |
|---|---|---|---|---|---|---|
| helmet_yolov8.pt | yolov8s.pt | 100 | 16 | auto | 42 | 8.4.121 |
| seatbelt_yolov8.pt | yolov8s.pt | 100 | 16 | auto | 42 | 8.4.121 |
| twowheeler_best.pt | yolov8n.pt | 100 | 32 | auto | 0 | 8.4.60 |
| plate_best_v2.pt | yolov8n.pt | 80 | 32 | auto | 0 | 8.4.60 |

## Runtime DB

- Speed estimates 23185, >200 km/h 0.13%, >400 km/h 0.02%, max 781.68 km/h