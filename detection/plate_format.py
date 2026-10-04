"""Indian registration-plate grammar and format-aware OCR correction
(paper Sec. 9 future work: "format-aware decoding constrained to valid
registration patterns").

Standard series : SS NN [L..LLL] NNNN   e.g. MH12AB1234, DL3CAF5031, KA05M1234
Bharat series   : YY BH NNNN L[L]        e.g. 22BH1234AB

OCR engines confuse look-alike glyphs (0/O/D/Q, 1/I/L, 2/Z, 5/S, 8/B, 6/G,
7/T, 4/A). Because every position of a plate is known to be a letter or a
digit, a read can be repaired by position: try every valid split of the string
into the grammar and substitute only confusable glyphs; keep the split that
needs the fewest substitutions and whose state code is real.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "DN", "GA", "GJ", "HP", "HR", "JH", "JK", "KA", "KL",
    "LA", "LD", "MH", "ML", "MN", "MP", "MZ", "NL", "OD", "OR", "PB", "PY", "RJ", "SK", "TG", "TN", "TR", "TS",
    "UK", "UA", "UP", "WB",
}

STANDARD_RE = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})$")
BH_RE = re.compile(r"^(\d{2})BH(\d{4})([A-Z]{1,2})$")

TO_LETTER = {"0": "O", "1": "I", "2": "Z", "3": "B", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "U": "0", "I": "1", "L": "1", "J": "1", "Z": "2", "S": "5", "B": "8",
            "G": "6", "T": "7", "A": "4"}


def clean(text: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def is_valid(plate: str) -> bool:
    """Plates carry a two-digit, zero-padded RTO code (MH 01, KA 05); Delhi is the
    exception that also uses single-digit district codes (DL 3C AF 5031)."""
    p = clean(plate)
    m = STANDARD_RE.match(p)
    if m:
        state, rto = m.group(1), m.group(2)
        if state not in STATE_CODES or int(rto) == 0:
            return False
        return len(rto) == 2 or state == "DL"
    return bool(BH_RE.match(p))


def _fit(chars: str, kinds: str) -> Optional[Tuple[str, int]]:
    """kinds: 'L'/'D' per position. Returns (fixed string, substitutions) or None."""
    out, subs = [], 0
    for c, k in zip(chars, kinds):
        if k == "L":
            if c.isalpha():
                out.append(c)
            elif c in TO_LETTER:
                out.append(TO_LETTER[c]); subs += 1
            else:
                return None
        else:
            if c.isdigit():
                out.append(c)
            elif c in TO_DIGIT:
                out.append(TO_DIGIT[c]); subs += 1
            else:
                return None
    return "".join(out), subs


def candidates(text: str) -> List[Tuple[str, int]]:
    s = clean(text)
    res = []
    n = len(s)
    for rto in (1, 2):
        for series in (0, 1, 2, 3):
            if 2 + rto + series + 4 == n:
                fit = _fit(s, "LL" + "D" * rto + "L" * series + "DDDD")
                if fit and is_valid(fit[0]):
                    res.append(fit)
    for tail in (1, 2):
        if 2 + 2 + 4 + tail == n:
            fit = _fit(s, "DD" + "LL" + "DDDD" + "L" * tail)
            if fit and fit[0][2:4] == "BH" and is_valid(fit[0]):
                res.append(fit)
    return sorted(res, key=lambda x: x[1])


def correct(text: Optional[str], max_subs: int = 2) -> Tuple[Optional[str], int]:
    """Best grammar-valid repair of an OCR read, or (None, -1) if none within max_subs."""
    s = clean(text)
    if not s:
        return None, -1
    if is_valid(s):
        return s, 0
    c = candidates(s)
    if c and c[0][1] <= max_subs:
        return c[0]
    return None, -1
