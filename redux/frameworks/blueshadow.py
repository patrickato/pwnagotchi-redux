"""Blue-shadow — what would a defender see from what you just did?

For every offensive action, mirror it against your own detector suite: which
detectors would fire, how loud the action is on the air, the ATT&CK technique it
maps to, the D3FEND countermeasure that stops it — and, when the action is loud, a
QUIETER way to reach the same goal. That last part is the point: you learn evasion
by seeing the footprint and choosing to not leave it, not by erasing it after.

Pure view over frameworks/registry (which already pairs each action with the
detectors that should catch it); edits nothing, reads nothing live.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .registry import REGISTRY, actions

# a quieter action that reaches a similar goal with fewer/no detectable frames
_QUIETER: Dict[str, str] = {
    "deauth": "pmkid_capture",
    "handshake_capture": "pmkid_capture",
    "evil_twin": "pmkid_capture",         # AITM is loud; PMKID gets a crackable hash silently
    "captive_portal": "pmkid_capture",
}


def shadow(action: str) -> dict:
    """The defensive footprint of one action + a stealthier alternative if any."""
    m = REGISTRY.get(action)
    if m is None:
        return {"action": action, "known": False,
                "reason": f"no framework mapping for '{action}'"}
    seen = list(m.detects)
    loud = bool(seen)
    stealthier: Optional[dict] = None
    alt = _QUIETER.get(action)
    if alt and alt != action:
        am = REGISTRY.get(alt)
        if am is not None and not am.detects:
            stealthier = {"action": alt, "label": am.label,
                          "why": f"{am.label} is silent (nothing in the suite catches it) — "
                                 f"same goal, no detectable frames"}
    return {
        "action": action, "label": m.label, "known": True,
        "seen_by": seen,
        "loud": loud,
        "footprint": ("loud — a WIDS/blue team would flag this" if loud
                      else "silent — little or nothing to detect by design"),
        "attack": list(m.attack_ids), "defend": list(m.defend), "ptes_phase": m.ptes_phase,
        "note": m.note,
        "stealthier": stealthier,
    }


def all_shadows() -> List[dict]:
    """Every action's footprint, loudest first (most to learn to avoid)."""
    out = [shadow(a) for a in actions()]
    out.sort(key=lambda s: (-len(s.get("seen_by", [])), s["action"]))
    return out
