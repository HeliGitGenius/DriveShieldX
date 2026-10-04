# DriveShieldX — Complete Project Navigation & Architecture Guide

> **Purpose:** This document is a navigation map for understanding the DriveShieldX codebase file-by-file.
>
> It is designed to answer:
> - Where does the application start?
> - Which file calls which file?
> - Where does video enter?
> - Where does YOLO detect vehicles?
> - Where does tracking happen?
> - Where is speed calculated?
> - Where are violations decided?
> - Where are number plates processed?
> - Where is data stored?
> - Where are reports/alerts generated?
> - Where does the API/backend fit?
> - Where should the payment gateway fit?

---

# 1. DriveShieldX — High-Level Architecture

The project can be understood as these major layers:

```text
┌─────────────────────────────────────────────────────────────┐
│                    USER / OPERATOR                          │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                  PRESENTATION / UI                          │
│                                                             │
│  dashboard/app.py                                           │
│  Streamlit Dashboard                                        │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                  BACKEND / API LAYER                        │
│                                                             │
│  backend/api_server.py                                      │
│  backend/stream_manager.py                                  │
│  backend/event_bus.py                                       │
│  backend/alerts.py                                          │
│  backend/reporting.py                                       │
│  backend/payments.py                                        │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                COMPUTER VISION PIPELINE                     │
│                                                             │
│  detection/pipeline.py                                      │
│  detection/advanced_pipeline.py                             │
│  detection/vehicle_detector.py                              │
│  detection/centroid_tracker.py                              │
│  detection/multi_tracker.py                                 │
│  detection/speed_estimator.py                               │
│  detection/rule_violations.py                               │
│  detection/zone_utils.py                                    │
│  detection/anpr_demo.py                                     │
│  detection/npr.py                                           │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                     DATA LAYER                              │
│                                                             │
│  database/schema.sql                                        │
│  database/db_manager.py                                     │
│  database/overspeed.db                                      │
│  data/                                                       │
│  models/                                                     │
│  snapshots/                                                  │
│  reports/                                                    │
└─────────────────────────────────────────────────────────────┘
```

---

# 2. IMPORTANT: Actual Call Flow vs Folder Structure

The folder structure tells us what files exist.

The **actual runtime architecture** is determined by:

```text
imports
   +
function calls
   +
class instantiation
   +
API routes
   +
database calls
```

Therefore, the arrows in this document are a **navigation architecture based on the files visible in the project**. Before changing production behavior, verify each connection by opening the referenced file and following its imports/calls.

---

# 3. Complete Project Structure

Based on the project structure visible in the screenshots:

```text
DriveShieldX/
│
├── .audit_output/
│   └── AUDIT.md
│
├── .pytest_cache/
│
├── .venv/
│   ├── bin/
│   ├── include/
│   ├── lib/
│   └── ...
│
├── backend/
│   ├── __pycache__/
│   ├── models/
│   ├── __init__.py
│   ├── alerts.py
│   ├── api_server.py
│   ├── event_bus.py
│   ├── health.py
│   ├── logger.py
│   ├── payments.py
│   ├── reporting.py
│   ├── requirements-vision.txt
│   ├── requirements.txt
│   └── stream_manager.py
│
├── dashboard/
│   ├── __init__.py
│   └── app.py
│
├── data/
│   └── frames/
│
├── database/
│   ├── __pycache__/
│   ├── __init__.py
│   ├── db_manager.py
│   ├── overspeed.db
│   └── schema.sql
│
├── detection/
│   ├── __pycache__/
│   ├── __init__.py
│   ├── advanced_pipeline.py
│   ├── anpr_demo.py
│   ├── centroid_tracker.py
│   ├── multi_tracker.py
│   ├── npr.py
│   ├── pipeline.py
│   ├── rule_violations.py
│   ├── speed_estimator.py
│   ├── vehicle_detector.py
│   └── zone_utils.py
│
├── logs/
│   ├── alert_outbox/
│   └── overspeed.log
│
├── models/
│   ├── helmet_yolov8.pt
│   ├── plate_best_v2.pt
│   ├── seatbelt_yolov8.pt
│   ├── twowheeler_best.pt
│   └── yolov8n.pt
│
├── reports/
│   ├── challan_notice_*.pdf
│   └── violations_report_*.pdf
│
├── snapshots/
│   └── violation / detection images
│
├── tests/
│   └── test_driveshieldx.py
│
├── utils/
│   ├── __init__.py
│   ├── dataset_setup.py
│   └── demo_runner.py
│
├── __init__.py
├── .env
├── .env.example
├── harness_render.py
├── PLATE_RECOGNITION_GUIDE.md
├── README.md
├── RUN_FIRST.md
├── smoke_test.py
├── start-mac.sh
├── start-windows.ps1
├── stop-mac.sh
├── stop-windows.ps1
├── test_escalation.py
├── test_seatbelt_direct.py
├── test_seatbelt_person.py
├── test_seatbelt_person_backup.py
├── test_seatbelt.py
├── test_vehicle_direct.py
├── test_vehicle.py
├── visualize_seatbelt.py
├── VIVA_GUIDE.md
└── yolov8n.pt
```

