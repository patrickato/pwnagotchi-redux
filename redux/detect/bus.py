"""Alert bus: dedup + severity escalation.

Detectors emit raw Alerts; the bus suppresses near-duplicates and escalates
severity when the same signature keeps firing. Glass-box: escalated alerts
explain the repeat count in their reason.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

from redux.detect.alerts import Alert
from redux.detect.config import DEFAULTS, _opt, merge_options

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _signature(alert: Alert) -> Tuple[str, str, str]:
    """Dedup key: kind + bssid + ssid (stable, no free-text reason)."""
    return (alert.kind.value, alert.bssid or "", alert.ssid or "")


def _rank(sev: str) -> int:
    return _SEVERITY_RANK.get((sev or "").lower(), 0)


class AlertBus:
    """Stateful dedup + escalation over a stream of Alerts."""

    def __init__(self, options: Optional[Mapping[str, Any]] = None) -> None:
        self.options = merge_options(options)
        self.dedup_window_s = float(_opt(self.options, "dedup_window_s"))
        self.escalate_after = int(_opt(self.options, "escalate_after"))
        self.escalate_to = str(_opt(self.options, "escalate_to"))
        # sig -> (last_ts, count, last_severity)
        self._state: Dict[Tuple[str, str, str], Tuple[float, int, str]] = {}

    def reset(self) -> None:
        self._state.clear()

    def publish(self, alert: Alert) -> Optional[Alert]:
        """Accept one alert; return None if suppressed, else possibly escalated."""
        sig = _signature(alert)
        prev = self._state.get(sig)

        if prev is None:
            self._state[sig] = (alert.ts, 1, alert.severity)
            return alert

        last_ts, count, last_sev = prev
        if alert.ts - last_ts > self.dedup_window_s:
            # Window expired — treat as fresh
            self._state[sig] = (alert.ts, 1, alert.severity)
            return alert

        count += 1
        severity = alert.severity
        reason = alert.reason

        # Escalate ONCE, when the running signature first crosses the threshold
        # (judged by the stored severity, not the incoming one). After that the
        # signature stays suppressed within the window — otherwise every repeat
        # would re-emit and defeat the bus's dedup contract.
        if count >= self.escalate_after and _rank(last_sev) < _rank(self.escalate_to):
            severity = self.escalate_to
            reason = (
                f"{alert.reason} "
                f"[escalated to {severity}: seen {count}× within {self.dedup_window_s:.0f}s]"
            )
            out = replace(alert, severity=severity, reason=reason)
            self._state[sig] = (alert.ts, count, severity)
            return out

        # Within window, not yet at escalate threshold → suppress duplicate
        self._state[sig] = (alert.ts, count, last_sev)
        return None

    def publish_many(self, alerts: List[Alert]) -> List[Alert]:
        out: List[Alert] = []
        for a in alerts:
            published = self.publish(a)
            if published is not None:
                out.append(published)
        return out
