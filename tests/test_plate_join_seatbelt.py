"""Plate pieces are joined in reading order; cars with no detected person get a
windscreen check. OCR and YOLO are faked."""
import numpy as np

from detection.npr import NumberPlateRecognizer


def _box(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def test_join_one_line_split_in_two():
    res = [(_box(120, 10, 220, 40), "BY 4812", 0.9), (_box(10, 12, 110, 40), "HR26", 0.8)]
    text, conf = NumberPlateRecognizer._join_pieces(res)
    assert text == "HR26BY4812" and 0.8 < conf < 0.9


def test_join_two_line_bike_plate_top_first():
    res = [(_box(10, 60, 200, 100), "4602", 0.9), (_box(20, 10, 190, 50), "PY02 L", 0.7)]
    assert NumberPlateRecognizer._join_pieces(res)[0] == "PY02L4602"


class _FakeReader:
    def __init__(self, results):
        self.results = results

    def readtext(self, img, detail=1, allowlist=None):
        return self.results


def test_best_read_prefers_whole_plate_over_fragment():
    npr = NumberPlateRecognizer.__new__(NumberPlateRecognizer)
    npr.format_correction = False
    npr.enabled = True
    npr.reader = _FakeReader([(_box(10, 10, 60, 40), "DL8", 0.6), (_box(70, 10, 200, 40), "CZ7230", 0.95)])
    text, _ = npr._best_read([np.zeros((40, 200, 3), np.uint8)])
    assert text == "DL8CZ7230"


def test_windshield_crops_and_fallback(monkeypatch):
    from detection.rule_violations import RuleViolationDetector

    rv = RuleViolationDetector.__new__(RuleViolationDetector)
    frame = np.zeros((600, 800, 3), np.uint8)
    crops = rv._windshield_crops(frame, (100, 100, 500, 400))
    assert len(crops) == 3 and crops[0].shape[:2] == (150, 360)
    assert rv._windshield_crops(frame, (100, 100, 150, 140)) == []  # too small to judge

    rv.seatbelt_model = object()
    rv.NO_SEATBELT_CONF, rv.SEATBELT_MARGIN = 0.5, 0.12
    monkeypatch.setattr(rv, "_find_person_for_car", lambda **k: None)
    seen = []
    monkeypatch.setattr(rv, "_seatbelt_scores", lambda c: (seen.append(c.shape) or (0.8, 0.1)))
    assert rv._check_car_seatbelt(frame, (100, 100, 500, 400)) is True
    assert len(seen) == 3


class _FakePred:
    def __init__(self, plate, probs):
        self.plate, self.char_probs = plate, probs


class _FakeFast:
    def __init__(self, plate, p):
        self.plate, self.p, self.calls = plate, p, 0

    def run(self, img, return_confidence=False):
        self.calls += 1
        return [_FakePred(self.plate, [self.p] * len(self.plate) + [0.99, 0.99])]


class _CountingReader(_FakeReader):
    def __init__(self, results):
        super().__init__(results)
        self.calls = 0

    def readtext(self, img, detail=1, allowlist=None):
        self.calls += 1
        return self.results


def _npr(fast, reader, engine="auto"):
    npr = NumberPlateRecognizer.__new__(NumberPlateRecognizer)
    npr.format_correction, npr.enabled, npr.engine = False, True, engine
    npr.fast, npr._fast_rgb, npr.reader, npr._easy_failed, npr.use_gpu = fast, True, reader, False, False
    return npr


def test_confident_fast_read_skips_easyocr():
    reader = _CountingReader([(_box(0, 0, 10, 10), "XX99XX9999", 0.99)])
    npr = _npr(_FakeFast("MH12AB1234", 0.95), reader)
    assert npr._best_read([np.zeros((40, 160, 3), np.uint8)])[0] == "MH12AB1234"
    assert reader.calls == 0


def test_unsure_fast_read_falls_back_to_easyocr():
    reader = _CountingReader([(_box(0, 0, 10, 10), "KA05MN4321", 0.9)])
    npr = _npr(_FakeFast("KA05MN432", 0.3), reader)
    assert npr._best_read([np.zeros((40, 160, 3), np.uint8)])[0] == "KA05MN4321"
    assert reader.calls > 0


def test_fastplate_only_never_uses_easyocr():
    reader = _CountingReader([(_box(0, 0, 10, 10), "KA05MN4321", 0.9)])
    npr = _npr(_FakeFast("KA05MN432", 0.3), reader, engine="fastplate")
    npr._best_read([np.zeros((40, 160, 3), np.uint8)])
    assert reader.calls == 0


def test_default_engine_falls_back_to_easyocr_when_fastplate_missing(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("fast_plate_ocr") or name == "easyocr":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.delenv("DSX_OCR_ENGINE", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    npr = NumberPlateRecognizer()
    assert npr.fast is None and npr.engine == "easyocr"