---

# 4. START HERE — Application Entry Point

You currently run:

```bash
python -m streamlit run dashboard/app.py
```

Therefore the first file to understand is:

```text
dashboard/app.py
```

## Navigation

```text
dashboard/app.py
       │
       ├── imports backend modules
       │
       ├── imports detection modules
       │
       ├── creates UI
       │
       ├── accepts user input
       │
       └── calls processing functions
```

## Questions to answer while reading

1. What is the first function that executes?
2. What modules are imported?
3. Is video uploaded or selected?
4. Which pipeline function is called?
5. How are results displayed?
6. Is the dashboard calling the backend API or Python functions directly?
7. Where is database information loaded?

---

# 5. Main Computer Vision Pipeline

The main conceptual flow is:

```text
Video / Camera / Image
          │
          ▼
┌────────────────────────────┐
│ vehicle_detector.py        │
│ YOLO vehicle detection     │
└──────────────┬─────────────┘
               │
               │ bounding boxes
               ▼
┌────────────────────────────┐
│ centroid_tracker.py        │
│ Vehicle tracking / IDs     │
└──────────────┬─────────────┘
               │
               │ tracked objects
               ▼
┌────────────────────────────┐
│ speed_estimator.py         │
│ Position → speed           │
└──────────────┬─────────────┘
               │
               │ speed + vehicle ID
               ▼
┌────────────────────────────┐
│ zone_utils.py              │
│ Zone / region logic        │
└──────────────┬─────────────┘
               │
               ▼
┌────────────────────────────┐
│ rule_violations.py         │
│ Violation decision logic   │
└──────────────┬─────────────┘
               │
               ├───────────────┐
               ▼               ▼
          ANPR / NPR       Snapshots
               │
               ▼
          Plate Number
               │
               └───────────────┐
                               ▼
                         Database / Reports
```

---

# 6. `detection/pipeline.py`

## Role

This should be treated as one of the first files to inspect after `dashboard/app.py`.

Think of it as an **orchestrator**.

```text
pipeline.py
     │
     ├── detector
     ├── tracker
     ├── speed estimator
     ├── rule checker
     ├── zone logic
     └── output/storage
```

## Navigation endpoint

Find:

```python
class ...
```

then:

```python
def ...
```

and especially functions with names similar to:

```text
run
process
process_video
detect
analyze
```

## Goal

Understand:

```text
INPUT
Video frame / video source

        ↓

PROCESS
Call detection/tracking/rules

        ↓

OUTPUT
Processed frame + vehicle/violation data
```

---

# 7. `detection/advanced_pipeline.py`

## Role

This is likely a higher-level or extended processing pipeline.

Possible conceptual relationship:

```text
pipeline.py
      │
      └── basic processing

advanced_pipeline.py
      │
      ├── vehicle detection
      ├── tracking
      ├── additional models
      ├── ANPR
      ├── violation logic
      └── advanced output
```

Do not assume that `advanced_pipeline.py` is always called.

### Verify

Search for:

```text
advanced_pipeline
```

through the project.

Find:

```text
WHO IMPORTS IT?
WHO CALLS IT?
```

That tells you whether it is part of the active runtime path or a separate/demo pipeline.

---

# 8. `detection/vehicle_detector.py`

## Role

Vehicle detection.

Conceptually:

```text
Frame
  │
  ▼
YOLOv8
  │
  ▼
Detections
  │
  ├── class
  ├── confidence
  └── bounding box
```

Example:

```text
Vehicle:
    class = car
    confidence = 0.92
    bbox = [x1, y1, x2, y2]
```

## Related model

```text
models/yolov8n.pt
```

Potential connection:

```text
vehicle_detector.py
       │
       ▼
models/yolov8n.pt
       │
       ▼
vehicle detections
```

---

# 9. `detection/centroid_tracker.py`

## Role

Maintains vehicle identity across frames.

