"""Unit tests for the disagreement gate (paper Sec. 4.4). No model weights needed.

Run:  python -m pytest tests/test_gate.py -q
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detection.rule_violations import RuleViolationDetector  # noqa: E402


def make():
    d = RuleViolationDetector.__new__(RuleViolationDetector)  # skip model loading
    d.gate_stats = Counter()
    d.GATE_THETA = 0.15
    d.GATE_ENABLED = True
    return d


def test_agree_positive_issues():
    assert make().gate_verdict(0.70, 0.40) == "issue"


def test_agree_negative_clears():
    assert make().gate_verdict(0.05, 0.00) == "clear"


def test_disagreement_goes_to_review_not_challan():
    d = make()
    assert d.gate_verdict(0.00, 0.58) == "review"
    assert d.gate_verdict(0.55, 0.00) == "review"
    assert d.gate_stats["disagree"] == 2


def test_legacy_or_rule_only_when_gate_disabled():
    d = make()
    d.GATE_ENABLED = False
    assert d.gate_verdict(0.00, 0.58) == "issue"


def test_single_model_fallback():
    d = make()
    assert d.gate_verdict(None, 0.58) == "issue"
    assert d.gate_verdict(0.05, None) == "clear"
    assert d.gate_stats["single_model"] == 2
