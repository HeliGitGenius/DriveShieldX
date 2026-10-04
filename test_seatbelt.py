import cv2
from ultralytics import YOLO

from detection.multi_tracker import HybridTracker


VIDEO = r"C:\Users\helim\Downloads\code+files\html\images\chai_pe_charcha-1080.mp4"
VEHICLE_MODEL = "yolov8n.pt"
SEATBELT_MODEL = "models/seatbelt_yolov8.pt"


def main():
    print("=" * 60)
    print("DriveShieldX Seatbelt Diagnostic")
    print("=" * 60)

    # Load models
    print("\n[1] Loading vehicle tracker...")
    tracker = HybridTracker(
        model_path=VEHICLE_MODEL,
        conf=0.15,
        device="cpu",
    )

    print("[2] Loading seatbelt model...")
    seatbelt = YOLO(SEATBELT_MODEL)

    print("Seatbelt classes:", seatbelt.names)

    # Open video
    print("\n[3] Opening video...")
    cap = cv2.VideoCapture(VIDEO)

    if not cap.isOpened():
        print("ERROR: Could not open video:")
        print(VIDEO)
        return

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)

    print("Video opened successfully")
    print("Total frames:", total_frames)
    print("FPS:", fps)

    found_car = False
    frame_number = 0

    # Scan frames until a car is found
    while True:
        ok, frame = cap.read()

        if not ok:
            print("\nEnd of video reached.")
            break

        frame_number += 1

        # Test every 5th frame
        if frame_number % 5 != 0:
            continue

        tracks = tracker.track(frame)

        print(
            f"FRAME {frame_number}: "
            f"tracks={len(tracks)}"
        )

        cars = [
            track
            for track in tracks
            if (track.vehicle_type or "").lower() == "car"
        ]

        if not cars:
            continue

        found_car = True

        print(f"  CARS FOUND: {len(cars)}")

        for track in cars:
            x1, y1, x2, y2 = track.bbox

            # Clamp coordinates to frame
            h, w = frame.shape[:2]

            x1 = max(0, min(x1, w - 1))
            y1 = max(0, min(y1, h - 1))
            x2 = max(0, min(x2, w))
            y2 = max(0, min(y2, h))

            if x2 <= x1 or y2 <= y1:
                print("  Invalid car bbox:", track.bbox)
                continue

            car_crop = frame[y1:y2, x1:x2]

            if car_crop.size == 0:
                print("  Empty car crop")
                continue

            print()
            print("  ----------------------------------------")
            print("  CAR")
            print("  Tracker ID:", track.tracker_id)
            print("  Vehicle type:", track.vehicle_type)
            print("  BBOX:", track.bbox)
            print("  Crop size:", car_crop.shape)

            # Run seatbelt detector
            results = seatbelt(
                car_crop,
                conf=0.10,
                imgsz=384,
                verbose=False,
            )

            detections = []

            for result in results:
                for box in result.boxes:
                    cls_id = int(box.cls[0])
                    confidence = float(box.conf[0])

                    class_name = seatbelt.names.get(
                        cls_id,
                        str(cls_id),
                    )

                    detections.append(
                        (
                            class_name,
                            round(confidence, 3),
                        )
                    )

            print("  SEATBELT DETECTIONS:", detections)

            if detections:
                for name, confidence in detections:
                    if name == "no_seatbelt":
                        print(
                            f"  >>> NO SEATBELT DETECTED "
                            f"(confidence={confidence})"
                        )

                    elif name == "seatbelt":
                        print(
                            f"  >>> SEATBELT DETECTED "
                            f"(confidence={confidence})"
                        )

            else:
                print("  >>> NO SEATBELT DETECTION")

        # Stop after finding cars
        if found_car:
            print("\nCar successfully reached seatbelt detector.")
            break

    cap.release()

    print("\n" + "=" * 60)

    if found_car:
        print("RESULT: Seatbelt model WAS TESTED.")
    else:
        print("RESULT: NO CAR WAS FOUND.")
        print()
        print("This means the problem is BEFORE seatbelt detection:")
        print("YOLOv8 vehicle detection/tracking is not producing cars.")
        print("The seatbelt model itself was not the problem tested.")

    print("=" * 60)


if __name__ == "__main__":
    main()