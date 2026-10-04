"""
ANPR (Automatic Number-Plate Recognition) demo helper.

This lets the dashboard demonstrate REAL plate recognition on a still image or a
single video frame — without needing a live webcam — which is handy for a viva.

Pipeline:
  1. Detect candidate plate regions (Haar cascade for Russian/EU-style plates ships
     with OpenCV and works well on most rectangular plates; falls back to the lower-
     centre crop heuristic if the cascade finds nothing).
  2. Pre-process each candidate (greyscale, resize, denoise, adaptive threshold).
  3. OCR with EasyOCR (the same engine used in the live pipeline).
  4. Validate against the Indian plate format and return the best match + confidence.

If EasyOCR isn't installed, the function returns a clear, honest status so the UI can
explain what to install rather than silently faking a result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except Exception:  # pragma: no cover
    _HAS_CV2 = False

# Indian plate format e.g. MH01AB1234 (state + RTO + series + number)
PLATE_REGEX = re.compile(r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{3,4}")

_READER = None


def _get_reader(use_gpu: bool = False):
    """Lazy-load a single EasyOCR reader (slow to construct)."""
    global _READER
    if _READER is not None:
        return _READER
    try:
        import easyocr
        _READER = easyocr.Reader(["en"], gpu=use_gpu)
    except Exception:
        _READER = False  # mark as unavailable
    return _READER


@dataclass
class PlateResult:
    ok: bool
    plate: Optional[str] = None
    confidence: float = 0.0
    candidates: List[Tuple[str, float]] = field(default_factory=list)
    boxes: List[Tuple[int, int, int, int]] = field(default_factory=list)
    message: str = ""
    annotated: Optional["np.ndarray"] = None


def _candidate_regions(gray: "np.ndarray") -> List[Tuple[int, int, int, int]]:
    """Find rectangular plate-like regions. Try the bundled Haar cascade first."""
    boxes: List[Tuple[int, int, int, int]] = []
    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_russian_plate_number.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        if not cascade.empty():
            found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 18))
            boxes = [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in found]
    except Exception:
        boxes = []
    return boxes


def _preprocess(crop: "np.ndarray") -> List["np.ndarray"]:
    """Deblur + sharpen + binarise. Returns several variants to OCR (the best wins)."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    h, w = g.shape[:2]
    if max(h, w) < 240:
        scale = 240.0 / max(1, max(h, w))
        g = cv2.resize(g, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    den = cv2.bilateralFilter(g, 9, 75, 75)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    eq = clahe.apply(den)
    blur = cv2.GaussianBlur(eq, (0, 0), 3)
    sharp = cv2.addWeighted(eq, 1.6, blur, -0.6, 0)   # unsharp mask = deblur
    th = cv2.adaptiveThreshold(sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 31, 9)
    return [sharp, eq, th]


def _ocr_text(reader, img) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    variants = img if isinstance(img, list) else [img]
    for v in variants:
        for _, text, conf in reader.readtext(v, detail=1):
            cleaned = re.sub(r"[^A-Za-z0-9]", "", text).upper()
            if len(cleaned) < 4:
                continue
            m = PLATE_REGEX.search(cleaned)
            out.append((m.group(0) if m else cleaned, float(conf)))
    return out


def scan_plate_image(image_bgr: "np.ndarray", use_gpu: bool = False) -> PlateResult:
    """Run ANPR on a full BGR image (a photo or a video frame). Returns a PlateResult."""
    if not _HAS_CV2:
        return PlateResult(False, message="OpenCV is not available in this environment.")
    reader = _get_reader(use_gpu)
    if not reader:
        return PlateResult(
            False,
            message="EasyOCR is not installed here. Install it with `pip install easyocr` "
                    "to run live plate recognition (it is listed in requirements.txt).",
        )

    annotated = image_bgr.copy()
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    boxes = _candidate_regions(gray)

    all_candidates: List[Tuple[str, float]] = []
    used_boxes: List[Tuple[int, int, int, int]] = []

    if boxes:
        for (x, y, w, h) in boxes:
            crop = image_bgr[y:y + h, x:x + w]
            if crop.size == 0:
                continue
            cands = _ocr_text(reader, _preprocess(crop))
            if cands:
                all_candidates.extend(cands)
                used_boxes.append((x, y, w, h))
                cv2.rectangle(annotated, (x, y), (x + w, y + h), (59, 169, 242), 3)
    # Fallback: OCR the whole (pre-processed) image if no boxes produced text
    if not all_candidates:
        cands = _ocr_text(reader, _preprocess(image_bgr))
        all_candidates.extend(cands)

    if not all_candidates:
        return PlateResult(False, message="No readable plate text found in the image.",
                           annotated=annotated)

    # Prefer candidates that match the Indian plate format, then by confidence.
    def _score(c: Tuple[str, float]) -> Tuple[int, float]:
        text, conf = c
        return (1 if PLATE_REGEX.fullmatch(text) else 0, conf)

    all_candidates.sort(key=_score, reverse=True)
    best_text, best_conf = all_candidates[0]
    return PlateResult(
        ok=True, plate=best_text, confidence=best_conf,
        candidates=all_candidates[:5], boxes=used_boxes,
        message="Plate recognised.", annotated=annotated,
    )


def make_synthetic_plate(text: str = "MH12DE1433", blur: bool = False) -> "np.ndarray":
    """Render a realistic Indian-style number plate (yellow commercial plate) so the
    demo works without a sample photo. If blur=True, motion blur is applied so you can
    demonstrate the deblur step recovering it."""
    if not _HAS_CV2:
        raise RuntimeError("OpenCV required")
    # yellow plate with black border + black text (common Indian commercial plate)
    plate = np.full((150, 470, 3), (20, 200, 240), np.uint8)  # BGR yellow
    cv2.rectangle(plate, (5, 5), (464, 144), (10, 10, 10), 4)
    # spaced text like a real plate: "MH 12 DE 1433"
    spaced = text[:2] + " " + text[2:4] + " " + text[4:6] + " " + text[6:]
    cv2.putText(plate, spaced, (18, 100), cv2.FONT_HERSHEY_DUPLEX, 1.7, (10, 10, 10), 4, cv2.LINE_AA)
    # mount on a darker car-rear background
    bg = np.full((380, 660, 3), 55, np.uint8)
    bg[150:300, 95:565] = plate
    if blur:
        k = 15
        kernel = np.zeros((k, k)); kernel[k // 2, :] = 1.0 / k
        bg = cv2.filter2D(bg, -1, kernel)
    return bg


def deblur_preview(image_bgr: "np.ndarray") -> Optional["np.ndarray"]:
    """Return the sharpened (deblurred) version of an image, for a before/after view."""
    if not _HAS_CV2:
        return None
    return _preprocess(image_bgr)[0]  # the unsharp-masked variant
