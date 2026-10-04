# DriveShieldX: start here

Everything below runs in **Windows PowerShell** on your own PC. You do not need Claude Code for any of it.

Demo videos (download link and how to use them): docs/VIDEOS.md

You need, once per PC:
- **Python 3.11** (3.10 to 3.12 work). Install from python.org and tick *"Add python.exe to PATH"*.
- **Git**, from git-scm.com (default options are fine).

---

## A. Open the app on your PC (from this zip)

1. Unzip `DriveShieldX_final.zip`, e.g. to `C:\Users\helim\Downloads\DriveShieldX_final`.
2. Open PowerShell in that folder:

```powershell
cd C:\Users\helim\Downloads\DriveShieldX_final
```

3. Copy the files the zip leaves out (models, keys, data) from the folder you have been using so far.
   Change `$old` if your old folder is somewhere else; it is the folder that contains `dashboard`, `models` and `.env`.

```powershell
$old = "C:\Users\helim\Downloads\DriveShieldX\DriveShieldX\DriveShieldX"
robocopy "$old\models" models *.pt
copy "$old\yolov8n.pt" .
copy "$old\.env" .env
copy "$old\database\overspeed.db" database\overspeed.db
robocopy "$old\evaluation\results" evaluation\results /E
robocopy "$old\evaluation\anpr_bench_holdout" evaluation\anpr_bench_holdout /E
dir models
```

`dir models` must list `accident_r3d18.pt`, `helmet_yolov8.pt`, `plate_best_v2.pt`, `seatbelt_yolov8.pt`, `twowheeler_best.pt` and `yolo11n.pt`.

4. Start it with one command:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start-windows.ps1
```

The first run creates `.venv` and installs everything (5 to 15 minutes). The browser then opens **http://localhost:8501**.

- Admin: `admin@speedcam.com` / `admin123`
- Traffic officer: `officer@speedcam.com` / `officer123`
- Vehicle owner: *Sign up as vehicle owner* on the first page

Stop it with `.\stop-windows.ps1`.

**If the browser shows nothing**, run the app in the open window instead, so you can see any error:

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run dashboard\app.py --server.port 8501
```

---

## B. Put the project on GitHub (once, by Heli)

1. On github.com: **New repository**, name `DriveShieldX`, set to **Private**, and tick nothing (no README, no .gitignore, no licence).
2. In the same PowerShell folder:

```powershell
git config --global user.name  "Heli Makwana"
git config --global user.email "you@example.com"     # the email of your GitHub account

git init
git add .
git status
```

3. Read the `git status` list. It must **not** contain `.env`, `database/overspeed.db`, `logs/`, `.venv/` or any `.mp4`. These are excluded by `.gitignore`.
   It **should** contain `models/*.pt`, so your friends get the models too.

4. Check that no file is over GitHub's 100 MB limit:

```powershell
Get-ChildItem -Recurse -File | Where-Object { $_.Length -gt 95MB -and $_.FullName -notmatch '\\.venv\\' } | Select-Object FullName, Length
```

   If this prints nothing, continue. If it lists a file, see *Large files* below.

5. Commit and push:

```powershell
git commit -m "DriveShieldX: traffic violation detection, e-challan and road-safety monitoring"
git branch -M main
git remote add origin https://github.com/HeliGitGenius/DriveShieldX.git
git push -u origin main
```

   A browser window asks you to sign in to GitHub the first time.

6. On GitHub: **Settings > Collaborators > Add people**, then add Nisha and Tanisha.

**Large files.** Only if step 4 listed a file:

```powershell
git lfs install
git lfs track "*.pt"
git rm -r --cached models
git add .gitattributes models
git add .
```

Then continue with step 5.

**Later changes:**

```powershell
git add .
git commit -m "what you changed"
git push
```

---

## C. Friends: get everything with one command

Accept the GitHub invitation first, then in PowerShell:

```powershell
cd $HOME\Downloads
git clone https://github.com/HeliGitGenius/DriveShieldX.git
cd DriveShieldX
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start-windows.ps1
```

That installs everything and opens the app. The models come with the clone.

What does **not** come with the clone (on purpose, it is private):
- **`.env`** (Razorpay and SMS keys). Without it the app still runs: SMS are written to the `outbox` folder instead of being sent. Heli can send her `.env` privately (not in any group chat) and the friend saves it in the `DriveShieldX` folder.
- **The database** with real challans and phone numbers. A fresh, empty database is created automatically with the logins above.
- **Videos.** Share the demo videos separately (e.g. Google Drive).

To get Heli's later changes: `git pull`.

---

## D. Finish the paper and presentation figure (PR curves)

The research paper and the presentation contain exactly the same text, numbers and references.
Figure 4 (precision-recall curves) is produced on your PC from your labelled data:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r evaluation\requirements-eval.txt
python -m evaluation.pr_curves
```

It takes about 30 to 40 minutes, mostly the crash panel. It writes `evaluation\results\pr_curves.pdf` and `.png`.
If a panel's data is missing, that panel says "not run" and the others still work. Paste any error to Claude.

Then copy `pr_curves.pdf` next to the `.tex` files and compile again (Overleaf: upload it into the project):

```powershell
copy evaluation\results\pr_curves.pdf paper\
copy evaluation\results\pr_curves.pdf presentation\
```

The paper stays 10 pages with Figure 4 on page 10.

---

## More

- What is done and what is left: `docs/STATUS.md`
- Every other command (tests, evaluations, Razorpay webhook): `docs/WINDOWS_COMMANDS.md`
- How each part works: `docs/ENFORCEMENT.md`, `docs/ESCALATION.md`, `docs/PAYMENTS_RAZORPAY.md`, `docs/SMS_AND_RC.md`, `docs/ACCIDENT_MODEL.md`
