"""Target-value radar — what to hit first.

Recon turns up more APs than you can work; this ranks them by how worth attacking
each one is, so the operator (or the autopilot) spends effort on the juiciest
target instead of the first one seen. The score is glass-box — every point has a
reason — and composes what the box already knows:

  - crackability (#4 classify)  : is there an offline path at all, and which?
  - a PMKID already seen (#88)  : a free, crackable hash — the cheapest win
  - signal                      : a strong AP is reliable to capture from
  - active clients              : a client means a handshake is forceable
  - WPS enabled                 : a pixie-dust path
  - already owned (captured list): drops to zero — don't re-pwn what you have

A + B ⇒ C: A (everything seen) + B (what the box knows about each) ⇒ C (a ranked
work order). Pure/deterministic; feeds capture planning and autopilot priority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Set


def _norm(bssid: str) -> str:
    return (bssid or "").strip().lower()


@dataclass(frozen=True)
class TargetObservation:
    bssid: str
    ssid: str = ""
    security: str = "wpa2-psk"
    rssi: Optional[int] = None
    channel: Optional[int] = None
    clients: int = 0
    pmkid_seen: bool = False
    wps: bool = False


@dataclass(frozen=True)
class RankedTarget:
    bssid: str
    ssid: str
    score: int                 # 0..100
    crackable: bool
    method: str
    reasons: tuple
    already_owned: bool

    def to_dict(self) -> dict:
        return {"bssid": self.bssid, "ssid": self.ssid, "score": self.score,
                "crackable": self.crackable, "method": self.method,
                "reasons": list(self.reasons), "already_owned": self.already_owned}


def _signal_points(rssi: Optional[int]) -> int:
    if rssi is None:
        return 0
    if rssi >= -55:
        return 15
    if rssi >= -70:
        return 10
    if rssi >= -80:
        return 5
    return 2


def score_target(obs: TargetObservation, *, retired: Optional[Set[str]] = None,
                 classify: Optional[Callable] = None) -> RankedTarget:
    """Composite 0..100 value for one AP, with a glass-box reason per point."""
    retired = {_norm(b) for b in (retired or set())}
    bssid = _norm(obs.bssid)
    if classify is None:
        from ..classify.crackability import classify as classify
    assessment = classify({"encryption": obs.security, "security": obs.security,
                           "ssid": obs.ssid})
    crackable = bool(getattr(assessment, "offline_crackable", False))
    method = getattr(assessment, "method", "unknown")

    if bssid in retired:
        return RankedTarget(bssid=bssid, ssid=obs.ssid, score=0, crackable=crackable,
                            method=method, reasons=("already owned — skip",),
                            already_owned=True)
    if not crackable:
        return RankedTarget(bssid=bssid, ssid=obs.ssid, score=0, crackable=False,
                            method=method,
                            reasons=(f"no offline path ({getattr(assessment, 'reason', method)})",),
                            already_owned=False)

    score = 30
    reasons = ["offline-crackable (+30)"]
    if obs.pmkid_seen:
        score += 25
        reasons.append("PMKID already seen — free hash (+25)")
    sp = _signal_points(obs.rssi)
    if sp:
        score += sp
        reasons.append(f"signal {obs.rssi}dBm (+{sp})")
    if obs.clients > 0:
        c = min(15, 5 + 2 * obs.clients)
        score += c
        reasons.append(f"{obs.clients} client(s) — handshake forceable (+{c})")
    if obs.wps:
        score += 10
        reasons.append("WPS enabled — pixie path (+10)")
    if obs.ssid:
        score += 5
        reasons.append("SSID known — salt for the crack (+5)")
    return RankedTarget(bssid=bssid, ssid=obs.ssid, score=min(100, score),
                        crackable=True, method=method, reasons=tuple(reasons),
                        already_owned=False)


def rank(observations: Sequence[TargetObservation], *, retired: Optional[Set[str]] = None,
         classify: Optional[Callable] = None) -> List[RankedTarget]:
    """Every target scored, juiciest first (owned/uncrackable sink to the bottom)."""
    out = [score_target(o, retired=retired, classify=classify) for o in observations or ()]
    out.sort(key=lambda t: (-t.score, t.bssid))
    return out


def summary(ranked: Sequence[RankedTarget]) -> dict:
    workable = [t for t in ranked if t.score > 0]
    top = workable[0] if workable else None
    return {"total": len(ranked), "workable": len(workable),
            "owned": sum(1 for t in ranked if t.already_owned),
            "top": top.to_dict() if top else None,
            "note": (f"{len(workable)} workable target(s); hit {top.ssid or top.bssid} first "
                     f"(score {top.score})" if top else "nothing workable in range")}
