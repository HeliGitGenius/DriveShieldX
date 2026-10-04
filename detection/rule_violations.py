"""
DriveShieldX — Layer-2 rule-violation detectors.

Detects:
    - No helmet
    - Triple riding / three-seater
    - No seatbelt
    - License plate localisation

Important:
    twowheeler_best.pt is NOT assumed to detect triple_riding.
    Triple riding is determined by counting COCO 'person' detections.

Seatbelt detection:
    The seatbelt model is applied to a PERSON crop associated with
    the detected car, rather than directly to the entire vehicle bbox.

Temporal confirmation prevents the same tracker from generating
the same violation repeatedly.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2


# ---------------------------------------------------------------------------
# MODEL DIRECTORY
# ---------------------------------------------------------------------------

MODEL_DIR = (
    Path(__file__).resolve().parents[1]
    / "models"
)


# ---------------------------------------------------------------------------
# FINES
# ---------------------------------------------------------------------------

DEFAULT_FINES = {
    "no_helmet": 500,
    "three_seater": 1000,
    "no_seatbelt": 1000,
}


# ---------------------------------------------------------------------------
# MODEL CLASS → RULE MAPPING
# ---------------------------------------------------------------------------

_POSITIVE = {
    "no_helmet": {
        "no_helmet",
        "no helmet",
        "no-helmet",
        "without_helmet",
        "without helmet",
        "unhelmeted",
    },

    "no_seatbelt": {
        "no_seatbelt",
        "no seatbelt",
        "no-seatbelt",
        "without_seatbelt",
        "without seatbelt",
        "unbelted",
    },

    "three_seater": {
        "triple_riding",
        "triple riding",
        "three_seater",
        "three seater",
    },
}


# ---------------------------------------------------------------------------
# YOLO LOADER
# ---------------------------------------------------------------------------

def _load(name: str):
    """Load a YOLO model safely."""

    path = MODEL_DIR / name

    if not path.exists():
        # The COCO backbone ships at the repo root / backend/models in this
        # project; look there before giving up (keeps models/ the first choice).
        for alt in (MODEL_DIR.parent / "backend" / "models" / name, MODEL_DIR.parent / name):
            if alt.exists():
                path = alt
                break

    if not path.exists():
        print(
            f"[RuleDetector] Model not found: {path}"
        )
        return None

    try:
        from ultralytics import YOLO

        model = YOLO(str(path))

        print(
            f"[RuleDetector] Loaded: {name}"
        )

        return model

    except Exception as exc:
        print(
            f"[RuleDetector] Failed loading "
            f"{name}: {exc}"
        )
        return None


def _largest_overlapping_group(boxes):
    n = len(boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(boxes[i])
    return max(groups.values(), key=len)


def _clahe_if_needed(crop):
    """CLAHE on the L channel, only for dark or glare-saturated cabin crops
    (paper Sec. 4.4: 'CLAHE applied beforehand to recover contrast under low
    light and glare'). Normal crops are passed through unchanged."""
    try:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        dark = gray.mean() < 90
        glare = (gray > 245).mean() > 0.20
        if not (dark or glare):
            return crop
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
        return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    except Exception:
        return crop


# ---------------------------------------------------------------------------
# RULE DETECTOR
# ---------------------------------------------------------------------------

class RuleViolationDetector:
    """
    Helmet / triple-riding / seatbelt / plate detector.

    Triple riding:
        YOLOv8n person detection is used to count people
        associated with a motorcycle.

    Seatbelt:
        The car is first associated with a person.
        The seatbelt model is then run on that PERSON crop.

    Temporal confirmation:
        A rule must be detected in consecutive frames before
        it becomes a confirmed violation.
    """

    # Number of consecutive frames required.
    CONFIRM_FRAMES = 2

    # Person confidence for triple-riding/person association.
    PERSON_CONF = 0.30

    # Three or more people associated with a motorcycle.
    TRIPLE_RIDING_COUNT = 3

    # Minimum no-seatbelt confidence.
    NO_SEATBELT_CONF = 0.50

    # Seatbelt model input size.
    SEATBELT_IMGSZ = 640

    # -------------------------------------------------------------------
    # DISAGREEMENT GATE (paper Sec. 4.4)
    #
    # The standalone helmet model (p_s) and the combined two-wheeler model
    # (p_c) are run on the same crop. With theta the issuance threshold:
    #   both > theta  -> agree positive -> violation may be issued
    #   both < theta  -> agree negative -> nothing
    #   otherwise     -> disagreement   -> officer review queue, NO challan
    # theta = 0.15 equals the detection threshold the code already used, so
    # each model's own sensitivity is unchanged; only the combination rule
    # changes (previously OR, which issued on disagreement).
    # Set env DRIVESHIELDX_GATE=0 to restore the old OR rule (ablation only).
    # -------------------------------------------------------------------
    GATE_THETA = float(os.environ.get("DRIVESHIELDX_GATE_THETA", "0.15"))

    # Seat-belt confirmation (paper Table 2 pipeline settings): 3 consecutive
    # observations, and no_seatbelt must beat seatbelt by a 0.12 margin.
    SEATBELT_CONFIRM_FRAMES = 3
    SEATBELT_MARGIN = 0.12

    # Rider association (scale-aware, one person -> one two-wheeler).
    RIDER_MIN_X_OVERLAP = 0.5
    GATE_ENABLED = os.environ.get("DRIVESHIELDX_GATE", "1") != "0"

    def __init__(self) -> None:

        # ---------------------------------------------------------------
        # SPECIALIST MODELS
        # ---------------------------------------------------------------

        self.helmet_model = _load(
            "helmet_yolov8.pt"
        )

        self.seatbelt_model = _load(
            "seatbelt_yolov8.pt"
        )

        self.twowheeler_model = _load(
            "twowheeler_best.pt"
        )

        self.plate_model = _load(
            "plate_best_v2.pt"
        )

        # ---------------------------------------------------------------
        # GENERAL COCO MODEL
        #
        # Class 0 = person
        # ---------------------------------------------------------------

        self.person_model = _load(
            "yolov8n.pt"
        )

        # ---------------------------------------------------------------
        # TEMPORAL STATE
        # ---------------------------------------------------------------

        self._streak: Dict[
            str,
            Dict[str, int]
        ] = defaultdict(
            lambda: defaultdict(int)
        )

        self._confirmed: Dict[
            str,
            set
        ] = defaultdict(set)

        # Per-frame rider assignment (set_frame_context)
        self._riders: Dict[str, List[Tuple[int, int, int, int]]] = {}

        # Disagreement-gate state
        self._review_streak: Dict[str, int] = defaultdict(int)
        self._review_queued: set = set()
        self._pending_reviews: List[dict] = []
        self.gate_stats: Counter = Counter()

    # -------------------------------------------------------------------
    # STATUS
    # -------------------------------------------------------------------

    def loaded(self) -> Dict[str, bool]:
        """Return model-loading status."""

        return {
            "helmet": (
                self.helmet_model
                is not None
            ),
            "seatbelt": (
                self.seatbelt_model
                is not None
            ),
            "twowheeler": (
                self.twowheeler_model
                is not None
            ),
            "plate": (
                self.plate_model
                is not None
            ),
            "person": (
                self.person_model
                is not None
            ),
        }

    # -------------------------------------------------------------------
    # GENERIC SPECIALIST MODEL CHECK
    # -------------------------------------------------------------------

    def _has(
        self,
        model,
        frame,
        box,
        rule: str,
    ) -> bool:
        """
        Check whether a specialist model detects a violation class.
        """

        return self._max_conf(model, frame, box, rule) >= 0.15

    def _max_conf(
        self,
        model,
        frame,
        box,
        rule: str,
    ) -> float:
        """
        Highest confidence of the rule's positive class (0.0 if none),
        i.e. p_s / p_c in the paper's gate definition.
        """

        if model is None:
            return 0.0

        try:

            x1, y1, x2, y2 = map(
                int,
                box
            )

            h, w = frame.shape[:2]

            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)

            if x2 <= x1 or y2 <= y1:
                return 0.0

            crop = frame[
                y1:y2,
                x1:x2
            ]

            if crop.size == 0:
                return 0.0

            results = model(
                crop,
                verbose=False,
                conf=0.15,
                imgsz=384,
            )

            if not results:
                return 0.0

            result = results[0]

            names = (
                getattr(
                    model,
                    "names",
                    {}
                )
                or {}
            )

            targets = _POSITIVE.get(
                rule,
                set()
            )

            best = 0.0

            for detection in result.boxes:

                cls_id = int(
                    detection.cls[0]
                )

                confidence = float(
                    detection.conf[0]
                )

                label = str(
                    names.get(
                        cls_id,
                        ""
                    )
                ).strip().lower()

                if (
                    label in targets
                    and confidence > best
                ):
                    best = confidence

            return best

        except Exception as exc:

            print(
                f"[RuleDetector] "
                f"_max_conf({rule}) failed: {exc}"
            )

            return 0.0

    # -------------------------------------------------------------------
    # RIDER ASSOCIATION (frame level, scale-aware, unique assignment)
    # -------------------------------------------------------------------

    def set_frame_context(self, vehicles, person_boxes) -> None:
        """Assign every detected person to at most ONE two-wheeler for this frame.

        vehicles: iterable of (tracker_id, vehicle_type, (x1,y1,x2,y2)).
        A person is a rider candidate for a bike when
          (a) their horizontal extent overlaps the bike by >= RIDER_MIN_X_OVERLAP
              of the person's width and their centre lies within the bike's
              x-range (+10 % of its width),
          (b) their feet (box bottom) fall between the bike's top - 0.6*h and
              the bike's bottom + 0.15*h, and
          (c) their height is 0.6-2.5x the bike's height (same depth; a person
              far behind or in front is not on this bike).
        All thresholds scale with the bike, unlike the old fixed 40/120-pixel
        padding. Each person goes to the bike with the largest overlap, so in
        dense traffic a neighbour's pillion is not double-counted -- the main
        source of false triple-riding flags.
        """
        bikes = [(str(t), tuple(map(float, b))) for t, vt, b in vehicles
                 if str(vt).strip().lower() in {"bike", "motorcycle", "motorbike", "two-wheeler", "two_wheeler"}]
        self._riders = {t: [] for t, _ in bikes}
        for pb in person_boxes or []:
            if len(pb) < 4:
                continue
            px1, py1, px2, py2 = map(float, pb[:4])
            pw = max(1.0, px2 - px1)
            best, best_ov = None, 0.0
            for t, (bx1, by1, bx2, by2) in bikes:
                bh = max(1.0, by2 - by1)
                bw = max(1.0, bx2 - bx1)
                ov = max(0.0, min(px2, bx2) - max(px1, bx1)) / pw
                if ov < self.RIDER_MIN_X_OVERLAP:
                    continue
                pcx = (px1 + px2) / 2
                if not (bx1 - 0.1 * bw <= pcx <= bx2 + 0.1 * bw):
                    continue
                if not (0.6 * bh <= (py2 - py1) <= 2.5 * bh):
                    continue
                if not (by1 - 0.6 * bh <= py2 <= by2 + 0.15 * bh):
                    continue
                if ov > best_ov:
                    best, best_ov = t, ov
            if best is not None:
                self._riders[best].append((int(px1), int(py1), int(px2), int(py2)))

        # People on ONE two-wheeler sit in tandem, so their boxes overlap each
        # other. Keep only the largest group of mutually-overlapping riders; an
        # isolated person inside the box belongs to a neighbouring vehicle that
        # the detector merged into this box.
        for t, rs in self._riders.items():
            if len(rs) > 1:
                self._riders[t] = _largest_overlapping_group(rs)

    def riders_for(self, tracker_id: str) -> Optional[List[Tuple[int, int, int, int]]]:
        """Assigned riders for a bike on the current frame, or None if no
        frame context was supplied (old call path)."""
        return self._riders.get(str(tracker_id)) if self._riders else None

    def _rider_crop_box(self, frame, bike_box, riders) -> Tuple[int, int, int, int]:
        """Bike box grown to include its riders (COCO motorcycle boxes usually
        stop at the riders' waist, cutting off the heads the helmet models need)."""
        x1, y1, x2, y2 = map(int, bike_box)
        for r in riders or []:
            x1, y1, x2, y2 = min(x1, r[0]), min(y1, r[1]), max(x2, r[2]), max(y2, r[3])
        if not riders:  # no person boxes: extend upwards by one bike height
            y1 = y1 - (y2 - y1)
        pad = int(0.08 * max(x2 - x1, y2 - y1))
        h, w = frame.shape[:2]
        return max(0, x1 - pad), max(0, y1 - pad), min(w, x2 + pad), min(h, y2 + pad)

    def _head_conf(self, model, frame, crop_box, rule: str, riders) -> float:
        """Max positive-class confidence whose box centre lies in a rider's head
        region (top 40% of the rider box). Without riders, any detection counts."""
        if model is None:
            return 0.0
        x1, y1, x2, y2 = crop_box
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return 0.0
        try:
            res = model(crop, verbose=False, conf=0.15, imgsz=384)
        except Exception as exc:
            print(f"[RuleDetector] _head_conf({rule}) failed: {exc}")
            return 0.0
        if not res:
            return 0.0
        names = getattr(model, "names", {}) or {}
        targets = _POSITIVE.get(rule, set())
        best = 0.0
        for d in res[0].boxes:
            label = str(names.get(int(d.cls[0]), "")).strip().lower()
            if label not in targets:
                continue
            conf = float(d.conf[0])
            dx1, dy1, dx2, dy2 = d.xyxy[0].tolist()
            cx, cy = x1 + (dx1 + dx2) / 2, y1 + (dy1 + dy2) / 2
            if riders:
                in_head = any(r[0] <= cx <= r[2] and r[1] - 0.1 * (r[3] - r[1]) <= cy <= r[1] + 0.4 * (r[3] - r[1])
                              for r in riders)
                if not in_head:
                    continue
            best = max(best, conf)
        return best

    # -------------------------------------------------------------------
    # DISAGREEMENT GATE
    # -------------------------------------------------------------------

    def gate_verdict(self, p_s: Optional[float], p_c: Optional[float]) -> str:
        """Return 'issue', 'clear' or 'review' (paper Sec. 4.4).

        p_s / p_c may be None when that model is not loaded; the gate then
        cannot compare and falls back to the available model alone.
        """

        theta = self.GATE_THETA

        if p_s is None and p_c is None:
            return "clear"

        if p_s is None or p_c is None:
            self.gate_stats["single_model"] += 1
            p = p_s if p_s is not None else p_c
            return "issue" if p > theta else "clear"

        if not self.GATE_ENABLED:
            # Legacy OR rule, kept only for the ablation study.
            return "issue" if (p_s > theta or p_c > theta) else "clear"

        if p_s > theta and p_c > theta:
            self.gate_stats["agree_positive"] += 1
            return "issue"

        if p_s <= theta and p_c <= theta:
            self.gate_stats["agree_negative"] += 1
            return "clear"

        self.gate_stats["disagree"] += 1
        return "review"

    def pop_reviews(self) -> List[dict]:
        """Hand newly queued officer-review events to the caller (and clear)."""
        out, self._pending_reviews = self._pending_reviews, []
        return out

    # -------------------------------------------------------------------
    # PERSON COUNTING
    # -------------------------------------------------------------------

    def _count_people_inside(
        self,
        frame,
        vehicle_box,
    ) -> int:
        """
        Count people associated with a motorcycle.

        The motorcycle ROI is expanded because the rider's body/head
        can extend considerably above the motorcycle bbox.
        """

        if self.person_model is None:
            return 0

        try:

            x1, y1, x2, y2 = map(
                int,
                vehicle_box
            )

            h, w = frame.shape[:2]

            pad_x = 40
            pad_top = 120
            pad_bottom = 40

            rx1 = max(
                0,
                x1 - pad_x
            )

            ry1 = max(
                0,
                y1 - pad_top
            )

            rx2 = min(
                w,
                x2 + pad_x
            )

            ry2 = min(
                h,
                y2 + pad_bottom
            )

            if rx2 <= rx1 or ry2 <= ry1:
                return 0

            crop = frame[
                ry1:ry2,
                rx1:rx2
            ]

            if crop.size == 0:
                return 0

            results = self.person_model(
                crop,
                verbose=False,
                conf=self.PERSON_CONF,
                imgsz=640,
            )

            if not results:
                return 0

            result = results[0]

            count = 0

            for detection in result.boxes:

                cls_id = int(
                    detection.cls[0]
                )

                if cls_id != 0:
                    continue

                confidence = float(
                    detection.conf[0]
                )

                if confidence < self.PERSON_CONF:
                    continue

                px1, py1, px2, py2 = map(
                    int,
                    detection.xyxy[0].tolist()
                )

                px1 += rx1
                py1 += ry1
                px2 += rx1
                py2 += ry1

                center_x = (
                    px1 + px2
                ) / 2.0

                bottom_y = float(py2)

                if (
                    rx1 <= center_x <= rx2
                    and ry1 <= bottom_y <= ry2
                ):
                    count += 1

            return count

        except Exception as exc:

            print(
                "[RuleDetector] "
                f"Person counting failed: {exc}"
            )

            return 0

    # -------------------------------------------------------------------
    # TRIPLE RIDING
    # -------------------------------------------------------------------

    def _is_triple_riding(
        self,
        frame,
        vehicle_box,
        person_boxes=None,
    ) -> bool:
        """
        Determine whether a motorcycle has >= 3 riders.
        """

        # ---------------------------------------------------------------
        # METHOD 1 — COCO PERSON DETECTOR
        # ---------------------------------------------------------------

        person_count = (
            self._count_people_inside(
                frame,
                vehicle_box,
            )
        )

        if (
            person_count
            >= self.TRIPLE_RIDING_COUNT
        ):
            return True

        # ---------------------------------------------------------------
        # METHOD 2 — PERSON BOXES FROM PIPELINE
        # ---------------------------------------------------------------

        if person_boxes:

            try:

                count = self._riders_inside(
                    vehicle_box,
                    person_boxes,
                )

                if (
                    count
                    >= self.TRIPLE_RIDING_COUNT
                ):
                    return True

            except Exception:
                pass

        # ---------------------------------------------------------------
        # METHOD 3 — SPECIALIST MODEL CLASS
        # ---------------------------------------------------------------

        if (
            self.twowheeler_model
            is not None
        ):

            try:

                names = (
                    getattr(
                        self.twowheeler_model,
                        "names",
                        {}
                    )
                    or {}
                )

                has_triple_class = any(
                    str(name)
                    .strip()
                    .lower()
                    in _POSITIVE[
                        "three_seater"
                    ]
                    for name in names.values()
                )

                if has_triple_class:

                    if self._has(
                        self.twowheeler_model,
                        frame,
                        vehicle_box,
                        "three_seater",
                    ):
                        return True

            except Exception:
                pass

        return False

    # -------------------------------------------------------------------
    # COMPATIBILITY PERSON-BOX COUNTER
    # -------------------------------------------------------------------

    def _riders_inside(
        self,
        box,
        person_boxes,
    ) -> int:
        """Count person boxes inside an expanded bike bbox."""

        if not person_boxes:
            return 0

        try:

            x1, y1, x2, y2 = map(
                int,
                box
            )

            exp_x1 = x1 - 40
            exp_y1 = y1 - 120
            exp_x2 = x2 + 40
            exp_y2 = y2 + 40

            count = 0

            for person in person_boxes:

                if len(person) < 4:
                    continue

                px1, py1, px2, py2 = map(
                    float,
                    person[:4]
                )

                center_x = (
                    px1 + px2
                ) / 2.0

                bottom_y = py2

                if (
                    exp_x1 <= center_x <= exp_x2
                    and exp_y1 <= bottom_y <= exp_y2
                ):
                    count += 1

            return count

        except Exception:
            return 0

    # -------------------------------------------------------------------
    # CAR PERSON ASSOCIATION
    # -------------------------------------------------------------------

    def _find_person_for_car(
        self,
        frame,
        vehicle_box,
        person_boxes=None,
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Find the most likely driver/person associated with a car.

        Priority:
            1. Person boxes already supplied by the pipeline.
            2. Fresh YOLOv8n person detection inside the car.

        Returns:
            (x1, y1, x2, y2)
            or None.
        """

        try:

            h, w = frame.shape[:2]

            vx1, vy1, vx2, vy2 = map(
                int,
                vehicle_box
            )

            vx1 = max(
                0,
                vx1
            )

            vy1 = max(
                0,
                vy1
            )

            vx2 = min(
                w,
                vx2
            )

            vy2 = min(
                h,
                vy2
            )

            if (
                vx2 <= vx1
                or vy2 <= vy1
            ):
                return None

            # -----------------------------------------------------------
            # METHOD 1 — USE PERSON BOXES FROM PIPELINE
            # -----------------------------------------------------------

            best_person = None
            best_score = -1.0

            if person_boxes:

                for person in person_boxes:

                    if len(person) < 4:
                        continue

                    px1, py1, px2, py2 = map(
                        int,
                        person[:4]
                    )

                    px1 = max(
                        0,
                        px1
                    )

                    py1 = max(
                        0,
                        py1
                    )

                    px2 = min(
                        w,
                        px2
                    )

                    py2 = min(
                        h,
                        py2
                    )

                    if (
                        px2 <= px1
                        or py2 <= py1
                    ):
                        continue

                    center_x = (
                        px1 + px2
                    ) / 2.0

                    center_y = (
                        py1 + py2
                    ) / 2.0

                    # ---------------------------------------------------
                    # Person center should lie inside vehicle.
                    # ---------------------------------------------------

                    if not (
                        vx1 <= center_x <= vx2
                        and vy1 <= center_y <= vy2
                    ):
                        continue

                    area = (
                        px2 - px1
                    ) * (
                        py2 - py1
                    )

                    # Prefer larger associated person.
                    if area > best_score:
                        best_score = area
                        best_person = (
                            px1,
                            py1,
                            px2,
                            py2,
                        )

            if best_person is not None:
                return best_person

            # -----------------------------------------------------------
            # METHOD 2 — DETECT PERSON DIRECTLY
            # -----------------------------------------------------------

            if self.person_model is None:
                return None

            crop = frame[
                vy1:vy2,
                vx1:vx2
            ]

            if crop.size == 0:
                return None

            results = self.person_model(
                crop,
                verbose=False,
                conf=0.25,
                imgsz=640,
            )

            if not results:
                return None

            result = results[0]

            best_person = None
            best_area = 0

            for detection in result.boxes:

                cls_id = int(
                    detection.cls[0]
                )

                if cls_id != 0:
                    continue

                confidence = float(
                    detection.conf[0]
                )

                if confidence < 0.25:
                    continue

                px1, py1, px2, py2 = map(
                    int,
                    detection.xyxy[0].tolist()
                )

                px1 += vx1
                py1 += vy1
                px2 += vx1
                py2 += vy1

                px1 = max(
                    0,
                    px1
                )

                py1 = max(
                    0,
                    py1
                )

                px2 = min(
                    w,
                    px2
                )

                py2 = min(
                    h,
                    py2
                )

                area = (
                    max(
                        0,
                        px2 - px1
                    )
                    *
                    max(
                        0,
                        py2 - py1
                    )
                )

                if area > best_area:

                    best_area = area

                    best_person = (
                        px1,
                        py1,
                        px2,
                        py2,
                    )

            return best_person

        except Exception as exc:

            print(
                "[RuleDetector] "
                f"Person/car association failed: {exc}"
            )

            return None

    # -------------------------------------------------------------------
    # SEATBELT CHECK
    # -------------------------------------------------------------------

    # Minimum car width (px) before the windscreen crop is worth checking.
    WINDSHIELD_MIN_WIDTH = 120
    WINDSHIELD_FALLBACK = True

    @staticmethod
    def _clip_crop(frame, box):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(v) for v in box)
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2]
        return crop if crop.size else None

    def _windshield_crops(self, frame, vehicle_box):
        """Windscreen band of a car seen from the front: roughly 15-65 % of the box
        height, inset 5 % at the sides; returned whole and as left/right halves."""
        x1, y1, x2, y2 = (int(v) for v in vehicle_box)
        bw, bh = x2 - x1, y2 - y1
        if bw < self.WINDSHIELD_MIN_WIDTH or bh <= 0:
            return []
        wx1, wx2 = x1 + int(0.05 * bw), x2 - int(0.05 * bw)
        wy1, wy2 = y1 + int(0.15 * bh), y1 + int(0.65 * bh)
        mid = (wx1 + wx2) // 2
        out = []
        for box in ((wx1, wy1, wx2, wy2), (wx1, wy1, mid, wy2), (mid, wy1, wx2, wy2)):
            c = self._clip_crop(frame, box)
            if c is not None:
                out.append(c)
        return out

    def _seatbelt_scores(self, crop):
        """(best no-seatbelt confidence, best seatbelt confidence) on one crop."""
        results = self.seatbelt_model(
            _clahe_if_needed(crop),
            verbose=False,
            conf=0.15,
            imgsz=self.SEATBELT_IMGSZ,
        )
        if not results:
            return 0.0, 0.0
        names = getattr(self.seatbelt_model, "names", {}) or {}
        no_c = yes_c = 0.0
        for det in results[0].boxes:
            label = str(names.get(int(det.cls[0]), "")).strip().lower()
            conf = float(det.conf[0])
            if label in {"no_seatbelt", "no seatbelt", "no-seatbelt", "without_seatbelt",
                         "without seatbelt", "unbelted"}:
                no_c = max(no_c, conf)
            elif label in {"seatbelt", "seat belt", "belted"}:
                yes_c = max(yes_c, conf)
        return no_c, yes_c

    def _check_car_seatbelt(
        self,
        frame,
        vehicle_box,
        person_boxes=None,
    ) -> bool:
        """
        Detect no-seatbelt on the person/driver associated with a car.

        IMPORTANT:
            seatbelt_yolov8.pt is run on the PERSON crop, not the
            entire vehicle crop. When no person is found inside the car
            (common behind glass), it is run on the windscreen band and its
            two halves instead, and the strongest scores are combined.
        """

        if self.seatbelt_model is None:
            return False

        try:

            person_box = (
                self._find_person_for_car(
                    frame=frame,
                    vehicle_box=vehicle_box,
                    person_boxes=person_boxes,
                )
            )

            crops = []
            if person_box is not None:
                crop = self._clip_crop(frame, person_box)
                if crop is not None:
                    crops.append(crop)
            elif self.WINDSHIELD_FALLBACK:
                # Occupants behind glass are often missed by the person detector.
                # Zoom into the windscreen band of the car instead: the whole band
                # plus its left and right halves (driver and front passenger).
                crops = self._windshield_crops(frame, vehicle_box)
                if crops:
                    print("[RuleDetector] No person found; checking the windscreen area.")

            if not crops:
                print("[RuleDetector] No person or windscreen area to check for this car.")
                return False

            best_no_seatbelt_conf = 0.0
            best_seatbelt_conf = 0.0
            for crop in crops:
                no_c, yes_c = self._seatbelt_scores(crop)
                best_no_seatbelt_conf = max(best_no_seatbelt_conf, no_c)
                best_seatbelt_conf = max(best_seatbelt_conf, yes_c)

            # -----------------------------------------------------------
            # DEBUG OUTPUT
            # -----------------------------------------------------------

            print(
                "[RuleDetector] Seatbelt:"
                f" no_seatbelt="
                f"{best_no_seatbelt_conf:.3f}"
                f" seatbelt="
                f"{best_seatbelt_conf:.3f}"
            )

            # -----------------------------------------------------------
            # DECISION
            #
            # A no-seatbelt violation requires:
            #
            #   no_seatbelt >= 0.50
            #
            # AND
            #
            #   no_seatbelt >= seatbelt
            #
            # This prevents a weak no-seatbelt detection from
            # overriding a stronger seatbelt detection.
            # -----------------------------------------------------------

            if (
                best_no_seatbelt_conf
                >= self.NO_SEATBELT_CONF
                and
                best_no_seatbelt_conf
                >= best_seatbelt_conf + self.SEATBELT_MARGIN
            ):

                print(
                    "[RuleDetector] "
                    "NO SEATBELT CONFIRMED"
                )

                return True

            return False

        except Exception as exc:

            print(
                "[RuleDetector] "
                f"Car seatbelt check failed: {exc}"
            )

            return False

    # -------------------------------------------------------------------
    # LICENSE PLATE LOCALISATION
    # -------------------------------------------------------------------

    def locate_plate(
        self,
        frame,
        vehicle_box,
    ) -> Optional[
        Tuple[list, float]
    ]:
        """
        Return plate bbox and confidence.

        OCR remains handled by NumberPlateRecognizer.
        """

        if self.plate_model is None:
            return None

        try:

            x1, y1, x2, y2 = map(
                int,
                vehicle_box
            )

            h, w = frame.shape[:2]

            x1 = max(
                0,
                x1
            )

            y1 = max(
                0,
                y1
            )

            x2 = min(
                w,
                x2
            )

            y2 = min(
                h,
                y2
            )

            if (
                x2 <= x1
                or y2 <= y1
            ):
                return None

            crop = frame[
                y1:y2,
                x1:x2
            ]

            if crop.size == 0:
                return None

            results = self.plate_model(
                crop,
                verbose=False,
                conf=0.30,
                imgsz=320,
            )

            if not results:
                return None

            result = results[0]

            best = None
            best_conf = 0.0

            for detection in result.boxes:

                confidence = float(
                    detection.conf[0]
                )

                if confidence <= best_conf:
                    continue

                px1, py1, px2, py2 = map(
                    int,
                    detection.xyxy[0].tolist()
                )

                # Number plates are wider than tall (1-line ~4:1, 2-line
                # ~1.5:1). Taller-than-wide boxes are localiser false
                # positives (seen on faces/legs in the project footage).
                if (px2 - px1) < (py2 - py1):
                    continue

                best = [
                    x1 + px1,
                    y1 + py1,
                    x1 + px2,
                    y1 + py2,
                ]

                best_conf = confidence

            if best is None:
                return None

            return (
                best,
                best_conf,
            )

        except Exception as exc:

            print(
                "[RuleDetector] "
                f"Plate localisation failed: {exc}"
            )

            return None

    # -------------------------------------------------------------------
    # MAIN OBSERVE FUNCTION
    # -------------------------------------------------------------------

    def observe(
        self,
        tracker_id: str,
        vehicle_type: str,
        frame,
        vehicle_box,
        person_boxes=None,
    ) -> List[str]:
        """
        Check rules for one tracked vehicle.

        Returns only newly confirmed violations.

        Example:

            frame 1 → no seatbelt
            frame 2 → no seatbelt
            frame 3 → already confirmed → nothing

        Result:

            ["no_seatbelt"]
        """

        confirmed_now: List[str] = []

        checks: Dict[str, bool] = {}

        vt = (
            vehicle_type or ""
        ).strip().lower()

        # ---------------------------------------------------------------
        # VEHICLE TYPE NORMALISATION
        # ---------------------------------------------------------------

        is_bike = vt in {
            "bike",
            "motorcycle",
            "motorbike",
            "two-wheeler",
            "two_wheeler",
        }

        is_car = vt in {
            "car",
            "sedan",
            "hatchback",
            "suv",
        }

        # ---------------------------------------------------------------
        # MOTORCYCLE RULES
        # ---------------------------------------------------------------

        if is_bike:

            riders = self.riders_for(tracker_id)

            if riders is not None:
                # Frame context available: count uniquely-assigned riders.
                # The combined model's triple_riding class can corroborate
                # when one of three riders is hidden (>= 2 visible).
                # 5+ "riders" on one two-wheeler is an association failure in a
                # dense queue, not a violation we can issue on.
                checks["three_seater"] = len(riders) <= 4 and (
                    len(riders) >= self.TRIPLE_RIDING_COUNT
                    or (
                        len(riders) >= self.TRIPLE_RIDING_COUNT - 1
                        and self._has(self.twowheeler_model, frame,
                                      self._rider_crop_box(frame, vehicle_box, riders),
                                      "three_seater")
                    )
                )
            else:
                checks[
                    "three_seater"
                ] = self._is_triple_riding(
                    frame,
                    vehicle_box,
                    person_boxes=person_boxes,
                )

            # Disagreement gate: standalone helmet model (p_s) vs combined
            # two-wheeler model (p_c) on the SAME crop -- the bike grown to
            # include its riders, scored only in the riders' head regions.
            crop_box = self._rider_crop_box(frame, vehicle_box, riders)
            p_s = (
                self._head_conf(self.helmet_model, frame, crop_box, "no_helmet", riders)
                if self.helmet_model is not None else None
            )
            p_c = (
                self._head_conf(self.twowheeler_model, frame, crop_box, "no_helmet", riders)
                if self.twowheeler_model is not None else None
            )

            verdict = self.gate_verdict(p_s, p_c)
            self.gate_stats["evaluations"] += 1

            checks["no_helmet"] = verdict == "issue"

            key = (tracker_id, "no_helmet")
            if verdict == "review":
                self._review_streak[tracker_id] += 1
                if (
                    self._review_streak[tracker_id] >= self.CONFIRM_FRAMES
                    and key not in self._review_queued
                    and "no_helmet" not in self._confirmed[tracker_id]
                ):
                    self._review_queued.add(key)
                    self.gate_stats["review_events"] += 1
                    self._pending_reviews.append({
                        "tracker_id": tracker_id,
                        "rule": "no_helmet",
                        "p_s": p_s,
                        "p_c": p_c,
                        "theta": self.GATE_THETA,
                    })
            else:
                self._review_streak[tracker_id] = 0

        # ---------------------------------------------------------------
        # CAR RULES
        # ---------------------------------------------------------------

        if is_car:

            checks[
                "no_seatbelt"
            ] = self._check_car_seatbelt(
                frame=frame,
                vehicle_box=vehicle_box,
                person_boxes=person_boxes,
            )

        # ---------------------------------------------------------------
        # TEMPORAL CONFIRMATION
        # ---------------------------------------------------------------

        state = self._streak[
            tracker_id
        ]

        already = self._confirmed[
            tracker_id
        ]

        for rule, hit in checks.items():

            # Already reported for this tracker.
            if rule in already:
                continue

            if hit:

                state[rule] += 1

            else:

                state[rule] = 0

            # -----------------------------------------------------------
            # Confirm after consecutive frames.
            # -----------------------------------------------------------

            needed = (
                self.SEATBELT_CONFIRM_FRAMES
                if rule == "no_seatbelt"
                else self.CONFIRM_FRAMES
            )

            if (
                state[rule]
                >= needed
            ):

                already.add(rule)

                confirmed_now.append(
                    rule
                )

                self.gate_stats[f"issued_{rule}"] += 1

        return confirmed_now

    # -------------------------------------------------------------------
    # RESET TRACKER
    # -------------------------------------------------------------------

    def reset_tracker(
        self,
        tracker_id: str,
    ) -> None:
        """Clear temporal state for a tracker."""

        self._streak.pop(
            tracker_id,
            None,
        )

        self._confirmed.pop(
            tracker_id,
            None,
        )

    # -------------------------------------------------------------------
    # SUMMARY
    # -------------------------------------------------------------------

    def summary(
        self,
    ) -> Dict[str, Dict[str, list]]:
        """Diagnostic information for dashboard."""

        return {
            tracker_id: {
                "confirmed": sorted(
                    rules
                )
            }
            for tracker_id, rules
            in self._confirmed.items()
        }


# ===========================================================================
# UPI QR HELPERS
# ===========================================================================

def upi_intent(
    vpa: str,
    payee: str,
    amount: int,
    tid: str,
    note: str = "",
) -> str:

    from urllib.parse import urlencode

    txn = (
        f"DSX-{tid}"
    )[:35]

    params = {
        "pa": vpa.strip(),
        "pn": payee.strip(),
        "am": str(
            max(
                0,
                int(amount)
            )
        ),
        "cu": "INR",
        "tn": (
            note
            or f"E-Challan #{tid}"
        )[:60],
        "tr": txn,
    }

    return (
        "upi://pay?"
        + urlencode(params)
    )


def upi_qr_png(
    data: str,
) -> bytes:

    import io
    import qrcode

    img = qrcode.make(
        data
    )

    buf = io.BytesIO()

    img.save(
        buf,
        format="PNG",
    )

    return buf.getvalue()