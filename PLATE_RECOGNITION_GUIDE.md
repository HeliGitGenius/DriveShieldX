# Number-Plate Recognition — what footage actually works

Your earlier test videos (`traffic_video.mp4`, `demo_video.mp4`) are shot from a **far-away
overhead / bridge camera**. In those clips each car is tiny and the number plate is only about
**5 pixels wide** — there are literally no characters to read. No ANPR system (not EasyOCR, not
even expensive commercial ANPR cameras) can read a plate that small, because the detail was
never captured. Those videos are perfect for **vehicle detection, tracking and speed**, but not
for reading plates.

This is a *camera/footage* limitation, not a bug in DriveShieldX. Here is how to get plates that
actually read.

## ✅ Use footage where the plate is clearly visible

For reliable recognition, the number plate in the frame should be roughly:

- **at least ~90–120 pixels wide** (characters ~20px tall),
- reasonably front/rear facing (not extreme angle),
- not heavily motion-blurred (DriveShieldX deblurs mild blur, but can't invent missing detail).

Practical ways to get this:

1. **Record with your phone at road level / parking exit**, 5–15 metres from vehicles, holding
   the plate in frame. A 1080p phone clip of cars passing a society gate or parking boom works great.
2. **Dashcam / entry-gate camera footage** — these are at plate height and read very well.
3. **Public ANPR sample videos / datasets** (plates ARE readable in these):
   - **Pexels** (pexels.com/videos) — search "parking gate barrier", "toll booth", "car rear close up"
   - **Pixabay** (pixabay.com/videos) — same searches, free download
   - **Kaggle** — "Car License Plate Detection" / "Vehicle Number Plate Detection"
   - **Roboflow Universe** (universe.roboflow.com) — search "license plate", most projects have a sample video
   - **UFPR-ALPR** research dataset — real road clips recorded close to plates
   - **YouTube** — "ANPR test video"; download a short clip with yt-dlp
   Rule of thumb: pause the clip — **if your eyes can read the plate, the OCR can too.**
4. **A single clear photo** of a car's plate — drop it into the Live Monitor → "Number-Plate
   Recognition demo" panel and it will read it immediately.

## ✅ Settings for best reads (Live Monitor)

- Keep **Number Plate Recognition** ON.
- Set **Resize width = 0 (original)** so plate detail isn't downscaled away.
- Keep **Frame skip = 1** so you don't miss the clearest frame of each car.
- Leave **Demo plate fallback** OFF for a genuine read; turn it ON only if you want the full
  challan flow to proceed on footage where plates can't be read (it assigns a consistent demo
  plate per vehicle and marks it confidence = 0 so it's clearly not a real OCR result).

## How to prove it in your viva

1. Open **Live Monitor → Number-Plate Recognition demo**.
2. Click **Sample plate** (clear) → it reads `MH12DE1433`.
3. Click **Blurry sample (test deblur)** → the deblur step sharpens it and it still reads —
   this demonstrates the deblur pipeline.
4. Upload a **clear photo of any real plate** → it reads that plate and (on live runs) stores it.

> First run downloads the EasyOCR models once (needs internet). After that it works offline.
