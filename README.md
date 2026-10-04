# DriveShieldX — Traffic Violation Detection & Enforcement

*A final-year project extension of OverSpeedX phase-2.*

DriveShieldX **adds** three new AI violation detectors on top of the existing
OverSpeedX pipeline — **without rewriting a single line** of the original
tracker, ANPR, database, MFA, PDF-challan or Streamlit dashboard code:

| Layer   | What it does                                     | Where it lives                                                    |
|---------|--------------------------------------------------|-------------------------------------------------------------------|
| Layer 1 | YOLOv8 vehicle detection                         | `detection/multi_tracker.py` (unchanged)                          |
| Layer 1 | Centroid + ByteTrack tracking                    | `detection/centroid_tracker.py`, `multi_tracker.py` (unchanged)   |
| Layer 1 | Speed estimation                                 | `detection/speed_estimator.py` (unchanged)                        |
| Layer 1 | Number-plate recognition (ANPR, EasyOCR)         | `detection/npr.py` (unchanged)                                    |
| **Layer 2** | **Helmet detection**                             | `models/helmet_yolov8.pt` + `models/twowheeler_best.pt` (NEW)     |
| **Layer 2** | **Triple-riding detection**                       | rider-count heuristic + `twowheeler_best.pt` (NEW)                |
| **Layer 2** | **Seat-belt detection**                           | `models/seatbelt_yolov8.pt` (NEW)                                 |
| **Layer 2** | **Improved plate localiser**                      | `models/plate_best_v2.pt` (NEW)                                   |
| Layer 3 | Cumulative confirmation (2 consecutive frames)   | `detection/rule_violations.py::RuleViolationDetector` (NEW)       |
| Layer 3 | Rule-violation DB persistence                    | `database/schema.sql::RULE_VIOLATION` + `db_manager.py` (NEW helpers) |
| Layer 3 | UPI QR e-challan generation                      | `detection/rule_violations.py::upi_intent / upi_qr_png` (NEW)     |
| Layer 4 | Streamlit dashboard (Google-Authenticator MFA, owner portal, PDF challans, live graphs, notice desk) | `dashboard/app.py` (unchanged except brand + one new **Rule Violations** tab) |

Everything you asked for is preserved:
- **Google Authenticator TOTP MFA** for both admins and vehicle owners
- **Separate owner login** — owners only see their own vehicles + challans
- **PDF challan generation** (`backend/reporting.py`)
- **Live speed telemetry graph + severity mix chart + notice pipeline donut**
- **Message flow** — traffic authority → owner (email/SMS outbox)
- **Configuration** page for fine amounts, speed limits, tracker mode
- **UPI QR code** on every challan → funds go to `driveshieldx@upi`

---

## v3 — road-safety layer, disagreement gate, evaluation suite

Everything above still works exactly as before. v3 **adds**:

| Feature | Where | Notes |
|---|---|---|
| **Disagreement gate** (standalone helmet model vs combined two-wheeler model) | `detection/rule_violations.py` | agree → challan, disagree → officer review queue (`GATE_REVIEW`), no challan. `DRIVESHIELDX_GATE=0` restores the old OR rule |
| **Rider-aware crops** | `rule_violations.py::set_frame_context` | each person assigned to one bike, scale-aware, tandem groups; helmet scored only in riders' head regions (fewer false triple-riding / helmet flags) |
| **Seat-belt rule per paper** | `rule_violations.py` | 3 observations, 0.12 margin, CLAHE on dark / glare crops |
| **Two-stage ANPR** | `advanced_pipeline.py`, `npr.py` | YOLOv8 plate box → deblur/sharpen → EasyOCR (old cascade path is the fallback) |
| **Format-aware plate decoding** | `detection/plate_format.py` | fixes 0/O, 2/Z, 5/S, 8/B… by position using the Indian plate grammar (incl. BH series) |
| **Speed fixes** | `advanced_pipeline.py` | true source FPS, no frame dropping for files, implausible speeds (> 200 km/h) never become violations |
| **Fog / visibility monitor** | `detection/fog_monitor.py` | own thread; dark channel + contrast + edges relative to each camera's clear baseline; zone advisory with reduced advisory speed |
| **Accident detection** | `detection/accident_monitor.py` | impact (IoU jump) + hard stop / reversal / stalled-in-moving-traffic, confirmed only if vehicles stay stopped |
| **Police + hospital dispatch** | `backend/dispatch.py` | nearest facilities by GPS (OpenStreetMap import or manual), control-room SMS/e-mail/dashboard alert with map links |
| **Vehicle registry** | `backend/registry.py` | local registry + pluggable RC-verification API (VAHAN has no free public API), cache, document-validity flags, masked owner names |
| **Real SMS gateway (optional)** | `backend/alerts.py` | Twilio or any webhook gateway; outbox files are still always written |
| **Road Safety + Analytics pages** | `dashboard/safety_pages.py` | fog log, accidents + dispatch log, facilities map, fog calibration, registry look-up, weekday×hour and image-space heatmaps |
| **Training module** | `training/` | provenance, dataset merge, reproducible retraining with model cards, safe promotion |
| **Evaluation suite** | `evaluation/` | every "not measured" item in the paper — see `evaluation/README.md` |

New settings are documented in `.env.example` (all optional). Tests: `python -m pytest tests/test_gate.py tests/test_fog.py tests/test_accident.py tests/test_registry_dispatch.py -q`.

## One-command boot

### macOS / Linux
```bash
chmod +x start-mac.sh
./start-mac.sh
```

