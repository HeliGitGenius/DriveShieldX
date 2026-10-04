from __future__ import annotations

import os
import sys
from pathlib import Path

import cv2
import numpy as np

# ------------------------------------------------------------
# Make project root importable
# ------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from detection.rule_violations import (
    RuleViolationDetector,
)


# ============================================================
# CONFIG
# ============================================================

VIDEO_PATH = os.getenv(
    "SEATBELT_TEST_VIDEO",
    "test.mp4",
)

SEATBELT_MODEL_PATH = os.getenv(
    "SEATBELT_MODEL_PATH",
    r"C:\Users\helim\Downloads\DriveShieldX\models\seatbelt_yolov8.pt",
)

# Person detector model.
# Change this if your project uses a different model.
PERSON_MODEL_PATH = os.getenv(
    "PERSON_MODEL_PATH",
    "models/yolo11n.pt",
)

# Vehicle bbox used for the test.
#
# IMPORTANT:
# If your existing test already obtains vehicle boxes from
# your detector/tracker, use those instead.
#
# If this is None, the script will use the full frame as the
# vehicle region, which is useful only for testing the
# person/seatbelt pipeline.
VEHICLE_BOX = None

# Process every Nth frame.
FRAME_STEP = int(
    os.getenv("SEATBELT_TEST_FRAME_STEP", "5")
)

# Person confidence.
PERSON_CONF = float(
    os.getenv("PERSON_CONF", "0.35")
)

# Save diagnostic images.
SAVE_DEBUG_IMAGES = os.getenv(
    "SAVE_SEATBELT_DEBUG_IMAGES",
    "1",
).lower() not in {
    "0",
    "false",
    "no",
    "off",
}

DEBUG_DIR = PROJECT_ROOT / "seatbelt_debug"


# ============================================================
# OPTIONAL YOLO IMPORT
# ============================================================

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


# ============================================================
# HELPERS
# ============================================================

def clip_box(
    box,
    width,
    height,
):
    x1, y1, x2, y2 = [
        int(v)
        for v in box[:4]
    ]

    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(0, min(width, x2))
    y2 = max(0, min(height, y2))

    return x1, y1, x2, y2


