"""Explain-as-it-goes — the purple learning stream.

Reads the signed auth log (what the box decided and did) and annotates each
offensive action with how a defender detects it and how they'd stop it, from
the purple-team knowledge base. You see the red action and the blue answer side
by side, in the order things happened — the fastest way to actually learn why a
technique works and how you'd get caught doing it.

Read-only and mode-agnostic (it's teaching, not firing). Pure: pass auth-log
entries in, get narrated lines out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .defend import explain

# auth-log event (after stripping a "campaign."/"fire." prefix) -> defend KB key
_EVENT_TO_DEFEND = {
    "handshake_capture": "handshake", "handshake": "handshake",
    "pmkid_capture": "pmkid", "pmkid": "pmkid",
    "deauth": "deauth", "offline_crack": "handshake",
    "evil_twin": "evil_twin", "captive_portal": "evil_portal", "karma": "karma",
    "wps": "wps", "net_scan": "netrecon", "pivot": "netrecon", "cred_test": "cred_spray",
}


@dataclass(frozen=True)
class NarratedLine:
    ts: float
    event: str
    detail: str
    technique: Optional[str] = None       # defend-KB technique name (offensive events only)
    detection: Optional[str] = None       # one line: how blue sees it
    mitigation: Optional[str] = None      # one line: how blue stops it


def _key_for(event: str) -> str:
    # "campaign.handshake_capture" / "fire.deauth" -> the action token
    return (event or "").split(".")[-1].strip().lower()


def narrate(entries: Sequence) -> List[NarratedLine]:
    """Pair each auth-log entry with its blue-team answer where one applies."""
    out: List[NarratedLine] = []
    for e in entries:
        event = str(getattr(e, "event", ""))
        detail = getattr(e, "detail", {}) or {}
        det_str = " ".join(f"{k}={v}" for k, v in sorted(detail.items())) if isinstance(detail, dict) else ""
        mapped = _EVENT_TO_DEFEND.get(_key_for(event))
        t = explain(mapped) if mapped else None
        out.append(NarratedLine(
            ts=float(getattr(e, "ts", 0.0) or 0.0), event=event, detail=det_str,
            technique=(t.name if t else None),
            detection=(t.detection[0] if t and t.detection else None),
            mitigation=(t.mitigation[0] if t and t.mitigation else None)))
    return out
