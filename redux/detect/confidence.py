"""Per-alert confidence score (0..1) + rationale into Alert.detail."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional

from redux.detect.alerts import Alert


def with_confidence(
    alert: Alert,
    confidence: float,
    rationale: str,
) -> Alert:
    """Return a copy of alert with confidence + rationale in detail."""
    c = max(0.0, min(1.0, float(confidence)))
    if not (rationale or "").strip():
        raise ValueError("confidence rationale must be non-empty (glass-box)")
    detail = dict(alert.detail or {})
    detail["confidence"] = c
    detail["confidence_rationale"] = rationale.strip()
    return replace(alert, detail=detail)


def read_confidence(alert: Alert) -> Optional[float]:
    v = (alert.detail or {}).get("confidence")
    return float(v) if v is not None else None
