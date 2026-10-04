import cv2
from ultralytics import YOLO

VIDEO = r"C:\Users\helim\Downloads\code+files\html\images\chai_pe_charcha-1080.mp4"

model = YOLO("yolov8n.pt")

print("MODEL CLASSES LOADED")
print(model.names)

cap = cv2.VideoCapture(VIDEO)

if not cap.isOpened():
    print("ERROR: VIDEO COULD NOT OPEN")
    raise SystemExit

frame_no = 0

while True:
    ok, frame = cap.read()

    if not ok:
        break

    frame_no += 1

    if frame_no % 5 != 0:
        continue

    results = model(
        frame,
        conf=0.05,
        imgsz=640,
        verbose=False,
    )

    detections = []

    for result in results:
        for box in result.boxes:
            cls_id = int(box.cls[0])
            confidence = float(box.conf[0])

            name = model.names.get(cls_id, str(cls_id))

            detections.append(
                (
                    name,
                    round(confidence, 3),
                    tuple(map(int, box.xyxy[0].tolist())),
                )
            )

    print(
        f"FRAME {frame_no}: "
        f"{len(detections)} detections"
    )

    for detection in detections:
        print("   ", detection)

cap.release()

print("DONE")