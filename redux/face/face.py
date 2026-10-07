"""Augur's face — the creature expression, driven only by real state.

Augur is the glass-box familiar: it reads the invisible RF world and tells you
*true*. Its face is not decoration — every expression maps to a real machine
state, so a glance tells you what the device is actually doing and why. That is
the whole house style:

  - motion only ever *marks a real event* (a new face seen, a capture cached, a
    detector firing) — never idle animation for its own sake;
  - the on-device TFT draws a *static* face and redraws only when the state
    changes (no animation loop = no SPI churn = no heat/battery cost);
  - the richer web face (client-rendered, free for the Pi) may blink/tilt, but
    only on the same real transitions.

Pure and deterministic: `face_for(status)` → `AugurFace`. No I/O, no randomness,
no time. Same snapshot in, same face out — so it is trivially testable and can
never surprise you on-device.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class AugurState(str, Enum):
    WATCH = "watch"     # idle / quiet — the patient watch (default)
    READ = "read"       # re-deciding radios / assessing a network (thinking)
    HUNT = "hunt"       # arranged to capture (hunting)
    MARK = "mark"       # a genuinely new device/face just seen (transient)
    CACHE = "cache"     # a capture just landed and was stored (transient)
    RUFFLE = "ruffle"   # a detector fired — feathers up (alert)
    BLIND = "blind"     # honest: no way to perceive (no capture engine, no CSI)
    ROOST = "roost"     # settled with the murder (mesh peers present)


# Eyes. The ‹ › beak-frame is Augur's signature; the pair between is the
# expression. Two glyph sets: unicode (nice on the panel font / web) and a pure
# ASCII fallback for mono panels and ascii-only rendering.
_EYES_U: Dict[AugurState, str] = {
    AugurState.WATCH:  "‹·_·›",
    AugurState.READ:   "‹o_·›",
    AugurState.HUNT:   "‹►_◄›",
    AugurState.MARK:   "‹!_·›",
    AugurState.CACHE:  "‹^_^›",
    AugurState.RUFFLE: "‹✺_✺›",
    AugurState.BLIND:  "‹-_-›",
    AugurState.ROOST:  "‹u_u›",
}
_EYES_A: Dict[AugurState, str] = {
    AugurState.WATCH:  "<._.>",
    AugurState.READ:   "<o_.>",
    AugurState.HUNT:   "<=_=>",
    AugurState.MARK:   "<!_.>",
    AugurState.CACHE:  "<^_^>",
    AugurState.RUFFLE: "<*_*>",
    AugurState.BLIND:  "<-_->",
    AugurState.ROOST:  "<u_u>",
}

_REASON: Dict[AugurState, str] = {
    AugurState.WATCH:  "watching — quiet",
    AugurState.READ:   "reading the air",
    AugurState.HUNT:   "on the hunt",
    AugurState.MARK:   "new face",
    AugurState.CACHE:  "cached it",
    AugurState.RUFFLE: "something's off",
    AugurState.BLIND:  "blind here — no monitor radio",
    AugurState.ROOST:  "roosting with the murder",
}


def eyes(state: AugurState, ascii: bool = False) -> str:
    return (_EYES_A if ascii else _EYES_U)[state]


@dataclass(frozen=True)
class AugurFace:
    state: AugurState
    reason: str   # the honest, human-readable reason this face is showing

    def eyes(self, ascii: bool = False) -> str:
        return (_EYES_A if ascii else _EYES_U)[self.state]


def face_for(status: Dict) -> AugurFace:
    """Map a glass-box `status()` snapshot to a face + an honest reason.

    Precedence follows what a human most needs at a glance:
      1. a live *critical* alert (ruffle) — the thing to look at now;
      2. honest blindness (no capture engine AND no CSI sense) — it says so
         rather than pretending to watch;
      3. a producer-set transient hint (a fresh capture / a newly seen device);
      4. what it's actively doing (hunting / thinking / alerting);
      5. settled with mesh peers (roost);
      6. otherwise the patient watch.

    Everything is read from fields `status()` already exposes — nothing invented.
    """
    sent = status.get("sentinel") or {}
    last = sent.get("last") or {}
    if last.get("severity") == "critical":
        return AugurFace(AugurState.RUFFLE, str(last.get("summary") or _REASON[AugurState.RUFFLE]))

    engine = status.get("capture_engine") or "none"
    sense_ok = bool((status.get("sense") or {}).get("available"))
    if engine in ("none", "") and not sense_ok:
        return AugurFace(AugurState.BLIND, _REASON[AugurState.BLIND])

    hint = status.get("face_hint")
    if hint in (AugurState.CACHE.value, "cache"):
        return AugurFace(AugurState.CACHE, _REASON[AugurState.CACHE])
    if hint in (AugurState.MARK.value, "mark"):
        return AugurFace(AugurState.MARK, _REASON[AugurState.MARK])

    mood = (status.get("mood") or "").lower()
    if mood == "alert":
        return AugurFace(AugurState.RUFFLE, _REASON[AugurState.RUFFLE])
    if mood == "hunting":
        return AugurFace(AugurState.HUNT, _REASON[AugurState.HUNT])
    if mood == "thinking":
        return AugurFace(AugurState.READ, _REASON[AugurState.READ])

    if (status.get("mesh") or {}).get("peers"):
        return AugurFace(AugurState.ROOST, _REASON[AugurState.ROOST])

    return AugurFace(AugurState.WATCH, _REASON[AugurState.WATCH])
