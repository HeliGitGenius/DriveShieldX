import cv2
from ultralytics import YOLO

VIDEO = r"C:\Users\helim\Downloads\code+files\html\images\chai_pe_charcha-1080.mp4"
MODEL = r"models\seatbelt_yolov8.pt"

print("=" * 60)
print("DriveShieldX DIRECT SEATBELT MODEL TEST")
print("=" * 60)

print("\n[1] Loading seatbelt model...")
model = YOLO(MODEL)
print("Classes:", model.names)

print("\n[2] Opening video...")
cap = cv2.VideoCapture(VIDEO)

if not cap.isOpened():
    print("ERROR: Could not open video")
    raise SystemExit(1)

total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
fps = cap.get(cv2.CAP_PROP_FPS)

print("Total frames:", total)
print("FPS:", fps)

best_count = 0
best_frame = None
best_results = []

frame_no = 0

while True:
    ok, frame = cap.read()

    if not ok:
        break

    frame_no += 1

    # Test every 5th frame
    if frame_no % 5 != 0:
        continue

    results = model(
        frame,
        conf=0.10,
        imgsz=640,
        verbose=False
    )

    detections = []

    for result in results:
        for box in result.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])

            x1, y1, x2, y2 = map(
                int,
                box.xyxy[0].tolist()
            )

            label = model.names.get(cls_id, str(cls_id))

            detections.append(
                (
                    label,
                    round(conf, 3),
                    (x1, y1, x2, y2)
                )
            )

    if detections:
        print(
            f"\nFRAME {frame_no}: "
            f"{len(detections)} seatbelt detections"
        )

        for d in detections:
            print("   ", d)

    if len(detections) > best_count:
        best_count = len(detections)
        best_frame = frame.copy()
        best_results = detections

cap.release()

print("\n" + "=" * 60)
print("RESULT")
print("=" * 60)

if best_count == 0:
    print("NO SEATBELT DETECTIONS FOUND.")
    print()
    print("The model loaded, but it did not detect")
    print("either 'seatbelt' or 'no_seatbelt'")
    print("on the full video frames.")
else:
    print("BEST FRAME DETECTIONS:", best_count)

    for d in best_results:
        print("   ", d)

print("=" * 60)