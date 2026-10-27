"""Adaptive capture-method selection — learn which capture works, per AP.

redux now has several ways to catch a handshake: AngryOxide's active scalpel, a
deauth-forced 4-way, a clientless PMKID grab, or a parked tcpdump on a locked
channel. Which works best isn't fixed — some APs cough up a PMKID instantly (quiet,
no deauth), some only yield to a deauth. This picks the method from the device's
own recorded outcomes (the experience layer), using its UCB explore/exploit rank so
an under-tried method is never starved. Glass-box: every pick carries why.

Record attempts with `context={"method": <m>, "band": <b>, "bssid": <ap>}` and the
recommender ranks over the methods actually available right now. Pure — all the
math is the experience layer's; this just filters and chooses.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ..core.experience import Experience, rank

# the capture methods redux can drive, in default-preference order (used only as a
# tiebreak / cold-start before there's any history)
CAPTURE_METHODS: Tuple[str, ...] = ("angryoxide", "pmkid", "deauth", "tcpdump-lock")


def _capture_rows(rows: Sequence[Experience], *, band=None, bssid=None) -> List[Experience]:
    """Experience rows that are capture attempts (tagged with a method), optionally
    narrowed to one band or one AP so the choice can adapt per target."""
    out: List[Experience] = []
    for r in rows:
        ctx = r.context or {}
        if not ctx.get("method"):
            continue
        if band is not None and ctx.get("band") != band:
            continue
        if bssid is not None and ctx.get("bssid") != bssid:
            continue
        out.append(r)
    return out


def recommend_capture_method(rows: Sequence[Experience], *, available: Sequence[str],
                             band=None, bssid=None) -> Tuple[Optional[str], str]:
    """Pick the capture method to try next from history + what's available now.

    Returns (method, reason). With no relevant history it uses the first available
    preferred method (honest cold start); otherwise the UCB-ranked best available —
    which naturally explores an untried method before locking onto one.
    """
    avail = [m for m in CAPTURE_METHODS if m in available] + \
            [m for m in available if m not in CAPTURE_METHODS]
    if not avail:
        return None, "no capture method available (no engine/radio)"
    hist = _capture_rows(rows, band=band, bssid=bssid)
    if not hist:
        where = " for this AP" if bssid else (f" on {band}" if band else "")
        return avail[0], f"no capture history{where} yet — starting with {avail[0]} (preferred)"
    stats = rank(hist, "method", candidates=avail)
    best = stats[0]
    scope = f" for {bssid}" if bssid else (f" on {band}" if band else "")
    return best.value, f"adaptive pick{scope}: {best.reason}"


def method_table(rows: Sequence[Experience], *, available: Sequence[str],
                 band=None, bssid=None) -> List[dict]:
    """The full ranked table (for surfacing in status / the UI): each method with
    successes/trials/score and its reason, best first."""
    avail = [m for m in CAPTURE_METHODS if m in available] + \
            [m for m in available if m not in CAPTURE_METHODS]
    hist = _capture_rows(rows, band=band, bssid=bssid)
    return [{"method": s.value, "successes": s.successes, "trials": s.trials,
             "rate": s.rate, "score": s.score, "reason": s.reason}
            for s in rank(hist, "method", candidates=avail)]
