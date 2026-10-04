# DriveShieldX: project status (4 Oct 2026)

Final-year major project, B.Tech CST, UMIT (SNDT Women's University).
Team: Heli Makwana, Nisha Mandal, Tanisha Sankhe. Guide: Prof. Sonal Kadam.

## Done and verified

### Detection and tracking
- YOLOv8 detectors: helmet, seat belt, two-wheeler/triple riding, plate localisation.
  Validation mAP@50: 0.852 / 0.984 / 0.868 / 0.966.
- Tracking on UA-DETRAC (10 sequences):
  - ByteTrack: IDF1 75.2, MOTA 67.5, HOTA 61.0, 451 ID switches.
  - Compared with Centroid, DeepSORT and no tracker, with Holm-corrected paired tests.
  - ByteTrack beats Centroid (p_Holm = 0.003). It is statistically equal to DeepSORT on IDF1, at 357× lower association cost.
- Speed from trajectories: MAE 3.0 km/h against ground-truth trajectories. Implausible speeds (> 200 km/h) are held and never issued.

### Disagreement gate (helmet)
- 1204 crops; the gate withholds 66.8% of OR-rule challans.
- Human-labelled (281 events): wrong automatic challans fall from 21.0% (no gate) to 1.1% (with gate).
- 129 of the 187 withheld events were real violations. These go to officer review.

### Seat belt
- If no person is found inside a car, the windscreen area (whole, plus driver and passenger halves) is checked instead. Not yet measured.

### Plate reading (ANPR)
- Public Indian dataset (Zenodo, CC-BY), first run on 300 photos, before the fixes:
  - localisation 98.7%
  - exact read 8.0% (two-stage) and 15.0% with Indian plate-format decoding
  - legacy cascade 5.7%
- Error analysis: about half the wrong reads had characters missing, because only one OCR fragment was kept. Fixed: all fragments are now joined in reading order.
- **Held-out test (291 unseen photos), with Indian format decoding:**

  | Engine | Exact | CER | Time per plate (CPU) |
  |---|---|---|---|
  | EasyOCR (original) | 16.5% | 0.45 | 3.0 s |
  | **fast-plate-ocr (now default)** | **36.8%** | **0.22** | **0.14–0.29 s** |
  | auto (fast + EasyOCR backup) | 36.8% | 0.21 | 2.2 s (not worth it) |

  - Localisation recall on the held-out set: 99.3%.
  - EasyOCR is used automatically if fast-plate-ocr is not installed.

### Enforcement chain (end to end, tested on the laptop)
- e-Challan:
  - PDF and an SMS through the SMSGate Android gateway (allow-listed numbers only)
  - Razorpay test payments, recorded automatically by webhook or a 30 s poll
  - refunds from the officer panel
- Repeat offenders:
  - ₹500 → ₹1000 → ₹3000 plus licence suspension
  - 5 challans in a year → 3-month suspension
  - the strike cycle resets after a suspension
  - the permanent record is kept for officers, including at accidents
- MV Act fines: helmet s.194D, triple riding s.194C, seat belt s.194B.
- Automatic rule challans: detection → plate read → registry check → e-challan.
  - If the plate is missing, misread or not registered, nothing is issued and an officer sees the reason (Notice Desk → Automatic e-challans).
  - Detections from before the feature was switched on are left to officers.
- Unpaid challans: reminders on day 7 and 13 → 45-day contest → day 75 "Not to be transacted" (vehicle blocked) → day 90 court referral. Settled challans clear automatically.
- Accident alert: SMS with the nearest of 132 hospitals and 20 police stations.
- Accident detection on UCF-Crime CCTV (held-out 23 crashes + 23 normal clips, 0.51 h):
  - **Learned R3D-18 video model** (fine-tuned on 127 hand-labelled crash videos, Kaggle GPU): **7/23 crashes found**, 3.9 false alarms per hour, precision 0.78, alert about 2 s after impact. It is now the live detector when `models/accident_r3d18.pt` exists.
  - Track-based rules: 1/23 at 0 false alarms; dev-tuned rules: 3/23 at 11.8 false alarms per hour.
  - Alerts go to the control room, which confirms before dispatch.
- Fog monitor: dark-channel visibility score. Synthetic haze test, plus real footage: 10 fog + 10 clear clips from different cameras, fog-vs-clear AUC 0.95 (frames 0.97). The deployed levels were 65% correct (all clear clips kept clear, 3/10 fog clips flagged moderate+), so the level thresholds need recalibrating on more clips.
- RC lookup interface (`backend/registry.py`): a pluggable provider, with owner names masked.

### Tests and documents
- Tests: 87+ automated tests (gateways mocked; the real database is never touched).
- Research paper (`paper/`): 10 pages, Figure 4 (PR curves) on page 10.
- Presentation (`presentation/`): 46 slides. Every sentence, number and reference is taken from the paper, so the two never disagree.

## Remaining

| # | Task | Who | Effort |
|---|---|---|---|
| 1 | Run `python -m evaluation.pr_curves`, copy `pr_curves.pdf` into `paper/` and `presentation/`, recompile | Heli | 40 min |
| 2 | Remove `DSX_ESCALATION_DAY_SECONDS` from `.env`; put the 3-number SMS allowlist back | Heli | 2 min |
| 3 | New private GitHub repo and push (`START_HERE.md` part B); add Nisha and Tanisha | Heli | 15 min |
| 4 | Demo video (detection → challan → SMS → payment → escalation → accident alert) | Team | 1 h |
| 5 | Optional: Razorpay webhook via ngrok; a live ₹1 payment, then refund | Heli | 20 min |

Future work (stated as such in the paper and presentation): per-class detector metrics, seed sweeps, violation-level accuracy on labelled video, speed field calibration, fine-tuning the plate reader on Indian plates, more crash training data.

## Not claimed (say this in the viva)
- No real-time claim: 0.26–1.66 FPS on a laptop CPU.
- Plate crops from our wide-angle CCTV clips were below human-readable resolution: ANPR needs a dedicated HD plate camera, and wide views go to officer confirmation.
- Real deployment needs government access:
  - Vahan/Sarathi/Parivahan through NIC
  - a DLT-registered SMS sender
  - a government payment gateway
- The demo uses test-mode Razorpay and an Android SMS gateway.
