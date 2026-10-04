# Tracking evaluation (UA-DETRAC)

Sequences (10): MVI_20012, MVI_20065, MVI_39761, MVI_40152, MVI_40212, MVI_40742, MVI_40775, MVI_40891, MVI_40992, MVI_63563
Detector: deployed yolov8n (conf >= 0.35); identical cached detections replayed through every tracker.

| Tracker | MOTA | IDF1 | HOTA | IDSW | Assoc. FPS | MOTA mean±sd | IDF1 mean±sd | HOTA mean±sd |
|---|---|---|---|---|---|---|---|---|
| ByteTrack | 67.5 | 75.2 | 61.0 | 451 | 694 | 63.6±14.1 | 74.1±11.3 | 58.7±9.9 |
| Centroid | 68.3 | 68.2 | 58.9 | 1583 | 7345 | 65.5±11.9 | 67.5±10.7 | 57.1±8.9 |
| NoTracker(per-frame) | -6.7 | 0.7 | 6.0 | 93053 | 44798 | -10.2±9.1 | 0.9±0.8 | 6.5±2.9 |

## Paired tests (per-sequence, Holm-corrected)

| Metric | A | B | test | mean diff (A-B) | p | p (Holm) |
|---|---|---|---|---|---|---|
| IDF1 | ByteTrack | Centroid | paired t-test | 6.65 | 0.001422 | 0.001422 |
| IDF1 | ByteTrack | NoTracker(per-frame) | paired t-test | 73.18 | 7.969e-09 | 2.391e-08 |
| IDF1 | Centroid | NoTracker(per-frame) | paired t-test | 66.53 | 1.059e-08 | 2.391e-08 |
| MOTA | ByteTrack | Centroid | Wilcoxon signed-rank | -1.87 | 0.625 | 0.625 |
| MOTA | ByteTrack | NoTracker(per-frame) | Wilcoxon signed-rank | 73.83 | 0.001953 | 0.003906 |
| MOTA | Centroid | NoTracker(per-frame) | paired t-test | 75.70 | 1.464e-09 | 4.393e-09 |
| HOTA | ByteTrack | Centroid | paired t-test | 1.57 | 0.1927 | 0.1927 |
| HOTA | ByteTrack | NoTracker(per-frame) | paired t-test | 52.24 | 9.752e-08 | 1.95e-07 |
| HOTA | Centroid | NoTracker(per-frame) | paired t-test | 50.67 | 3.786e-08 | 1.136e-07 |

## Speed from trajectories (same estimator and s_px for tracker and ground truth)

| Tracker | MAE vs GT trajectory (km/h) | max estimate | >120 | >200 | >400 |
|---|---|---|---|---|---|
| ByteTrack | 3.00 | 446.1 | 1.84% | 0.16% | 0.00% |
| Centroid | 4.01 | 435.9 | 2.20% | 0.21% | 0.00% |
| NoTracker(per-frame) | n/a | n/a | n/a | n/a | n/a |

```latex
\begin{tabular}{lrrrrr}
\toprule
Tracker & MOTA $\uparrow$ & IDF1 $\uparrow$ & HOTA $\uparrow$ & IDSW $\downarrow$ & Assoc. FPS $\uparrow$\\
\midrule
ByteTrack & 67.5 & 75.2 & 61.0 & 451 & 694\\
Centroid & 68.3 & 68.2 & 58.9 & 1583 & 7345\\
NoTracker(per-frame) & -6.7 & 0.7 & 6.0 & 93053 & 44798\\
\bottomrule
\end{tabular}
```