YOLO says:

```text
"There is a car here."
```

The tracker says:

```text
"This car is Vehicle ID 7."
```

## Flow

```text
Frame 1
   │
   ▼
Detection
   │
   ▼
Centroid
   │
   ▼
Vehicle ID = 1


Frame 2
   │
   ▼
Detection
   │
   ▼
Centroid
   │
   ▼
Compare with previous centroid
   │
   ▼
Vehicle ID = 1
```

## Important functions to find

```text
__init__()
register()
deregister()
update()
```

The most important function is normally:

```text
update()
```

because it receives new detections and associates them with existing tracked vehicles.

---

# 10. `detection/multi_tracker.py`

## Role

Inspect this file after understanding `centroid_tracker.py`.

The filename suggests a higher-level tracking component, but the exact relationship must be verified from imports and calls.

Possible structure:

```text
pipeline
   │
   ▼
multi_tracker
   │
   ├── tracker 1
   ├── tracker 2
   └── tracker N
```

or:

```text
pipeline
   │
   ▼
multi_tracker
   │
   ▼
centroid_tracker
```

### Navigation rule

Do not assume.

Search:

```text
from detection.multi_tracker ...
MultiTracker(...)
```

and:

```text
multi_tracker.
```

---

# 11. `detection/speed_estimator.py`

## Role

Converts movement of a tracked object into an estimated speed.

Conceptually:

```text
Vehicle ID
    │
    ▼
Centroid history
    │
    ▼
Distance moved
    │
    ▼
Time / frame difference
    │
    ▼
Speed calculation
    │
    ▼
km/h
```

Example:

```text
Vehicle 7

Frame 100 → (200, 300)
Frame 120 → (240, 350)

        ↓

Pixel movement
        ↓
Calibration / conversion
        ↓
Estimated speed
```

## Important question

Find the exact point where:

```text
(x, y) movement
```

becomes:

```text
speed in km/h
```

---

# 12. `detection/zone_utils.py`

## Role

Provides zone/region-related utilities.

Possible concepts:

```text
point inside polygon
vehicle inside zone
line crossing
ROI
speed measurement zone
```

Conceptual flow:

```text
Tracked Vehicle
      │
      ▼
Current position
      │
      ▼
Zone check
   ┌──┴──┐
   │     │
 YES     NO
   │     │
   ▼     ▼
Process Ignore
```

Verify the exact functions in the file.

---

# 13. `detection/rule_violations.py`

## Role

Turns raw detection information into violations.

Potential inputs:

```text
vehicle ID
speed
vehicle class
helmet result
seatbelt result
person count
zone information
```

Potential outputs:

```text
overspeeding
no helmet
no seatbelt
three seater
other configured violations
```

Conceptual flow:

```text
Vehicle Data
     │
     ├── speed
     ├── helmet
     ├── seatbelt
     ├── passenger count
     └── vehicle type
             │
             ▼
      Rule Evaluation
             │
             ▼
         Violation
```

---

# 14. YOLO Specialized Models

Your `models/` directory contains multiple models.

```text
models/
│
├── yolov8n.pt
│       └── main/general object detection
│
├── helmet_yolov8.pt
│       └── helmet detection
│
├── seatbelt_yolov8.pt
│       └── seatbelt detection
│
├── plate_best_v2.pt
│       └── number plate detection
│
└── twowheeler_best.pt
        └── two-wheeler related detection
```

The exact class labels and usage should be verified inside the Python files that load these models.

---

# 15. Helmet Detection Flow

Potential architecture:

```text
Vehicle / Person
      │
      ▼
helmet_yolov8.pt
      │
      ▼
Helmet detected?
   ┌──┴──┐
  YES    NO
   │      │
   ▼      ▼
Valid   Violation
```

Find where:

```text
helmet_yolov8.pt
```

is loaded.

Then follow the caller.

---

# 16. Seatbelt Detection Flow

Potential architecture:

```text
Vehicle / Person
      │
      ▼
seatbelt_yolov8.pt
      │
      ▼
Seatbelt detected?
   ┌──┴──┐
  YES    NO
   │      │
   ▼      ▼
Valid   No-seatbelt
```

Related files visible in the project include:

```text
test_seatbelt.py
test_seatbelt_direct.py
test_seatbelt_person.py
test_seatbelt_person_backup.py
visualize_seatbelt.py
```

Use these test files to understand how the seatbelt component is expected to behave.

---

# 17. ANPR / Number Plate Flow

Relevant files:

```text
detection/anpr_demo.py
detection/npr.py
models/plate_best_v2.pt
PLATE_RECOGNITION_GUIDE.md
```

Conceptual flow:

```text
Vehicle
   │
   ▼
Vehicle bounding box
   │
   ▼
Plate detector
plate_best_v2.pt
   │
   ▼
Number plate crop
   │
   ▼
OCR / recognition
   │
   ▼
Plate text
   │
   ▼
Vehicle / violation record
```

Example:

```text
Vehicle ID = 17
        │
        ▼
Plate = MH12AB1234
        │
        ▼
Violation = Overspeeding
```

---

# 18. `database/schema.sql`

## Role

Defines the database structure.

Read this file **before** deeply reading `db_manager.py`.

Navigation:

```text
schema.sql
    │
    ├── tables
    ├── columns
    ├── primary keys
    ├── relationships
    └── constraints
```

Ask:

```text
What entities does DriveShieldX store?
```

For example:

```text
Vehicle
Violation
Challan
Payment
Event
etc.
```

Only include entities that actually exist in the schema.

---

# 19. `database/db_manager.py`

## Role

Database access layer.

Conceptual architecture:

```text
Detection / Backend
        │
        ▼
   db_manager.py
        │
        ▼
   SQLite database
        │
        ▼
database/overspeed.db
```

Look for functions similar to:

```text
insert
create
get
update
delete
query
save_violation
```

The exact names must be verified from the code.

---

# 20. `database/overspeed.db`

## Role

Persistent application data.

Do not manually modify this database while the application is running.

Treat it as:

```text
Python code
   ↓
db_manager.py
   ↓
overspeed.db
```

rather than:

```text
Python code
   ↓
direct SQL everywhere
```

---

# 21. `backend/api_server.py`

## Role

Backend/API entry point.

Look for:

```text
FastAPI
Flask
routes
routers
GET
POST
PUT
DELETE
```

Potential architecture:

```text
Client / Dashboard
       │
       ▼
api_server.py
       │
       ├── detection
       ├── database
       ├── reporting
       ├── alerts
       └── payments
```

For every API endpoint, record:

```text
HTTP method
URL
request body
function called
response
```

Create a small map while reading:

```text
POST /something
      ↓
function_name()
      ↓
module/file
      ↓
response
```

---

# 22. `backend/stream_manager.py`

## Role

Likely manages video/camera streams.

Potential flow:

```text
Camera / Video
      │
      ▼
stream_manager.py
      │
      ▼
Frames
      │
      ▼
Detection pipeline
```

Find:

```text
stream
camera
video
frame
start
stop
```

and trace who calls these functions.

---

# 23. `backend/event_bus.py`

## Role

Potential event-driven communication layer.

Conceptually:

```text
Producer
   │
   ▼
Event Bus
   │
   ├── Database
   ├── Alert
   ├── Report
   └── Dashboard
```

Possible events:

```text
VIOLATION_DETECTED
PAYMENT_SUCCESS
PAYMENT_FAILED
STREAM_STARTED
STREAM_STOPPED
```

Only document events that actually exist in the code.

---

# 24. `backend/alerts.py`

## Role

Notification/alert handling.

Potential flow:

```text
Violation / Event
       │
       ▼
alerts.py
       │
       ▼
Alert / notification
```

Check the implementation before assuming whether alerts are:

```text
email
SMS
UI
file outbox
webhook
```

Your screenshot shows:

```text
logs/alert_outbox/
```

so inspect how `alerts.py` interacts with that directory.

---

# 25. `backend/reporting.py`

## Role

Generate reports/receipts/violation documents.

Existing project output includes:

```text
reports/
├── challan_notice_9.pdf
├── challan_notice_50.pdf
├── challan_notice_999.pdf
├── challan_notice_12345.pdf
└── violations_report_*.pdf
```

Conceptual flow:

```text
Violation
    │
    ▼
Database
    │
    ▼
reporting.py
    │
    ▼
PDF / Report
    │
    ▼
reports/
```

---

# 26. `backend/payments.py`

## Role

Payment gateway integration.

Since this file already exists, treat it as the main payment service file.

Recommended architecture:

```text
Dashboard
    │
    ▼
api_server.py
    │
    ▼
payments.py
    │
    ▼
Razorpay
```

Payment flow:

