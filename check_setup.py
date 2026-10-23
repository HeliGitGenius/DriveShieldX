"""DriveShieldX setup check -- run from the project folder:

    python check_setup.py

Prints NO secrets: for .env it only says which setting NAMES exist.
Checks: which folder/app is running, Razorpay switched on or off and why,
plate reader, models, database, what is listening on port 8501, and how this
folder's code differs from the old project folder.
"""
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path.cwd().resolve()
OLD = Path(os.environ.get("DSX_OLD_FOLDER", r"C:\Users\helim\Downloads\DriveShieldX\DriveShieldX\DriveShieldX"))
sys.path.insert(0, str(ROOT))
ok = lambda b: "OK " if b else "!! "


def head(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70)


head("1. Folder and Python")
print("project folder :", ROOT)
print("python         :", sys.executable)
print(ok(".venv" in sys.executable) + "running inside this folder's .venv" if ".venv" in sys.executable
      else "!! NOT using .venv -- run .\\.venv\\Scripts\\Activate.ps1 first")
print(ok((ROOT / "dashboard" / "app.py").exists()) + "dashboard/app.py present")

head("2. .env (names only, values never printed)")
envf = ROOT / ".env"
names = []
if envf.exists():
    for line in envf.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            names.append((k.strip(), bool(v.strip())))
    print(ok(True) + f".env found ({len(names)} settings)")
    for k, has in names:
        if any(t in k for t in ("RAZORPAY", "PAYMENT", "SMS", "SMSGATE", "PUBLIC_BASE", "OCR", "ALLOW")):
            print(f"    {k:32s} {'set' if has else 'EMPTY'}")
else:
    print("!! no .env in this folder -> Razorpay and SMS are OFF (copy it from the old folder)")

head("3. Razorpay")
try:
    from backend.env_loader import load_env
    load_env()
    from backend import razorpay_gateway as rz
    mode = os.environ.get("DSX_PAYMENT_MODE", "test").strip().lower()
    suffix = "LIVE" if mode == "live" else "TEST"
    kid = os.environ.get(f"RAZORPAY_KEY_ID_{suffix}", "").strip()
    sec = os.environ.get(f"RAZORPAY_KEY_SECRET_{suffix}", "").strip()
    print("payment mode   :", mode)
    print(ok(bool(kid)) + f"RAZORPAY_KEY_ID_{suffix} present" + ("" if kid else "  <- MISSING"))
    print(ok(bool(sec)) + f"RAZORPAY_KEY_SECRET_{suffix} present" + ("" if sec else "  <- MISSING"))
    if kid:
        print(ok(kid.startswith("rzp_test_") if suffix == "TEST" else kid.startswith("rzp_live_")) +
              f"key prefix matches {mode} mode")
    en = rz.enabled()
    print(ok(en) + ("Razorpay is ON (Pay buttons use Razorpay)" if en
                    else "Razorpay is OFF -> the app falls back to the old UPI QR screen"))
    if en:
        try:
            import requests
            cfg = rz.config()
            r = requests.get("https://api.razorpay.com/v1/payment_links?count=1", auth=(cfg.key_id, cfg.key_secret), timeout=15)
            print(ok(r.status_code == 200) + f"Razorpay API login test: HTTP {r.status_code}"
                  + ("" if r.status_code == 200 else "  (401 = wrong key/secret)"))
        except Exception as e:
            print("!! could not reach Razorpay:", e)
except Exception as e:
    print("!! Razorpay check failed:", type(e).__name__, e)

head("4. Plate reader (ANPR)")
try:
    import fast_plate_ocr  # noqa: F401
    print(ok(True) + "fast-plate-ocr installed")
except Exception as e:
    print("!! fast-plate-ocr NOT installed:", e, " -> pip install \"fast-plate-ocr[onnx]\"")
try:
    from detection.npr import NumberPlateRecognizer
    n = NumberPlateRecognizer()
    eng = getattr(n, "engine_name", n.engine)
    print(ok(n.enabled) + "plate reader engine:", eng() if callable(eng) else eng)
except Exception as e:
    print("!! plate reader failed to start:", type(e).__name__, e)

head("5. Model files")
for m in ["helmet_yolov8.pt", "seatbelt_yolov8.pt", "twowheeler_best.pt", "plate_best_v2.pt", "yolo11n.pt", "accident_r3d18.pt"]:
    p = ROOT / "models" / m
    print(ok(p.exists()) + f"models/{m}" + (f"  {p.stat().st_size/1e6:.1f} MB" if p.exists() else "  MISSING"))

head("6. Database")
db = ROOT / "database" / "overspeed.db"
if db.exists():
    c = sqlite3.connect(db)
    for t in ["USER", "VIOLATION_NOTICE", "RULE_VIOLATION", "PAYMENT_LINK", "EMERGENCY_FACILITY", "CAMERA_GEO"]:
        try:
            print(f"    {t:20s} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} rows")
        except Exception:
            print(f"    {t:20s} (table not created yet)")
    try:
        r = c.execute("SELECT COUNT(*), SUM(plate_number IS NULL OR plate_number='' OR plate_number LIKE 'UNREG-%') FROM VIOLATION_NOTICE").fetchone()
        print(f"    challans without a read plate: {r[1]} of {r[0]}")
    except Exception:
        pass
else:
    print("!! no database/overspeed.db")

head("7. What is running on port 8501")
try:
    import psutil
    found = False
    for con in psutil.net_connections(kind="tcp"):
        if con.laddr and con.laddr.port in (8501, 8502, 8503) and con.status == "LISTEN":
            found = True
            try:
                p = psutil.Process(con.pid)
                print(f"    port {con.laddr.port}: PID {con.pid}  cwd={p.cwd()}")
                print(f"        cmd: {' '.join(p.cmdline())[:160]}")
                if Path(p.cwd()).resolve() != ROOT:
                    print("    !! this app is running from a DIFFERENT folder -- that is the old app you see in the browser")
            except Exception as e:
                print(f"    port {con.laddr.port}: PID {con.pid} ({e})")
    if not found:
        print("    nothing is running on 8501-8503")
except Exception as e:
    print("    (could not check ports:", e, ")")

head("8. Code differences: this folder vs old folder")
if OLD.exists():
    skip = {".venv", "venv", "__pycache__", ".git", "database", "logs", "models", "evaluation", "snapshots", "outbox", "reports", "videos", "paper", "presentation"}

    def files(base):
        out = {}
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in (".py", ".ps1", ".sh", ".txt", ".toml", ".cfg") and not (set(p.relative_to(base).parts) & skip):
                out[p.relative_to(base).as_posix()] = hashlib.md5(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        return out
    a, b = files(ROOT), files(OLD)
    only_old = sorted(set(b) - set(a))
    differ = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    print("old folder:", OLD)
    print(f"    {len(differ)} code files differ, {len(only_old)} only in the old folder")
    for k in differ:
        newer = "OLD is newer" if (OLD / k).stat().st_mtime > (ROOT / k).stat().st_mtime else "this folder is newer"
        print(f"    differs: {k}   ({newer})")
    for k in only_old:
        print(f"    only in OLD folder: {k}")
else:
    print("old folder not found:", OLD)
print("\nDone. Copy everything above and send it.")