def draw_box(
    image,
    box,
    label,
    thickness=2,
):
    x1, y1, x2, y2 = [
        int(v)
        for v in box
    ]

    cv2.rectangle(
        image,
        (x1, y1),
        (x2, y2),
        (255, 255, 255),
        thickness,
    )

    cv2.putText(
        image,
        label,
        (x1, max(20, y1 - 5)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )


def save_debug_image(
    frame,
    frame_number,
):
    if not SAVE_DEBUG_IMAGES:
        return

    DEBUG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        DEBUG_DIR
        / f"frame_{frame_number:06d}.jpg"
    )

    cv2.imwrite(
        str(path),
        frame,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("DRIVESHIELDX - REAL SEATBELT RULE PIPELINE TEST")
    print("=" * 70)

    # --------------------------------------------------------
    # Check files
    # --------------------------------------------------------

    print()
    print("Project root:")
    print(PROJECT_ROOT)

    print()
    print("Video:")
    print(VIDEO_PATH)

    print()
    print("Seatbelt model:")
    print(SEATBELT_MODEL_PATH)

    print()
    print("Person model:")
    print(PERSON_MODEL_PATH)

    if not Path(VIDEO_PATH).exists():
        print()
        print("ERROR: Video does not exist:")
        print(VIDEO_PATH)
        print()
        print("Set it with:")
        print(
            '$env:SEATBELT_TEST_VIDEO="path\\to\\video.mp4"'
        )
        return 1

    if not Path(SEATBELT_MODEL_PATH).exists():
        print()
        print("ERROR: Seatbelt model does not exist:")
        print(SEATBELT_MODEL_PATH)
        return 1

    if YOLO is None:
        print()
        print("ERROR: ultralytics is not installed.")
        print()
        print("Install with:")
        print("pip install ultralytics")
        return 1

    # --------------------------------------------------------
    # Load person detector
    # --------------------------------------------------------

    print()
    print("Loading person detector...")

    try:
        person_model = YOLO(
            PERSON_MODEL_PATH
        )
    except Exception as exc:
        print(
            "ERROR loading person model:",
            exc,
        )
        return 1

    print(
        "Person model classes:",
        getattr(
            person_model,
            "names",
            {},
        ),
    )

    # --------------------------------------------------------
    # Load actual RuleViolationDetector
    # --------------------------------------------------------

    print()
    print("Loading RuleViolationDetector...")

    detector = RuleViolationDetector(
        seatbelt_model_path=SEATBELT_MODEL_PATH,
        debug=True,
    )

    print(
        "Seatbelt model classes:",
        getattr(
            detector.seatbelt_model,
            "names",
            {},
        ),
    )

    if detector.seatbelt_model is None:
        print()
        print(
            "ERROR: RuleViolationDetector could not load "
            "seatbelt model."
        )
        return 1

    # --------------------------------------------------------
    # Open video
    # --------------------------------------------------------

    cap = cv2.VideoCapture(
        VIDEO_PATH
    )

    if not cap.isOpened():
        print()
        print("ERROR: Could not open video.")
        return 1

    total_frames = int(
        cap.get(
            cv2.CAP_PROP_FRAME_COUNT
        )
    )

    fps = cap.get(
        cv2.CAP_PROP_FPS
    )

    print()
    print("Video information:")
    print(
        "  frames:",
        total_frames,
    )
    print(
        "  fps:",
        fps,
    )

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    processed = 0
    seatbelt_count = 0
    no_seatbelt_count = 0
    ambiguous_count = 0
    no_detection_count = 0
    other_count = 0

    # --------------------------------------------------------
    # Process video
    # --------------------------------------------------------

    frame_number = 0

    while True:

        ok, frame = cap.read()

        if not ok:
            break

        frame_number += 1

        if (
            frame_number % FRAME_STEP
            != 0
        ):
            continue

        processed += 1

        height, width = frame.shape[:2]

        # ----------------------------------------------------
        # Person detection
        # ----------------------------------------------------

        try:

            person_results = (
                person_model.predict(
                    source=frame,
                    conf=PERSON_CONF,
                    verbose=False,
                )
            )

        except Exception as exc:

            print(
                f"[FRAME {frame_number}] "
                f"Person detection failed: {exc}"
            )

            continue

        person_boxes = []

        for result in person_results:

            boxes = getattr(
                result,
                "boxes",
                None,
            )

            if boxes is None:
                continue

            for box in boxes:

                try:

                    cls_id = int(
                        box.cls[0].item()
                    )

                    conf = float(
                        box.conf[0].item()
                    )

                    coords = (
                        box.xyxy[0]
                        .tolist()
                    )

                except Exception:
                    continue

                # COCO person = class 0.
                if cls_id != 0:
                    continue

                if len(coords) < 4:
                    continue

                pbox = clip_box(
                    coords,
                    width,
                    height,
                )

                person_boxes.append(
                    pbox
                )

        # ----------------------------------------------------
        # No people
        # ----------------------------------------------------

        if not person_boxes:

            print(
                f"\nFRAME {frame_number}: "
                "NO PERSON DETECTED"
            )

            continue

        print()
        print("-" * 70)
        print(
            f"FRAME {frame_number}"
        )
        print(
            f"Persons detected: "
            f"{len(person_boxes)}"
        )

        # ----------------------------------------------------
        # Determine vehicle box.
        #
        # For this diagnostic test, full frame is used if
        # no vehicle bbox is supplied.
        #
        # This means _person_inside_vehicle() will accept
        # the person because the person is inside the frame.
        # ----------------------------------------------------

        if VEHICLE_BOX is None:

            vehicle_box = (
                0,
                0,
                width,
                height,
            )

        else:

            vehicle_box = clip_box(
                VEHICLE_BOX,
                width,
                height,
            )

        # ----------------------------------------------------
        # Draw diagnostic image.
        # ----------------------------------------------------

        debug_frame = frame.copy()

        draw_box(
            debug_frame,
            vehicle_box,
            "TEST VEHICLE",
        )

        # ----------------------------------------------------
        # Run each person through the actual detector.
        #
        # We call the private method directly here because
        # we want diagnostic output for every individual.
        # This is NOT the final application API.
        # ----------------------------------------------------

        for person_index, person_box in enumerate(
            person_boxes
        ):

            print()
            print(
                f"PERSON {person_index}"
            )

            print(
                "  box:",
                person_box,
            )

            # Draw person.
            draw_box(
                debug_frame,
                person_box,
                f"PERSON {person_index}",
            )

            # ------------------------------------------------
            # Get torso ROI
            # ------------------------------------------------

            torso_result = (
                detector._get_torso_roi(
                    person_box,
                    frame.shape,
                )
            )

            if torso_result is None:

                print(
                    "  torso ROI: INVALID"
                )

                continue

            torso_box, full_box = (
                torso_result
            )

            print(
                "  torso ROI:",
                torso_box,
            )

            draw_box(
                debug_frame,
                torso_box,
                f"TORso {person_index}",
            )

            # ------------------------------------------------
            # Extract torso crop
            # ------------------------------------------------

            tx1, ty1, tx2, ty2 = (
                torso_box
            )

            torso_crop = frame[
                ty1:ty2,
                tx1:tx2,
            ]

            if (
                torso_crop is None
                or torso_crop.size == 0
            ):

                print(
                    "  torso crop: EMPTY"
                )

                continue

            print(
                "  torso crop:",
                torso_crop.shape,
            )

            # ------------------------------------------------
            # Run actual seatbelt model through the detector.
            # ------------------------------------------------

            observation = (
                detector._run_seatbelt_model(
                    torso_crop
                )
            )

            print()
            print(
                "  SEATBELT MODEL RESULT"
            )

            print(
                f"    seatbelt_conf    = "
                f"{observation.seatbelt_conf:.3f}"
            )

            print(
                f"    no_seatbelt_conf = "
                f"{observation.no_seatbelt_conf:.3f}"
            )

            print(
                f"    margin           = "
                f"{observation.margin:+.3f}"
            )

            print(
                f"    seatbelt_area    = "
                f"{observation.seatbelt_area:.4f}"
            )

            print(
                f"    no_seatbelt_area = "
                f"{observation.no_seatbelt_area:.4f}"
            )

            print(
                f"    decision         = "
                f"{observation.decision}"
            )

            print(
                f"    violation_candidate = "
                f"{observation.violation_candidate}"
            )

            # ------------------------------------------------
            # Counters
            # ------------------------------------------------

            if observation.decision == "seatbelt":
                seatbelt_count += 1

            elif (
                observation.decision
                == "no_seatbelt"
            ):
                no_seatbelt_count += 1

            elif (
                observation.decision
                == "ambiguous"
            ):
                ambiguous_count += 1

            elif (
                observation.decision
                == "no_detection"
            ):
                no_detection_count += 1

            else:
                other_count += 1

        # ----------------------------------------------------
        # Save diagnostic image.
        # ----------------------------------------------------

        save_debug_image(
            debug_frame,
            frame_number,
        )

        # ----------------------------------------------------
        # Also run the PUBLIC observe() API.
        #
        # This confirms that the rule pipeline itself works.
        # ----------------------------------------------------

        rules = detector.observe(
            tracker_id="TEST_DRIVER",
            vehicle_type="car",
            frame=frame,
            vehicle_bbox=vehicle_box,
            person_boxes=person_boxes,
        )

        print()
        print(
            "PUBLIC observe() RESULT:",
            rules,
        )

    cap.release()

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print("=" * 70)
    print("FINAL DIAGNOSTIC SUMMARY")
    print("=" * 70)

    print(
        "Processed frames:",
        processed,
    )

    print(
        "Seatbelt decisions:",
        seatbelt_count,
    )

    print(
        "No-seatbelt decisions:",
        no_seatbelt_count,
    )

    print(
        "Ambiguous decisions:",
        ambiguous_count,
    )

    print(
        "No detection:",
        no_detection_count,
    )

    print(
        "Other:",
        other_count,
    )

    print()
    print(
        "Debug images:",
        DEBUG_DIR,
    )

    print()
    print("=" * 70)

    if no_seatbelt_count > 0:

        print(
            "GOOD: The pipeline is capable of "
            "producing no_seatbelt decisions."
        )

    elif seatbelt_count > 0:

        print(
            "MODEL RESULT: The seatbelt model is "
            "strongly favoring SEATBELT."
        )

        print(
            "If the driver is actually unbelted, "
            "the model/training data is likely the "
            "next thing to investigate."
        )

    else:

        print(
            "WARNING: The model is not producing "
            "useful seatbelt decisions."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )

