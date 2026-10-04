# DriveShieldX: Windows commands

Run everything in **PowerShell** from the project folder, e.g.
`cd C:\Users\helim\Downloads\DriveShieldX_final`

## 1. First-time setup

```powershell
python --version                      # needs 3.10 - 3.12 (3.11 used in testing)
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r evaluation\requirements-eval.txt   # only for the evaluation scripts
copy .env.example .env                # then open .env in Notepad and fill your own keys
notepad .env
```

**Never share `.env` and never commit it.** It holds the Razorpay and SMS keys.

## 2. Bring over your data from the old folder (one time)

The zip does not contain the model files, personal data or secrets. Copy these from the old project folder:

```powershell
$old = "C:\Users\helim\Downloads\DriveShieldX\DriveShieldX\DriveShieldX"
robocopy "$old\models" models *.pt          # model weights (not in the zip: too large to send)
copy "$old\yolov8n.pt" .
copy "$old\.env" .env
copy "$old\database\overspeed.db" database\overspeed.db
robocopy "$old\evaluation\results" evaluation\results /E
robocopy "$old\evaluation\anpr_bench_holdout" evaluation\anpr_bench_holdout /E
copy "$old\RESULTS.md" . -ErrorAction SilentlyContinue
```

## 3. Run the app

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run dashboard\app.py --server.port 8501
```

Then open http://localhost:8501.

Optional API and webhook server, in a second window:

```powershell
uvicorn backend.api_server:app --host 0.0.0.0 --port 8000
```

## 4. Tests

```powershell
python -m pytest -q tests
```
The tests work on a **copy** of `database\overspeed.db` and need its records (challans, vehicles). Copy the database over first (section 2). On a fresh clone without it, about 30 tests fail only because the sample data is missing.

## 5. Evaluation (results go to RESULTS.md)

**Gate**
```powershell
python -m evaluation.gate_eval score --sheet evaluation\results\gate\review_sheet.csv
```

**Plate reading on the public dataset**

Download https://zenodo.org/records/13954136/files/number_plate.zip and extract it to `C:\data\number_plate`.

```powershell
python -m evaluation.anpr_import_dataset --images C:\data\number_plate --sheet C:\data\number_plate\number_plate.xlsx
python -m evaluation.anpr_import_dataset --images C:\data\number_plate --sheet C:\data\number_plate\number_plate.xlsx --max-rows 0 --exclude-bench evaluation\anpr_bench_dataset --out evaluation\anpr_bench_holdout
python -m evaluation.anpr_benchmark score --bench evaluation\anpr_bench_holdout --engine fastplate
python -m evaluation.anpr_benchmark score --bench evaluation\anpr_bench_holdout --engine easyocr
python -m evaluation.anpr_benchmark score --bench evaluation\anpr_bench_holdout --engine auto
```

**Labelling tool** (gate and plate sheets)
```powershell
streamlit run evaluation\label_tool.py --server.port 8502
```

**Final report**
```powershell
python -m evaluation.make_report
```

**Useful checks**
```powershell
python -m backend.registry MH02AB1234             # RC lookup (your own vehicles only)
python -c "from backend import payment_worker; print(payment_worker.run_once())"   # sync Razorpay payments now
python -c "from backend import escalation; print(escalation.run_once())"          # run unpaid-challan escalation now
```

## 6. New GitHub repository, and friends cloning it

See `START_HERE.md`, parts B (create the private repo and push, once) and C (friends: `git clone` + `.\start-windows.ps1`).

## 6b. Precision-recall curves (paper and presentation Figure 4)

```powershell
python -m evaluation.pr_curves
copy evaluation\results\pr_curves.pdf paper\
copy evaluation\results\pr_curves.pdf presentation\
```

## 7. Stop the app

Press **Ctrl + C** in the PowerShell window running Streamlit, or run `.\stop-windows.ps1`.
