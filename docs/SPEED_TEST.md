# Speed field test (about 1 hour, 2 people, daytime)

**Goal:** check the system's speed reading against a real, known speed.

## Safety first

- Use a quiet, straight road or an empty parking lot, with permission.
- The rider wears a helmet. Ride only at speeds that are safe there. 15 / 25 / 35 km/h is fine if 40 is not.
- The person filming stands well off the road.

## You need

- a phone on a stand, or propped firmly (it must not move)
- a measuring tape
- 2 markers (water bottles, bags or chalk lines)
- a scooter
- a second phone with a GPS speedometer app (for example "Speedometer GPS"), or the scooter's own speedometer

## Setup

1. **Mark the distance.** Measure **exactly 10 m** along the road and put marker A and marker B at the edge of the lane.
2. **Place the camera.** Put the filming phone **side-on to the road**, 6–10 m back from the lane. It should point straight across the road (not at an angle), held landscape, recording 1080p at 30 fps.
3. **Frame the shot.** Both markers must be clearly visible, with a few metres of road before and after them.
4. **Don't move the phone** between runs.

## Each run

1. Start recording.
2. The rider starts 20–30 m before marker A, reaches the target speed, and **holds it steady** past both markers.
3. The rider (or a pillion) notes the GPS speed while passing the markers.
4. Stop recording, then **write down the file name and GPS speed** right away.

Do 3–5 runs each at about 20, 30 and 40 km/h (9–15 runs). Use both directions if you can.

## On the laptop

1. Copy all the videos to `C:\data\speed\`.
2. Make `C:\data\speed\runs.csv` in Notepad:
   ```
   video,gps_kmh
   C:\data\speed\run01.mp4,21
   C:\data\speed\run02.mp4,29
   ```
3. Find the markers' pixel positions:
   ```
   python -m evaluation.speed_field_test frame --video C:\data\speed\run01.mp4
   ```
   - Open `run01_frame.png` in **Paint**.
   - Hover over the bottom of marker A and read the x,y at the bottom-left of the Paint window. Do the same for marker B.
4. Run the test:
   ```
   python -m evaluation.speed_field_test run --runs C:\data\speed\runs.csv --mark-a 412,610 --mark-b 1490,605
   ```
   Use your own x,y values for the two markers.

## What you get

For each run, three speeds:
- the **system's** estimate
- the **timing gate** (10 m ÷ time between the markers)
- **GPS**

The summary gives the average error of the system against both references. It also shows how well the two references agree with each other, which tells you how trustworthy the GPS readings were.
