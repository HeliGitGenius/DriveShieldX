# Training the crash-detection model (Kaggle, free GPU)

**Why:** the rule-based accident monitor follows tracked vehicles. On low-resolution CCTV it found 1/23 test crashes. This model looks at the whole picture instead: R3D-18, a video network pretrained on Kinetics-400, fine-tuned to say "crash / no crash" for every 2-second window.

**Fair test:** the 23 test crashes and 23 test normal clips (`C:\data\ucf\accident_labels.csv`) are never used for labelling, training or choosing the threshold. Every script refuses or hides them.

## Step 1: Mark crash times on training videos (about 1 hour, on your laptop)

```powershell
cd <project folder>
.\.venv\Scripts\Activate.ps1
streamlit run training\accident_label_tool.py --server.port 8503 -- --videos C:\data\ucf\videos --exclude C:\data\ucf\accident_labels.csv --out C:\data\ucf\accident_train_labels.csv
```

For each video:
1. Drag the slider to the first contact and press **Crash starts here**.
2. Drag to when the vehicles have stopped and press **Crash ends here**.
3. Press **Save**.

If you can't see a crash, press **No clear crash**. Aim for **60 or more** crashes; all ~128 is best. Answers save after every video, so you can stop any time.

## Step 2: Make the upload (about 10–20 min)

```powershell
python -m training.accident_prepare --train-labels C:\data\ucf\accident_train_labels.csv --videos C:\data\ucf\videos --exclude C:\data\ucf\accident_labels.csv --out C:\data\ucf\kaggle_upload
```

This creates `C:\data\ucf\kaggle_upload.zip` (a few hundred MB). It contains:
- the clips (8 fps, small)
- a manifest
- the training code

## Step 3: Train on Kaggle (about 30–60 min)

1. Go to kaggle.com, sign in, and **verify your phone number**. GPU and internet access need it.
2. Go to **Datasets → New Dataset** and upload `kaggle_upload.zip`. Name it `dsx-crash`. Kaggle unzips it.
3. Go to **Code → New Notebook**. In the right panel:
   - **Accelerator:** GPU T4 x1 (or P100)
   - **Internet:** On
   - **Add Input:** your `dsx-crash` dataset
4. In one cell, run:
   ```
   !ls /kaggle/input/dsx-crash
   !python /kaggle/input/dsx-crash/train_accident_model.py --data /kaggle/input/dsx-crash --out /kaggle/working --epochs 10
   ```
   If `ls` shows one more folder level (for example `kaggle_upload`), add that folder to both paths.
5. Each epoch prints one line, including `val_crashes_found` and `val_false_alarms_per_hour`.
6. When it ends, open the **Output** panel and download:
   - `accident_r3d18.pt` (about 66 MB)
   - `accident_val_report.json`

## Step 4: Test once on the 23 + 23 test clips (laptop, about 20–40 min)

1. Put `accident_r3d18.pt` in the project's `models\` folder.
2. Run:
   ```powershell
   python -m evaluation.accident_eval --labels C:\data\ucf\accident_labels.csv --model models\accident_r3d18.pt --tolerance 10 --out evaluation\results\accident_test_model.json
   ```

Report the result exactly as printed, next to the rule-based results:
- deployed rules: 1/23 crashes, 0 false alarms
- dev-tuned rules: 3/23 crashes, 11.8 false alarms per hour

Don't change the threshold after seeing the test result.

## Notes

- The threshold and the best epoch come from the validation videos (20% of training videos, split by video). Rule: most crashes found with ≤ 2 false alarms per hour (`--max-fa`).
- The checkpoint is half precision to stay under GitHub's 100 MB file limit.
- **Live use:** `detection/accident_classifier.py` → `AccidentVideoClassifier.update(frame, t)` returns `True` when an alert should be raised. It's wired into the live pipeline only after the test result is known.
