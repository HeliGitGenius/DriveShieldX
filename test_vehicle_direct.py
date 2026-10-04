import cv2
from ultralytics import YOLO

VIDEO = r"C:\Users\helim\Downloads\code+files\html\images\chai_pe_charcha-1080.mp4"

print("=" * 60)
print("DIRECT VEHICLE DETECTION TEST")
print("=" * 60)

model = YOLO("yolov8n.pt")

print("Model loaded.")
print("Looking specifically for car/motorcycle/bus/truck/person...")

cap = cv2.VideoCapture(VIDEO)

if not cap.isOpened():
    print("ERROR: Could not open video")
    raise SystemExit

frame_no = 0
found = False

while True:
    ok, frame = cap.read()

    if not ok:
        break

    frame_no += 1

    # Test every 5th frame
    if frame_no % 5 != 0:
        continue

    result = model(
        frame,
        conf=0.01,
        imgsz=640,
        verbose=False
    )[0]

    detections = []

    for b in result.boxes:
        cls = int(b.cls[0])
        conf = float(b.conf[0])
        name = model.names[cls]

        if name in {
            "car",
            "motorcycle",
            "bicycle",
            "bus",
            "truck",
            "person",
        }:
            xyxy = tuple(map(int, b.xyxy[0].tolist()))
            detections.append((name, round(conf, 3), xyxy))

    if detections:
        found = True

        print(f"\nFRAME {frame_no}: {len(detections)} detections")

        for d in detections:
            print("   ", d)

cap.release()

print("\n" + "=" * 60)

if found:
    print("VEHICLE/PERSON DETECTION WORKS")
else:
    print("NO VEHICLE/PERSON DETECTIONS FOUND")

print("=" * 60)