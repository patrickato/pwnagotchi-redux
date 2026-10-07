"""Hardware-free tests for confidence helper (Grok D6)."""
import pytest

from redux.detect.alerts import Alert, AlertKind
from redux.detect.confidence import read_confidence, with_confidence


def test_with_confidence():
    a = Alert(kind=AlertKind.DEAUTH_FLOOD, reason="flood", ts=1.0)
    b = with_confidence(a, 0.85, "20 frames in 5s exceeds threshold")
    assert read_confidence(b) == pytest.approx(0.85)
    assert b.detail["confidence_rationale"].startswith("20 frames")


def test_clamp():
    a = Alert(kind=AlertKind.DEAUTH_FLOOD, reason="x", ts=1.0)
    assert read_confidence(with_confidence(a, 1.5, "hi")) == 1.0
    assert read_confidence(with_confidence(a, -1.0, "lo")) == 0.0


def test_empty_rationale_rejected():
    a = Alert(kind=AlertKind.DEAUTH_FLOOD, reason="x", ts=1.0)
    with pytest.raises(ValueError):
        with_confidence(a, 0.5, "")