```text
Unpaid Challan
      │
      ▼
Pay Now
      │
      ▼
POST /payments/create-order
      │
      ▼
payments.py
      │
      ▼
Razorpay Order
      │
      ▼
Checkout
      │
      ▼
User Payment
      │
      ▼
Payment Response
      │
      ▼
POST /payments/verify
      │
      ▼
Signature Verification
      │
   ┌──┴──┐
 VALID  INVALID
   │       │
   ▼       ▼
PAID     Reject
   │
   ▼
Database
```

Keep the Razorpay secret key on the backend.

---

# 27. Payment Files to Modify

For the payment feature, navigate:

```text
.env
    ↓
backend/payments.py
    ↓
backend/api_server.py
    ↓
database/schema.sql
    ↓
database/db_manager.py
    ↓
dashboard/app.py
```

Optional:

```text
backend/reporting.py
backend/alerts.py
backend/event_bus.py
```

Do NOT put payment gateway code into:

```text
detection/*.py
```

---

# 28. `backend/health.py`

## Role

Application/backend health checks.

Conceptual:

```text
Health endpoint
      │
      ├── backend available?
      ├── database available?
      └── dependencies available?
```

This is operational infrastructure rather than computer-vision logic.

---

# 29. `backend/logger.py`

## Role

Centralized logging.

Potential flow:

```text
Any backend component
       │
       ▼
logger.py
       │
       ▼
logs/
```

Your screenshot shows:

```text
logs/
├── alert_outbox/
└── overspeed.log
```

When debugging production behavior, inspect logs before changing code.

---

# 30. `utils/dataset_setup.py`

## Role

Dataset preparation/setup.

Conceptual:

```text
Dataset
   ↓
dataset_setup.py
   ↓
Prepared data
   ↓
Training/testing
```

This is not part of the normal runtime detection flow unless explicitly imported.

---

# 31. `utils/demo_runner.py`

## Role

Likely provides a convenient way to run demos/test scenarios.

Treat it as:

```text
Demo runner
     ↓
pipeline / detection
     ↓
output
```

Verify its imports to find the exact pipeline it uses.

---

# 32. Test Files

The project contains multiple test/debug scripts.

Important ones visible:

```text
tests/test_driveshieldx.py

test_escalation.py

test_seatbelt.py
test_seatbelt_direct.py
test_seatbelt_person.py
test_seatbelt_person_backup.py

test_vehicle.py
test_vehicle_direct.py

visualize_seatbelt.py

smoke_test.py
```

## How to use them

Do not treat these as application entry points.

Use them to understand expected behavior.

For example:

```text
test_vehicle.py
      ↓
What vehicle detector expects

test_seatbelt.py
      ↓
What seatbelt detector expects

test_escalation.py
      ↓
How violation escalation behaves

smoke_test.py
      ↓
High-level system health
```

---

# 33. Root-Level Run Scripts

## `start-mac.sh`

Likely Mac startup helper.

## `start-windows.ps1`

Windows startup helper.

## `stop-mac.sh`

Stops Mac processes/services.

## `stop-windows.ps1`

Stops Windows processes/services.

## `smoke_test.py`

High-level quick test.

## `harness_render.py`

Likely test/render/harness utility.

Always inspect these before assuming they are production runtime files.

---

# 34. Configuration Files

## `.env`

Private environment configuration.

Potential values include:

```text
database configuration
API configuration
model configuration
payment credentials
other secrets
```

Never commit secrets.

## `.env.example`

Safe template.

Use it to document required variables without real credentials.

---

# 35. Dependency Files

```text
backend/requirements.txt
backend/requirements-vision.txt
```

Use these to understand which external packages the backend/vision components require.

The virtual environment is:

```text
.venv/
```

Do not modify package files manually inside `.venv`.

---

# 36. Logs

```text
logs/
├── alert_outbox/
└── overspeed.log
```

Conceptual:

```text
Application
    │
    ▼
logger.py
    │
    ▼
logs/
```

Use logs to debug:

```text
detection failures
speed calculations
alerts
backend errors
```

depending on what the logger actually writes.

---

# 37. Models

```text
models/
├── yolov8n.pt
├── helmet_yolov8.pt
├── seatbelt_yolov8.pt
├── plate_best_v2.pt
└── twowheeler_best.pt
```

These are **model artifacts**, not Python business logic.

Navigation:

```text
Python detector
      │
      ▼
loads .pt model
      │
      ▼
inference
      │
      ▼
prediction
```

---

# 38. Snapshots

```text
snapshots/
```

These are output artifacts.

Potential flow:

```text
Violation detected
      │
      ▼
Capture frame
      │
      ▼
snapshots/
```

Find the exact saving function to verify which violations create snapshots.

---

