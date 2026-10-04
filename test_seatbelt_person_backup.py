import cv2
from ultralytics import YOLO

VIDEO = r"C:\Users\helim\Downloads\code+files\html\images\chai_pe_charcha-1080.mp4"

print("=" * 60)
print("DRIVERS/PERSON -> SEATBELT TEST")
print("=" * 60)

person_model = YOLO("yolov8n.pt")
seatbelt_model = YOLO("models/seatbelt_yolov8.pt")

print("Person model:", person_model.names)
print("Seatbelt model:", seatbelt_model.names)

cap = cv2.VideoCapture(VIDEO)

if not cap.isOpened():
    print("ERROR: Could not open video")
    raise SystemExit

frame_no = 0
best = []

while True:
    ok, frame = cap.read()

    if not ok:
        break

    frame_no += 1

    if frame_no % 5 != 0:
        continue

    # Detect people
    result = person_model(
        frame,
        conf=0.20,
        imgsz=640,
        verbose=False
    )[0]

    people = []

    for b in result.boxes:
        cls = int(b.cls[0])
        conf = float(b.conf[0])

        if person_model.names[cls] != "person":
            continue

        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())

        people.append((conf, x1, y1, x2, y2))

    if not people:
        continue

    # Pick the largest person
    person = max(
        people,
        key=lambda p: (p[3] - p[1]) * (p[4] - p[2])
    )

    conf, x1, y1, x2, y2 = person

    h, w = frame.shape[:2]

    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(w, x2)
    y2 = min(h, y2)

    crop = frame[y1:y2, x1:x2]

    if crop.size == 0:
        continue

    seat_result = seatbelt_model(
        crop,
        conf=0.10,
        imgsz=640,
        verbose=False
    )[0]

    detections = []

    for b in seat_result.boxes:
        cls = int(b.cls[0])
        score = float(b.conf[0])
        name = seatbelt_model.names[cls]

        bx1, by1, bx2, by2 = map(
            int,
            b.xyxy[0].tolist()
        )

        # Convert crop coordinates back to original frame
        detections.append(
            (
                name,
                round(score, 3),
                (
                    bx1 + x1,
                    by1 + y1,
                    bx2 + x1,
                    by2 + y1,
                )
            )
        )

    if detections:
        print()
        print(f"FRAME {frame_no}")
        print(
            f"PERSON conf={conf:.3f} "
            f"box=({x1},{y1},{x2},{y2})"
        )

        for d in detections:
            print("   ", d)

            best.append((frame_no, d))

cap.release()

print()
print("=" * 60)
print("RESULT")
print("=" * 60)

if best:
    print("SEATBELT MODEL DETECTED SOMETHING ON PERSON CROPS.")

    print()
    print("Highest confidence detections:")

    for item in sorted(
        best,
        key=lambda x: x[1][1],
        reverse=True
    )[:10]:
        print("   ", item)
else:
    print("NO SEATBELT DETECTIONS ON PERSON CROPS.")

print("=" * 60)