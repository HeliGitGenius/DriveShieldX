# DriveShieldX Industry MVP - Run Guide

## 1) Create and activate a virtual environment

### Windows PowerShell
```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 2) Run smoke test
```powershell
python smoke_test.py
```

## 3) Start the Streamlit dashboard
```powershell
streamlit run dashboard/app.py
```

## 4) Optional: Start the FastAPI websocket backend
```powershell
uvicorn backend.api_server:app --host 0.0.0.0 --port 8000 --reload
```

## Default local credentials
- Admin: `admin@speedcam.com` / `admin123`
- Officer: `officer@speedcam.com` / `officer123`

These are local app accounts stored in SQLite. They are **not** Google accounts.

## Portals & access control (NEW)
The home page splits into two separate portals:
- **Traffic Authority** — officers/admins. Sign-up is **locked behind an invite code**
  (`OVERSPEED-AUTH` in this build) so the public cannot create enforcement accounts.
- **Vehicle Owner** — open registration for citizens; they only ever see their own challans.

## 2FA setup
When you sign in for the first time, the app shows a manual secret key.
1. Open Google Authenticator or Microsoft Authenticator.
2. Add account.
3. Choose **Enter setup key manually**.
4. Paste the secret shown by DriveShieldX.
5. Choose **Time based**.
6. Enter the 6-digit code back into the app.

## Number-plate recognition (NEW)
- ANPR is now **ON by default** (EasyOCR). Recognised plates are stored in the database
  and shown across violations, notices and the owner dashboard.
- Live Monitor has a **"Number-Plate Recognition demo"** panel with deblur: upload a vehicle
  /plate photo, or use the clear/blurry sample buttons, to see the OCR read the plate — ideal
  for a viva. The first EasyOCR run downloads model weights (one-time, needs internet).
- **IMPORTANT — footage matters.** Far-away CCTV (plate < ~10px wide) cannot be read by any
  ANPR. Use closer footage where the plate is ~90+px wide. See `PLATE_RECOGNITION_GUIDE.md`.
- A **"Demo plate fallback"** toggle assigns a consistent demo plate per vehicle when a real
  read isn't possible, so the full challan flow still demonstrates end-to-end.

## E-challan escalation + overdue jump (NEW)
Repeat offenders escalate automatically (same plate / same tracked vehicle):
- 1st overspeed → warning + e-challan **₹500** (rises to **₹1000** if overdue)
- 2nd → e-challan **₹1000** (rises to **₹2000** if overdue)
- 3rd+ → e-challan **₹3000** (rises to **₹5000** if overdue) + driving-licence suspension
  (3 months, +1 per extra repeat)
Unpaid challans past the 14-day due date auto-jump to the overdue amount, exactly like a
utility/Airtel bill that increases after the deadline. Challans are generated for **every**
overspeeding vehicle — registered or not.

## Payments — realistic UPI checkout (honest scope)
Clicking **Pay** opens a real UPI checkout: a scannable `upi://pay` QR code (Google Pay /
PhonePe / Paytm understand it), an order reference, app choice, and an **"I have paid — verify"**
step that confirms settlement and records a transaction id + receipt. The QR is a genuine UPI
deep link. What it does NOT do is automatically confirm that money was received — that requires
a registered business + a payment-gateway merchant account (Razorpay/Paytm) with server-side
keys and a webhook. The verify step stands in for that gateway callback. To go live later, set
a real payee VPA in `backend/payments.py` and replace `gateway_simulate_verify()` with the
gateway's verify call — nothing else changes.

## Feature map
- Near-real-time monitoring on uploaded CCTV video and RTSP sources
- ByteTrack / centroid tracker selection
- Number-plate recognition via EasyOCR (on by default) + standalone ANPR demo
- Graduated repeat-offender e-challan escalation with licence suspension
- Challans + reports for ALL overspeeders (registered and unregistered)
- Owner dashboard: challan records, penalties, due dates, licence status, demo payments
- Notice delivery channel choice: dashboard / email / both
- Multi-camera worker manager for RTSP cameras
- PDF violation reports and PDF challan notices
- Email/SMS/dashboard alert outbox (zero-cost local fallback)
- Zones with per-zone speed limits and calibration
- System health dashboard and event logs
- FastAPI websocket backend for event streaming

## Verify without a browser
- `python test_escalation.py` — proves the escalation/fine/suspension/payment/overdue logic
- `python harness_render.py` — imports the dashboard and runs every page headlessly

## Honest note
This project implements a strong **local industry-style MVP**. It does not by itself guarantee public-cloud scale, SLA-backed latency, or production networking hardening. Those need real infrastructure, monitoring, secrets management, and deployment budgets.
