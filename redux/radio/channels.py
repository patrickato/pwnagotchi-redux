"""All-band channel lists for passive sweeping.

pwnagotchi hops a handful of 2.4 GHz channels. The redux philosophy is to sweep
every channel across every band the radio can reach, so nothing is missed just
because it sits on 5 GHz or a non-1/6/11 channel. The live runtime already does
this (bettercap `wifi.recon` hops all supported channels with no restriction);
this module gives the CLI passive tools (`captap --hop`, `survey`) the same
full-band default instead of a 2.4-only 1/6/11 list.

Channel *numbers* are unambiguous for 2.4 and 5 GHz, so `iw dev set channel N`
works directly. 6 GHz reuses low numbers and needs an explicit frequency, so it
is out of this number-based default; add it via --channels/freqs when you have a
6E radio. Tuning is best-effort: a channel the radio or regdom won't accept is
simply skipped by the caller, so the full list is safe to offer everywhere.
"""
from __future__ import annotations

from typing import List, Sequence

# 2.4 GHz: 1–14 (ch 14 is Japan-only; harmless to attempt, skipped if disallowed).
BAND_24: List[int] = list(range(1, 15))

# 5 GHz: UNII-1 / UNII-2 / UNII-2e (DFS) / UNII-3. Passive listening on a DFS
# channel is fine; a radio/regdom that refuses one is skipped best-effort.
BAND_5: List[int] = [36, 40, 44, 48, 52, 56, 60, 64,
                     100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
                     149, 153, 157, 161, 165]


def all_band_channels(bands: Sequence[str] = ("2.4", "5")) -> List[int]:
    """Every channel across the requested bands, 2.4 first then 5 (low→high).
    Defaults to both bands — the full sweep."""
    out: List[int] = []
    if "2.4" in bands:
        out += BAND_24
    if "5" in bands:
        out += BAND_5
    return out


def parse_channels(spec: str) -> List[int]:
    """Parse a comma/space-separated channel override ('1,6,11,36 149') into a
    de-duplicated, order-preserving list of positive ints. Junk tokens are
    ignored so a stray comma can't crash the sweep."""
    seen = set()
    out: List[int] = []
    for tok in spec.replace(",", " ").split():
        try:
            n = int(tok)
        except ValueError:
            continue
        if n > 0 and n not in seen:
            seen.add(n)
            out.append(n)
    return out
