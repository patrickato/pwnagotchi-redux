"""Threat-report aggregator — severity-sorted, glass-box summary of alerts."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from redux.detect.alerts import Alert

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _sev_key(a: Alert) -> tuple:
    return (_SEVERITY_ORDER.get((a.severity or "").lower(), 9), -a.ts, a.kind.value)


@dataclass(frozen=True)
class ThreatReport:
    """One readable, severity-sorted rollup of recent alerts."""

    alerts: tuple[Alert, ...] = ()
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None
    summary: str = ""
    counts_by_severity: dict = field(default_factory=dict)
    counts_by_kind: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.alerts)

    def to_text(self) -> str:
        """Human-readable multi-line report (glass-box)."""
        lines: List[str] = [self.summary or f"Threat report: {self.total} alert(s)"]
        if self.counts_by_severity:
            parts = [f"{k}={v}" for k, v in sorted(self.counts_by_severity.items())]
            lines.append("  by severity: " + ", ".join(parts))
        if self.counts_by_kind:
            parts = [f"{k}={v}" for k, v in sorted(self.counts_by_kind.items())]
            lines.append("  by kind: " + ", ".join(parts))
        for i, a in enumerate(self.alerts, 1):
            where = a.bssid or a.ssid or "—"
            lines.append(
                f"  {i}. [{a.severity}] {a.kind.value} @ {a.ts:.1f} ({where}): {a.reason}"
            )
        return "\n".join(lines)


def build_threat_report(
    alerts: Sequence[Alert],
    *,
    since_ts: Optional[float] = None,
    until_ts: Optional[float] = None,
    limit: Optional[int] = None,
) -> ThreatReport:
    """Filter, sort by severity (critical first), build glass-box summary."""
    filtered: List[Alert] = []
    for a in alerts:
        if since_ts is not None and a.ts < since_ts:
            continue
        if until_ts is not None and a.ts > until_ts:
            continue
        filtered.append(a)

    ordered = sorted(filtered, key=_sev_key)
    if limit is not None:
        ordered = ordered[: int(limit)]

    by_sev = Counter((a.severity or "warning").lower() for a in ordered)
    by_kind = Counter(a.kind.value for a in ordered)

    if not ordered:
        summary = "Threat report: no alerts in window"
    else:
        top = ordered[0]
        summary = (
            f"Threat report: {len(ordered)} alert(s); "
            f"highest severity '{top.severity}' ({top.kind.value}): {top.reason}"
        )

    return ThreatReport(
        alerts=tuple(ordered),
        since_ts=since_ts,
        until_ts=until_ts,
        summary=summary,
        counts_by_severity=dict(by_sev),
        counts_by_kind=dict(by_kind),
    )