### Windows (PowerShell)
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
./start-windows.ps1
```

Both scripts:
1. create a Python virtual env in `.venv`
2. `pip install -r requirements.txt` (Streamlit, ultralytics, easyocr, reportlab, qrcode, plotly, opencv …)
3. apply the SQLite schema (creates the `RULE_VIOLATION` table)
4. load environment variables from `.env` (or copy `.env.example` on first run)
5. launch the Streamlit dashboard on **<http://localhost:8501>**

Open the URL and sign in with the demo credentials (see below), or from the
CLI feed a video straight to the pipeline:

```bash
python -m detection.pipeline --source /path/to/traffic.mp4 --camera-id 1 \
  --tracker centroid --enable-npr --frame-skip 3 --resize-width 720
```

The terminal will log every detected speed / plate / helmet / triple / seat-belt
event as it happens, and rows land in the `RULE_VIOLATION` table for the
dashboard to render.

---

## UPI configuration

`.env.example` ships with your VPA already set. Copy it to `.env` on first boot
and edit if needed:

```env
UPI_ID=driveshieldx@upi
UPI_PAYEE_NAME=DriveShieldX Traffic Authority
CONTACT_PHONE=+91-9000000000
```

Every rule-violation e-challan renders a **real** `upi://pay?...` intent QR:
scanning it with Google Pay / PhonePe / Paytm / BHIM opens their payment screen
and sends the fine straight to your VPA. The Streamlit **Rule Violations** tab
(authority) and **My Dashboard** page (owner) both show the QR.

---

## Demo credentials

Every new database is created with these accounts (change the passwords from
the *Users* tab once you're logged in):

| Role              | Email                    | Password     |
|-------------------|--------------------------|--------------|
| Admin             | `admin@speedcam.com`     | `admin123`   |
| Traffic authority | `officer@speedcam.com`   | `officer123` |

Vehicle owners create their own account with *Sign up as vehicle owner* on the
landing page; MFA is optional at first, enable it under *Security*.

---

## Getting real test videos

External downloads are blocked from inside this preview environment, but on
your local machine any of these datasets work great:

| Source | What to grab | Link |
|--------|--------------|------|
| **Kaggle** | *Sample Videos for Helmet Detection on YOLOv8* — motorcycle + rider clips | https://www.kaggle.com/datasets/ayushraj2349/sample-videos-for-helmet-detection-on-yolov8 |
| Kaggle | Helmet Detection Dataset | https://www.kaggle.com/datasets/andrewmvd/helmet-detection |
| Roboflow Universe | Motorcycle Helmets — export as video | https://universe.roboflow.com/miguel-diaz-lpenf/motorcycle-helmets-video |
| Pexels | free stock footage | https://www.pexels.com/search/videos/motorcycle%20traffic/ |
| Pixabay | free stock footage | https://pixabay.com/videos/search/motorcycle%20traffic/ |
| YouTube via `yt-dlp` | any public CCTV clip | `pip install yt-dlp && yt-dlp -f mp4 <URL>` |

Drop the downloaded `.mp4` into the *Live Monitor* upload box and press
**Start Live Processing** — the terminal will log every helmet / triple /
seat-belt event as it happens, and the *Rule Violations* tab will fill up.

---

## Architecture in one diagram

```
CCTV video / RTSP / webcam
   │
   ▼
YOLOv8 vehicle detection          (detection/multi_tracker.py)
   │
   ▼
ByteTrack or Centroid tracker     (detection/multi_tracker.py + centroid_tracker.py)
   │
   ▼
Speed estimator + ANPR (EasyOCR)  (detection/speed_estimator.py + npr.py)
   │
   ├──► Speed violations          → VIOLATION table
   │
   └──► DriveShieldX Layer 2      (detection/rule_violations.py)   ← NEW
              │
              ├── helmet_yolov8.pt        (helmet / no_helmet)
              ├── twowheeler_best.pt      (helmet / no-helmet / triple_riding)
              ├── seatbelt_yolov8.pt      (seatbelt / no_seatbelt)
              └── plate_best_v2.pt        (License_Plate)
              │
              ▼
Temporal confirmation (2 consecutive frames)
              │
              ▼
   RULE_VIOLATION table  ─►  Streamlit dashboard
              │                    │
              ▼                    ├── authority "Rule Violations" page
   PDF e-challan                   └── owner "My Dashboard" page
              │
              ▼
   UPI QR (upi://pay?pa=driveshieldx@upi…)  → real payment
```

Nothing in the original OverSpeedX_phase2 tree was removed. The only *modified*
files are:
- `dashboard/app.py`  — brand rename + new *Rule Violations* tab + UPI QR block on the owner page
- `detection/advanced_pipeline.py` — one injected call to `RuleViolationDetector.observe()` + person-box collection
- `database/schema.sql`, `database/db_manager.py` — new `RULE_VIOLATION` table + helpers
- `backend/payments.py` — UPI defaults now driven by env

The rest of the changes are **new files only** (`detection/rule_violations.py`,
5 YOLO weights in `models/`, `.env.example`, start scripts).

---

## Stop the dashboard

```bash
./stop-mac.sh          # macOS / Linux
.\stop-windows.ps1     # Windows
```

## Online payments (Razorpay)
Real gateway integration for e-challans: Razorpay Payment Link per challan, signed redirect, webhook
(`POST /razorpay/webhook`) and status reconciliation, idempotent settlement, confirmation SMS/e-mail and refunds.
Test and live keys are kept apart (`DSX_PAYMENT_MODE`), live mode is capped by `DSX_LIVE_MAX_AMOUNT`.
Without keys the original UPI-QR / card demo checkout is used. Setup: `docs/PAYMENTS_RAZORPAY.md`.
