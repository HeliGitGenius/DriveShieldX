import cv2
from ultralytics import YOLO

VIDEO = r"C:\Users\helim\Downloads\code+files\html\images\chai_pe_charcha-1080.mp4"

person_model = YOLO("yolov8n.pt")
seatbelt_model = YOLO("models/seatbelt_yolov8.pt")

cap = cv2.VideoCapture(VIDEO)

# Use frame 100 because it had a strong seatbelt detection
target_frame = 100
frame_no = 0

while True:
    ok, frame = cap.read()

    if not ok:
        break

    frame_no += 1

    if frame_no != target_frame:
        continue

    # Find person
    result = person_model(
        frame,
        conf=0.20,
        imgsz=640,
        verbose=False
    )[0]

    people = []

    for b in result.boxes:
        cls = int(b.cls[0])

        if person_model.names[cls] == "person":
            conf = float(b.conf[0])
            x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
            people.append((conf, x1, y1, x2, y2))

    if not people:
        print("No person found.")
        break

    # Largest person
    _, x1, y1, x2, y2 = max(
        people,
        key=lambda p: (p[3] - p[1]) * (p[4] - p[2])
    )

    crop = frame[y1:y2, x1:x2]

    detections = seatbelt_model(
        crop,
        conf=0.10,
        imgsz=640,
        verbose=False
    )[0]

    # Draw person box
    cv2.rectangle(
        frame,
        (x1, y1),
        (x2, y2),
        (255, 255, 255),
        3
    )

    for b in detections.boxes:
        cls = int(b.cls[0])
        conf = float(b.conf[0])
        name = seatbelt_model.names[cls]

        bx1, by1, bx2, by2 = map(
            int,
            b.xyxy[0].tolist()
        )

        # Convert crop coords -> full frame
        bx1 += x1
        bx2 += x1
        by1 += y1
        by2 += y1

        label = f"{name}: {conf:.2f}"

        cv2.rectangle(
            frame,
            (bx1, by1),
            (bx2, by2),
            (0, 255, 0),
            3
        )

        cv2.putText(
            frame,
            label,
            (bx1, max(30, by1 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 255, 0),
            2
        )

        print(
            name,
            round(conf, 3),
            (bx1, by1, bx2, by2)
        )

    output = "seatbelt_debug_frame.jpg"
    cv2.imwrite(output, frame)

    print()
    print("Saved:", output)
    print("Frame:", target_frame)

    break

cap.release()