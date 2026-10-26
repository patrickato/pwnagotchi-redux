"""Quiet/Ghost mode — go dark. The stealth posture for LAB.

Being unnoticed is its own discipline, so Augur makes it a first-class, one-toggle
posture rather than a scatter of flags. When Quiet mode is on:

  - capture prefers the ZERO-TRANSMIT methods (PMKID, parked tcpdump) over anything
    that emits (deauth, active AngryOxide) — you grab what you can without saying a
    word on the air;
  - a frame budget caps how much you'll ever emit (0 = fully passive); any transmit
    beyond it needs an explicit, per-action confirm (this stacks on top of the
    power-tier rule — Quiet never loosens a gate, only tightens);
  - the device's own MAC is rotated so it doesn't leave a stable fingerprint;
  - a live footprint score tells you how detectable you are right now.

It's a LAB posture (you choose to be stealthy on your own gear) and it NEVER wipes
the brain — the experience/graph/loot that make Augur smarter persist on /captures
exactly as before; Quiet mode is about emissions, not memory.

Pure/testable: config is plain data with fail-safe load/save, and the scoring +
gate are injected-clock functions.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# transmit cost per capture method — how many frames / how loud it is on the air.
# 0 = emits nothing (pure listen). Quiet mode prefers the cheapest.
_METHOD_TX_COST: Dict[str, int] = {
    "pmkid": 0,            # clientless PMKID — request-less capture, silent
    "tcpdump-lock": 0,     # parked monitor on a locked channel — listen only
    "angryoxide": 2,       # active scalpel can deauth
    "deauth": 3,           # forces a 4-way by kicking a client — loudest
}

# how loud each emitted event is, for the footprint score
_EVENT_WEIGHT: Dict[str, int] = {
    "deauth": 5, "disassoc": 5, "assoc": 2, "auth": 2,
    "probe": 1, "beacon": 3, "eapol": 1, "karma": 4,
}


@dataclass
class QuietConfig:
    enabled: bool = False
    randomize_mac: bool = True
    prefer_passive: bool = True
    frame_budget: int = 0            # max frames to emit while quiet (0 = fully passive)
    require_confirm: bool = True     # any transmit past the budget needs explicit confirm

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path) -> "QuietConfig":
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        # fail closed on garbage: only real booleans/ints are honoured
        return cls(
            enabled=data.get("enabled", False) is True,
            randomize_mac=data.get("randomize_mac", True) is True,
            prefer_passive=data.get("prefer_passive", True) is True,
            frame_budget=int(data["frame_budget"]) if isinstance(data.get("frame_budget"), int) else 0,
            require_confirm=data.get("require_confirm", True) is not False,
        )

    def save(self, path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))


def quiet_method_order(available: Sequence[str]) -> List[str]:
    """Order the available capture methods stealthiest-first (cheapest transmit
    cost first), so Quiet mode reaches for a silent method before a loud one."""
    return sorted(available, key=lambda m: (_METHOD_TX_COST.get(m, 1), m))


def footprint_score(events: Sequence[dict]) -> dict:
    """A 0–100 detectability score from recently emitted events. 0 = silent (pure
    listen); higher = louder / easier for a WIDS to flag. Each event is
    {"kind": <k>, "frames": <n>}."""
    total = 0
    for e in events:
        w = _EVENT_WEIGHT.get((e.get("kind") or "").lower(), 1)
        total += w * int(e.get("frames", 1) or 1)
    score = min(100, total)
    if total == 0:
        label = "silent"
    elif score < 15:
        label = "quiet"
    elif score < 50:
        label = "moderate"
    else:
        label = "loud"
    return {"score": score, "label": label, "emitted": total,
            "reason": f"{total} weighted frame(s) emitted → {label}"}


def transmit_allowed(quiet: QuietConfig, *, confirmed: bool = False,
                     frames_so_far: int = 0) -> Tuple[bool, str]:
    """Whether a transmit is permitted under the current Quiet posture. Quiet never
    loosens anything — with it off this just says yes; with it on it enforces the
    frame budget + per-action confirm on top of the existing scope/power gates."""
    if not quiet.enabled:
        return True, "quiet mode off — existing gates apply"
    if frames_so_far < quiet.frame_budget:
        return True, f"within frame budget ({frames_so_far}/{quiet.frame_budget})"
    if quiet.require_confirm and confirmed:
        return True, "over budget but explicitly confirmed"
    return False, (f"quiet mode: frame budget {quiet.frame_budget} reached — "
                   f"transmit needs explicit confirm" if quiet.require_confirm
                   else f"quiet mode: frame budget {quiet.frame_budget} reached")


def status(quiet: QuietConfig, *, recent_events: Optional[Sequence[dict]] = None) -> dict:
    """The stealth block for the shared status model (TFT/web/phone)."""
    fp = footprint_score(recent_events or [])
    return {
        "quiet": quiet.enabled,
        "prefer_passive": quiet.prefer_passive,
        "frame_budget": quiet.frame_budget,
        "randomize_mac": quiet.randomize_mac,
        "footprint": fp,
    }