# 39. Reports

```text
reports/
```

Output artifacts.

Potential flow:

```text
Violation / Challan
       │
       ▼
reporting.py
       │
       ▼
PDF
       │
       ▼
reports/
```

---

# 40. The Complete Detection Architecture

```text
                    VIDEO / IMAGE
                         │
                         ▼
              ┌────────────────────┐
              │ vehicle_detector.py│
              │       YOLO         │
              └─────────┬──────────┘
                        │
                  Bounding Boxes
                        │
                        ▼
              ┌────────────────────┐
              │ centroid_tracker.py│
              │ Vehicle ID tracking│
              └─────────┬──────────┘
                        │
                   Vehicle IDs
                        │
                        ▼
              ┌────────────────────┐
              │ multi_tracker.py   │
              │ if used by pipeline│
              └─────────┬──────────┘
                        │
                        ▼
              ┌────────────────────┐
              │ speed_estimator.py │
              │ speed calculation  │
              └─────────┬──────────┘
                        │
                        ▼
              ┌────────────────────┐
              │ zone_utils.py      │
              │ zone/ROI logic     │
              └─────────┬──────────┘
                        │
                        ▼
              ┌────────────────────┐
              │ rule_violations.py │
              │ rule evaluation    │
              └─────────┬──────────┘
                        │
             ┌──────────┼──────────┐
             │          │          │
             ▼          ▼          ▼
          Helmet     Seatbelt    Plate
             │          │          │
             │          │          ▼
             │          │      ANPR/NPR
             │          │          │
             └──────────┼──────────┘
                        ▼
                    VIOLATION
                        │
             ┌──────────┼─────────────┐
             │          │             │
             ▼          ▼             ▼
          Snapshot   Database      Report
```

---

# 41. Complete Backend Architecture

```text
                    Client / Dashboard
                           │
                           ▼
                  ┌─────────────────┐
                  │ api_server.py   │
                  │ API endpoints   │
                  └───────┬─────────┘
                          │
             ┌────────────┼────────────┐
             │            │            │
             ▼            ▼            ▼
       stream_manager   payments    detection
             │            │            │
             │            │            ▼
             │            │       pipeline.py
             │            │
             ▼            ▼
         Video stream   Razorpay
             │
             └──────────────┐
                            ▼
                      event_bus.py
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
             alerts.py            reporting.py
                 │                     │
                 ▼                     ▼
              alerts                reports
```

---

# 42. Database Architecture

```text
                 Application
                     │
                     ▼
              db_manager.py
                     │
                     ▼
               schema.sql
                     │
                     ▼
              overspeed.db
```

Do not think of `schema.sql` as something called on every request.

It describes the database structure.

`db_manager.py` is the runtime code that performs database operations.

---

# 43. End-to-End Violation Flow

This is the most important flow to understand.

```text
                     CAMERA / VIDEO
                           │
                           ▼
                       FRAME
                           │
                           ▼
                  vehicle_detector.py
                           │
                           ▼
                     YOLO DETECTION
                           │
                           ▼
                     BOUNDING BOX
                           │
                           ▼
                  centroid_tracker.py
                           │
                           ▼
                      VEHICLE ID
                           │
                           ▼
                  speed_estimator.py
                           │
                           ▼
                        SPEED
                           │
                           ▼
                    zone_utils.py
                           │
                           ▼
                rule_violations.py
                           │
                           ▼
                       VIOLATION
                           │
                ┌──────────┼──────────┐
                ▼          ▼          ▼
             Snapshot   Database    ANPR
                                      │
                                      ▼
                                 PLATE NUMBER
                                      │
                                      ▼
                               Violation Record
                                      │
                                      ▼
                               reporting.py
                                      │
                                      ▼
                                   PDF
```

---

# 44. End-to-End Challan Flow

```text
Violation
    │
    ▼
Violation Record
    │
    ▼
Challan / Fine
    │
    ▼
Database
    │
    ▼
Dashboard
    │
    ▼
User sees fine
```

---

# 45. End-to-End Payment Flow

```text
                 UNPAID CHALLAN
                       │
                       ▼
                dashboard/app.py
                       │
                  [ PAY NOW ]
                       │
                       ▼
              api_server.py
                       │
                       ▼
               payments.py
                       │
                       ▼
                  Razorpay
                       │
                       ▼
              Razorpay Checkout
                       │
                       ▼
                   USER PAYS
                       │
                       ▼
             Payment Response
                       │
                       ▼
              api_server.py
                       │
                       ▼
               payments.py
                       │
                       ▼
             Signature Verify
                       │
                 ┌─────┴─────┐
                 ▼           ▼
               VALID       INVALID
                 │           │
                 ▼           ▼
               PAID        REJECT
                 │
                 ▼
             db_manager.py
                 │
                 ▼
            overspeed.db
                 │
                 ▼
          Dashboard = PAID
```

