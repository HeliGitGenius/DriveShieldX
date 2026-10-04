# Demo videos

The videos are **not** in this repository: they are large and `.gitignore` deliberately
blocks `*.mp4`/`*.avi`/`*.mov`/`*.mkv` and the `videos/` folder. Download them separately.

**Download:** `<DRIVE LINK HERE>`

Archive: `DriveShieldX_videos.zip` — 145 MB zipped, 305 MB unzipped, 7 files.

## What is in the archive

| File | Size | Resolution / length | What it shows |
|---|---|---|---|
| `triple_riding_video_1.mp4` | 2.8 MB | 640×480, 25 fps, 32 s | Indian street scene with motorcycles. The main rule-violation demo: a Live Monitor run over it recorded **12 no-helmet** and **7 triple-riding** violations. |
| `seat_belt_video_1.mp4` | 25.7 MB | 1280×720, 59.94 fps, 85 s | Seat-belt footage, the longest of the three. |
| `seat_belt_video_2.mp4` | 4.2 MB | 1280×720, 25 fps, 15 s | Seat-belt footage, short. |
| `seatbelt.mp4` | 2.7 MB | 1280×720, 24 fps, 10 s | Seat-belt footage, shortest — the quickest clip for a live demo. |
| `15397236_1920_1080_60fps.mp4` | 15.5 MB | 1920×1080, 59.94 fps, 19 s | Road traffic. Used as the high-resolution case in the throughput measurements. |
| `traffic_video.mp4` | 34.1 MB | 1920×1080, 25 fps, 66 s | Road traffic, longer run for speed estimation and ANPR. |
| `implementation of project.mp4` | 220 MB | 1916×1194, 30 fps, 189 s | Screen recording of the project being used — a walkthrough for the viva, **not** camera footage. Do not feed this one to the detector. |

The first six are camera footage and are what the detector is meant to run on.

## Where to put them

Unzip into a `videos` folder inside the project folder:

```
DriveShieldX\
  dashboard\
  detection\
  models\
  videos\            <-- unzip here
    triple_riding_video_1.mp4
    seat_belt_video_1.mp4
    ...
```

PowerShell, from the project folder:

```powershell
Expand-Archive -Path "$env:USERPROFILE\Downloads\DriveShieldX_videos.zip" -DestinationPath .\videos
dir .\videos
```

`videos/` is in `.gitignore`, so nothing there can be committed by accident.

## How to load one in the app

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run dashboard\app.py
```

Then, in the dashboard:

1. Sign in through **Enter Authority Portal** (officer account, then the 2FA code).
2. Open the **Live Monitor** page from the top navigation bar.
3. Under **Source type**, leave **Uploaded video** selected.
4. Use **Upload CCTV / UA-DETRAC video** and pick a file from `videos\`.
5. Optional settings on the right: *Tracker*, *Number Plate Recognition*, *Frame skip*,
   *Frames to process this run*, *Resize width*, *Run notes*.
   Frame skip 2–3 and resize width 960 keep it responsive on a CPU-only laptop.
6. Press **▶ Start Live Processing**.

Violations appear live on the frame and are written to the database; afterwards they show up
on **Rule Violations**, **Notice Desk** and **Command Center**.

### Two things worth knowing

- **The 200 MB upload limit.** Streamlit's uploader accepts at most 200 MB per file by
  default, so `implementation of project.mp4` (220 MB) is rejected. That file is a
  walkthrough recording meant to be watched directly, not uploaded. If you ever need to
  upload something larger, start the app with
  `streamlit run dashboard\app.py --server.maxUploadSize 400`.
- **Processing is slow on CPU.** Measured throughput on this laptop is roughly
  0.3–1.7 frames/second depending on the clip and whether number-plate OCR is on, so a
  400-frame run takes several minutes. That is expected, not a hang.
