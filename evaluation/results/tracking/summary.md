# Tracking evaluation (UA-DETRAC)

Sequences (10): MVI_20012, MVI_20065, MVI_39761, MVI_40152, MVI_40212, MVI_40742, MVI_40775, MVI_40891, MVI_40992, MVI_63563
Detector: deployed yolov8n (conf >= 0.35); identical cached detections replayed through every tracker.

| Tracker | MOTA | IDF1 | HOTA | IDSW | Assoc. FPS | MOTA mean±sd | IDF1 mean±sd | HOTA mean±sd |
|---|---|---|---|---|---|---|---|---|
| ByteTrack | 67.5 | 75.2 | 61.0 | 451 | 357 | 63.6±14.1 | 74.1±11.3 | 58.7±9.9 |
| Centroid | 68.3 | 68.2 | 58.9 | 1583 | 4266 | 65.5±11.9 | 67.5±10.7 | 57.1±8.9 |
| DeepSORT | 68.7 | 74.3 | 61.5 | 718 | 1 | 65.9±12.1 | 74.1±8.4 | 60.2±8.1 |
| NoTracker(per-frame) | -6.7 | 0.7 | 6.0 | 93053 | 24627 | -10.2±9.1 | 0.9±0.8 | 6.5±2.9 |

## Paired tests (per-sequence, Holm-corrected)

| Metric | A | B | test | mean diff (A-B) | p | p (Holm) |
|---|---|---|---|---|---|---|
| IDF1 | ByteTrack | Centroid | paired t-test | 6.65 | 0.001422 | 0.002844 |
| IDF1 | ByteTrack | DeepSORT | paired t-test | -0.02 | 0.9896 | 0.9896 |
| IDF1 | ByteTrack | NoTracker(per-frame) | paired t-test | 73.18 | 7.969e-09 | 3.985e-08 |
| IDF1 | Centroid | DeepSORT | paired t-test | -6.67 | 0.0002095 | 0.0006286 |
| IDF1 | Centroid | NoTracker(per-frame) | paired t-test | 66.53 | 1.059e-08 | 4.238e-08 |
| IDF1 | DeepSORT | NoTracker(per-frame) | paired t-test | 73.20 | 5.515e-10 | 3.309e-09 |
| MOTA | ByteTrack | Centroid | Wilcoxon signed-rank | -1.87 | 0.625 | 0.625 |
| MOTA | ByteTrack | DeepSORT | Wilcoxon signed-rank | -2.33 | 0.1934 | 0.5801 |
| MOTA | ByteTrack | NoTracker(per-frame) | Wilcoxon signed-rank | 73.83 | 0.001953 | 0.007812 |
| MOTA | Centroid | DeepSORT | paired t-test | -0.45 | 0.2711 | 0.5801 |
| MOTA | Centroid | NoTracker(per-frame) | paired t-test | 75.70 | 1.464e-09 | 8.786e-09 |
| MOTA | DeepSORT | NoTracker(per-frame) | paired t-test | 76.15 | 2.011e-09 | 1.006e-08 |
| HOTA | ByteTrack | Centroid | paired t-test | 1.57 | 0.1927 | 0.3853 |
| HOTA | ByteTrack | DeepSORT | Wilcoxon signed-rank | -1.53 | 0.2324 | 0.3853 |
| HOTA | ByteTrack | NoTracker(per-frame) | paired t-test | 52.24 | 9.752e-08 | 3.901e-07 |
| HOTA | Centroid | DeepSORT | paired t-test | -3.10 | 0.0003171 | 0.0009513 |
| HOTA | Centroid | NoTracker(per-frame) | paired t-test | 50.67 | 3.786e-08 | 1.893e-07 |
| HOTA | DeepSORT | NoTracker(per-frame) | paired t-test | 53.77 | 1.013e-08 | 6.081e-08 |

## Speed from trajectories (same estimator and s_px for tracker and ground truth)

| Tracker | MAE vs GT trajectory (km/h) | max estimate | >120 | >200 | >400 |
|---|---|---|---|---|---|
| ByteTrack | 3.00 | 446.1 | 1.84% | 0.16% | 0.00% |
| Centroid | 4.01 | 435.9 | 2.20% | 0.21% | 0.00% |
| DeepSORT | 3.93 | 555.0 | 1.93% | 0.26% | 0.00% |
| NoTracker(per-frame) | n/a | n/a | n/a | n/a | n/a |

```latex
\begin{tabular}{lrrrrr}
\toprule
Tracker & MOTA $\uparrow$ & IDF1 $\uparrow$ & HOTA $\uparrow$ & IDSW $\downarrow$ & Assoc. FPS $\uparrow$\\
\midrule
ByteTrack & 67.5 & 75.2 & 61.0 & 451 & 357\\
Centroid & 68.3 & 68.2 & 58.9 & 1583 & 4266\\
DeepSORT & 68.7 & 74.3 & 61.5 & 718 & 1\\
NoTracker(per-frame) & -6.7 & 0.7 & 6.0 & 93053 & 24627\\
\bottomrule
\end{tabular}
```