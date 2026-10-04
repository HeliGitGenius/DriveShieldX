from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

import cv2

# Indian plate (e.g. MH01AB1234). Kept for FORMAT BONUS scoring, not as a hard filter.
INDIAN_PLATE_REGEX = re.compile(r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{3,4}")
# General plate: a run of 5–10 alphanumerics with at least one letter and one digit.
GENERAL_PLATE_REGEX = re.compile(r"[A-Z0-9]{5,11}")
PLATE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


FASTPLATE_DEFAULT_MODEL = "cct-s-v2-global-model"
# A fast-plate-ocr read at or above this mean character confidence is accepted
# without asking EasyOCR; below it EasyOCR is tried as the backup.
FASTPLATE_ACCEPT_CONF = 0.6


class NumberPlateRecognizer:
    """Number-plate reader for plate crops.

    Engines (DSX_OCR_ENGINE or the `engine` argument):
      fastplate (default) fast-plate-ocr (MIT, ONNX, trained on plate crops); if it is
                not installed, EasyOCR is used instead
      auto      fast-plate-ocr, plus EasyOCR on plates it is unsure about
      easyocr   EasyOCR only (the original reader)
    Held-out test, 291 Indian plate photos: exact match 36.8 % (fastplate) vs 16.5 %
    (EasyOCR) with format decoding; auto gave the same 36.8 % at ~7.5x the time.
    Locates the plate region inside the vehicle box (Haar cascade + heuristic fallback)
    when no YOLO plate box is given, then reads the crop and picks the best candidate."""

    def __init__(self, use_gpu: bool = False, format_correction: bool = True, engine: Optional[str] = None) -> None:
        # Repair look-alike glyphs (0/O, 2/Z, 5/S, 8/B ...) by position using the
        # Indian plate grammar (detection/plate_format.py). Only applied when the
        # repaired string is a valid plate needing <= 2 substitutions.
        self.format_correction = format_correction
        self.engine = (engine or os.environ.get("DSX_OCR_ENGINE") or "fastplate").strip().lower()
        self.use_gpu = use_gpu
        self.reader = None
        self.fast = None
        self._fast_rgb = False
        self._easy_failed = False
        self._cascade = None
        if self.engine in ("auto", "fastplate"):
            try:
                from fast_plate_ocr import LicensePlateRecognizer
                model = os.environ.get("DSX_FASTPLATE_MODEL", FASTPLATE_DEFAULT_MODEL)
                self.fast = LicensePlateRecognizer(model, device="cuda" if use_gpu else "cpu")
                mode = str(getattr(getattr(self.fast, "config", None), "image_color_mode", "rgb")).lower()
                self._fast_rgb = mode != "grayscale"
            except Exception as exc:
                self.fast = None
                if self.engine == "fastplate":
                    print(f"[NPR] fast-plate-ocr unavailable ({exc}); using EasyOCR. "
                          "Install it with: pip install \"fast-plate-ocr[onnx]\"")
                    self.engine = "easyocr"
        if self.engine == "easyocr" or (self.engine == "auto" and self.fast is None):
            self._easy_reader()  # load now: it is the main reader
        self.enabled = self.fast is not None or self.reader is not None or (
            self.engine == "auto" and self._easyocr_installed())

    @staticmethod
    def _easyocr_installed() -> bool:
        import importlib.util
        return importlib.util.find_spec("easyocr") is not None

    def _easy_reader(self):
        """EasyOCR, loaded on first use (as the backup it is often never needed)."""
        if (self.reader is None and not getattr(self, "_easy_failed", False)
                and getattr(self, "engine", "easyocr") in ("auto", "easyocr")):
            try:
                import easyocr
                self.reader = easyocr.Reader(["en"], gpu=self.use_gpu)
            except Exception:
                self._easy_failed = True
        return self.reader

    @property
    def engine_name(self) -> str:
        if getattr(self, "fast", None) is not None:
            return "fast-plate-ocr" + (" + EasyOCR backup" if self.engine == "auto" else "")
        return "EasyOCR"

        # Load the bundled plate Haar cascade (helps locate the actual plate rectangle).
        try:
            path = cv2.data.haarcascades + "haarcascade_russian_plate_number.xml"
            c = cv2.CascadeClassifier(path)
            if not c.empty():
                self._cascade = c
        except Exception:
            self._cascade = None

    def _vehicle_crop(self, frame, bbox):
        x1, y1, x2, y2 = map(int, bbox)
        if x2 <= x1 or y2 <= y1:
            return None
        v = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        return v if v.size else None

    def _plate_regions(self, vehicle) -> List:
        """Return candidate plate crops: cascade detections first, then a heuristic
        lower-centre crop as a fallback."""
        regions = []
        if vehicle is None or vehicle.size == 0:
            return regions
        gray = cv2.cvtColor(vehicle, cv2.COLOR_BGR2GRAY)
        if self._cascade is not None:
            try:
                found = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 14))
                for (x, y, w, h) in found:
                    pad = int(h * 0.15)
                    crop = vehicle[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
                    if crop.size:
                        regions.append(crop)
            except Exception:
                pass
        # Heuristic fallback: plates usually sit in the lower-centre of the vehicle box.
        h, w = vehicle.shape[:2]
        crop = vehicle[int(h * 0.55): min(h, int(h * 0.95)), int(w * 0.10): min(w, int(w * 0.90))]
        if crop.size:
            regions.append(crop)
        return regions

    def _enhance(self, crop):
        """Deblur + sharpen a (possibly motion-blurred / low-res) plate crop so OCR
        can read it. Returns a list of processed single-channel variants to try."""
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        h, w = gray.shape[:2]
        if max(h, w) < 240:
            scale = 240.0 / max(1, max(h, w))
            gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
        den = cv2.bilateralFilter(gray, 9, 75, 75)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        eq = clahe.apply(den)
        blur = cv2.GaussianBlur(eq, (0, 0), 3)
        sharp = cv2.addWeighted(eq, 1.6, blur, -0.6, 0)
        th = cv2.adaptiveThreshold(sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 31, 9)
        return [sharp, eq, th]

    @staticmethod
    def _looks_like_plate(s: str) -> bool:
        return bool(s) and len(s) >= 5 and any(c.isalpha() for c in s) and any(c.isdigit() for c in s)

    def read_plate_crop(self, frame, plate_bbox, pad_x: float = 0.15, pad_y: float = 0.12) -> Tuple[Optional[str], float]:
        """OCR a plate box that was localised by the YOLOv8 plate detector
        (two-stage ANPR, paper Sec. 4.5): pad (wider at the sides, where the state
        code sits), deblur/sharpen, EasyOCR."""
        if not self.enabled:
            return None, 0.0
        x1, y1, x2, y2 = map(int, plate_bbox)
        h, w = frame.shape[:2]
        px, py = int((x2 - x1) * pad_x), int((y2 - y1) * pad_y)
        crop = frame[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)]
        if crop.size == 0:
            return None, 0.0
        return self._best_read([crop])

    def read_plate(self, frame, bbox) -> Tuple[Optional[str], float]:
        if not self.enabled:
            return None, 0.0
        vehicle = self._vehicle_crop(frame, bbox)
        if vehicle is None:
            return None, 0.0
        return self._best_read(self._plate_regions(vehicle))

    @staticmethod
    def _join_pieces(results) -> Tuple[str, float]:
        """EasyOCR often splits one plate into pieces ("HR 26" | "BY 4812", or the two
        lines of a bike plate). Join them in reading order: top line first, then left
        to right. Returns (joined text, mean confidence)."""
        items = []
        for box, text, conf in results:
            t = re.sub(r"[^A-Za-z0-9]", "", text).upper()
            if not t:
                continue
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), t, float(conf)))
        if not items:
            return "", 0.0
        items.sort(key=lambda it: it[0])
        lines, cur = [], [items[0]]
        for it in items[1:]:
            ref = cur[-1]
            if abs(it[0] - ref[0]) <= 0.5 * max(it[1], ref[1], 1):
                cur.append(it)
            else:
                lines.append(cur)
                cur = [it]
        lines.append(cur)
        text = "".join(t for line in lines for _, _, _, t, _ in sorted(line, key=lambda it: it[2]))
        n = sum(len(it[3]) for it in items)
        conf = sum(it[4] * len(it[3]) for it in items) / n
        return text, conf

    def _candidate_score(self, candidate: str, conf: float) -> float:
        score = conf
        if INDIAN_PLATE_REGEX.fullmatch(candidate):
            score += 0.25
        if self.format_correction:
            from detection.plate_format import correct
            if correct(candidate)[0]:
                score += 0.25
        return score

    def _fast_read(self, crop) -> Tuple[str, float]:
        img = crop if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if self._fast_rgb else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        pred = self.fast.run(img, return_confidence=True)[0]
        text = re.sub(r"[^A-Za-z0-9]", "", str(getattr(pred, "plate", pred))).upper()
        probs = getattr(pred, "char_probs", None)
        if probs is None or not text:
            return text, 0.0
        probs = [float(p) for p in list(probs)[: len(text)]]
        return text, (sum(probs) / len(probs)) if probs else 0.0

    def _consider(self, cleaned: str, conf: float, best):
        if len(cleaned) < 5:
            return best
        # An Indian-format run inside noise (e.g. "IND" band) is preferred.
        m = INDIAN_PLATE_REGEX.search(cleaned)
        if m:
            candidate = m.group(0)
        else:
            gm = GENERAL_PLATE_REGEX.search(cleaned)
            candidate = gm.group(0) if gm else cleaned
        if not self._looks_like_plate(candidate):
            return best
        score = self._candidate_score(candidate, conf)
        return (candidate, score) if score > best[1] else best

    def _best_read(self, regions) -> Tuple[Optional[str], float]:
        best = (None, 0.0)
        fast_conf = 0.0
        if getattr(self, "fast", None) is not None:
            for region in regions:
                try:
                    text, conf = self._fast_read(region)
                except Exception as exc:
                    print(f"[NPR] fast-plate-ocr failed on a crop: {exc}")
                    continue
                if conf > fast_conf and len(text) >= 5:
                    fast_conf = conf
                best = self._consider(text, conf, best)
        need_backup = best[0] is None or fast_conf < FASTPLATE_ACCEPT_CONF
        reader = self._easy_reader() if (need_backup and getattr(self, "engine", "easyocr") != "fastplate") else None
        if reader is None and getattr(self, "fast", None) is None:
            reader = self.reader  # EasyOCR-only instances (and tests) set it directly
        for region in (regions if reader is not None else []):
            for variant in self._enhance(region):
                results = reader.readtext(variant, detail=1, allowlist=PLATE_CHARS)
                pieces = [(re.sub(r"[^A-Za-z0-9]", "", t).upper(), float(c)) for _, t, c in results]
                for cleaned, conf in pieces + [self._join_pieces(results)]:
                    best = self._consider(cleaned, conf, best)
        best_text, best_conf = best
        if best_text and self.format_correction:
            from detection.plate_format import correct
            fixed, subs = correct(best_text)
            if fixed:
                best_text = fixed
        return best_text, min(best_conf, 1.0)