---

# 46. Full System Architecture

```text
                           USER
                            │
                            ▼
                 ┌────────────────────┐
                 │ dashboard/app.py   │
                 │ Streamlit UI       │
                 └─────────┬──────────┘
                           │
                           ▼
                 ┌────────────────────┐
                 │ backend/api_server │
                 │ API layer          │
                 └─────────┬──────────┘
                           │
           ┌───────────────┼────────────────┐
           │               │                │
           ▼               ▼                ▼
      Stream Manager   Detection        Payments
           │               │                │
           │               ▼                ▼
           │         pipeline.py        Razorpay
           │               │
           │       ┌───────┼────────┐
           │       │       │        │
           │       ▼       ▼        ▼
           │    YOLO    Tracker   Rules
           │
           └───────────────┐
                           │
                           ▼
                    Event Bus / Backend
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
          Alerts        Reports       Database
             │             │             │
             ▼             ▼             ▼
          Outbox        PDFs       overspeed.db
```

---

# 47. Recommended Learning Order

Do NOT read the entire project randomly.

Use this order:

```text
PHASE 1 — ENTRY
│
├── dashboard/app.py
│
└── Identify the first pipeline/API function
        │
        ▼

PHASE 2 — PIPELINE
│
├── detection/pipeline.py
└── detection/advanced_pipeline.py
        │
        ▼

PHASE 3 — DETECTION
│
└── detection/vehicle_detector.py
        │
        ▼

PHASE 4 — TRACKING
│
├── detection/centroid_tracker.py
└── detection/multi_tracker.py
        │
        ▼

PHASE 5 — SPEED / ZONES
│
├── detection/speed_estimator.py
└── detection/zone_utils.py
        │
        ▼

PHASE 6 — VIOLATIONS
│
└── detection/rule_violations.py
        │
        ▼

PHASE 7 — ANPR
│
├── detection/anpr_demo.py
├── detection/npr.py
└── PLATE_RECOGNITION_GUIDE.md
        │
        ▼

PHASE 8 — DATABASE
│
├── database/schema.sql
└── database/db_manager.py
        │
        ▼

PHASE 9 — BACKEND
│
├── backend/api_server.py
├── backend/stream_manager.py
├── backend/event_bus.py
├── backend/alerts.py
├── backend/reporting.py
└── backend/logger.py
        │
        ▼

PHASE 10 — PAYMENT
│
├── backend/payments.py
├── api_server.py
├── db_manager.py
└── dashboard/app.py
        │
        ▼

PHASE 11 — TESTING
│
├── tests/test_driveshieldx.py
├── smoke_test.py
├── test_vehicle*.py
├── test_seatbelt*.py
└── test_escalation.py
```

---

# 48. How to Read Every Python File

For every file, use this exact checklist.

## Step 1 — Imports

Look at:

```python
import ...
from ... import ...
```

Ask:

```text
What other files does this file depend on?
```

---

## Step 2 — Classes

Find:

```python
class ...
```

Ask:

```text
What object does this class represent?
```

---

## Step 3 — Constructor

Find:

```python
def __init__(...)
```

Ask:

```text
What state does this object maintain?
```

---

## Step 4 — Public Functions

Find:

```python
def ...
```

Ask:

```text
Who calls this function?
What does it receive?
What does it return?
```

---

## Step 5 — External Calls

Look for:

```text
model(...)
database(...)
requests(...)
API(...)
event_bus(...)
```

These calls tell you where the execution moves next.

---

## Step 6 — Return Values

Always identify:

```text
INPUT → PROCESSING → OUTPUT
```

This is the fastest way to understand an unfamiliar Python project.

---

# 49. Your Most Important Call-Graph Exercise

For each file, create a small note:

```text
FILE:
dashboard/app.py

CALLS:
?

INPUT:
?

OUTPUT:
?

NEXT FILE:
?
```

Then:

```text
FILE:
pipeline.py

CALLS:
?

INPUT:
?

OUTPUT:
?

NEXT FILE:
?
```

Continue until you have:

```text
app.py
 ↓
pipeline.py
 ↓
vehicle_detector.py
 ↓
centroid_tracker.py
 ↓
speed_estimator.py
 ↓
rule_violations.py
 ↓
db_manager.py
 ↓
reporting.py
```

This is your **real call graph**.

---

# 50. Golden Rule for Navigating DriveShieldX

When you get lost, ask only three questions:

```text
1. WHO CALLED THIS FILE?
          ↓
2. WHAT DATA CAME INTO THIS FILE?
          ↓
3. WHERE DOES THIS FILE SEND THE DATA NEXT?
```

If you answer these three questions repeatedly, you can navigate almost the entire codebase.

---

# 51. Quick File Responsibility Table

| File | Main responsibility | Layer |
|---|---|---|
| `dashboard/app.py` | Streamlit UI | Presentation |
| `backend/api_server.py` | API endpoints | Backend |
| `backend/stream_manager.py` | Stream management | Backend |
| `backend/event_bus.py` | Event communication | Backend |
| `backend/alerts.py` | Alerts | Backend |
| `backend/reporting.py` | Reports | Backend |
| `backend/payments.py` | Payment gateway | Backend |
| `backend/health.py` | Health checks | Infrastructure |
| `backend/logger.py` | Logging | Infrastructure |
| `detection/pipeline.py` | Main CV orchestration | Detection |
| `detection/advanced_pipeline.py` | Extended CV pipeline | Detection |
| `detection/vehicle_detector.py` | YOLO detection | Detection |
| `detection/centroid_tracker.py` | Vehicle IDs/tracking | Detection |
| `detection/multi_tracker.py` | Higher-level tracking | Detection |
| `detection/speed_estimator.py` | Speed calculation | Detection |
| `detection/zone_utils.py` | Zone/ROI utilities | Detection |
| `detection/rule_violations.py` | Violation rules | Detection |
| `detection/anpr_demo.py` | ANPR flow/demo | Detection |
| `detection/npr.py` | Plate recognition | Detection |
| `database/schema.sql` | Database structure | Data |
| `database/db_manager.py` | Database operations | Data |
| `utils/dataset_setup.py` | Dataset setup | Utility |
| `utils/demo_runner.py` | Demo execution | Utility |
| `smoke_test.py` | System smoke testing | Testing |
| `tests/test_driveshieldx.py` | Project tests | Testing |
| `models/*.pt` | ML model artifacts | ML |
| `snapshots/` | Image outputs | Output |
| `reports/` | PDF/report outputs | Output |
| `logs/` | Runtime logs | Output |

---

# 52. The One Diagram to Remember

```text
                  ┌───────────────┐
                  │     USER      │
                  └───────┬───────┘
                          │
                          ▼
                ┌──────────────────┐
                │ dashboard/app.py │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ api_server.py    │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │    PIPELINE      │
                │   pipeline.py    │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ YOLO DETECTION   │
                │vehicle_detector  │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ TRACKING          │
                │centroid_tracker  │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ SPEED             │
                │speed_estimator   │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ ZONE              │
                │zone_utils        │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ VIOLATIONS        │
                │rule_violations   │
                └────────┬─────────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
              ▼          ▼          ▼
           ANPR       SNAPSHOT    DATABASE
              │                     │
              ▼                     ▼
         PLATE NUMBER        db_manager.py
                                    │
                                    ▼
                              overspeed.db
                                    │
                      ┌─────────────┴────────────┐
                      ▼                          ▼
                 reporting.py               alerts.py
                      │                          │
                      ▼                          ▼
                   REPORT                     ALERT
                      │
                      └─────────────┬────────────┘
                                    │
                                    ▼
                              DASHBOARD
```

---

# 53. Where Payment Fits

Payment is **after violation/challan generation**, not inside the YOLO pipeline.

```text
YOLO
 ↓
Tracking
 ↓
Speed
 ↓
Violation
 ↓
Challan
 ↓
Database
 ↓
Dashboard
 ↓
PAY NOW
 ↓
api_server.py
 ↓
payments.py
 ↓
Razorpay
 ↓
Payment Verification
 ↓
Database
 ↓
PAID
 ↓
Receipt / Report
```

This separation is important because it keeps:

```text
Computer Vision
```

separate from:

```text
Business / Payment Logic
```

---

# 54. Recommended Next Navigation Step

Start with:

```text
dashboard/app.py
```

Then trace the first function it calls.

After that, move to:

```text
detection/pipeline.py
```

Do not start by modifying code.

First build the call graph:

```text
app.py
  ↓
?
  ↓
?
  ↓
?
```

Once the call graph is understood, modifications become much safer.
