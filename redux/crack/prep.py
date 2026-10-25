"""Crack-prep — one pass that turns a pile of captures into a work order.

Chains the three crack-side tools we built so they stop being standalone:

  preflight  — is each capture actually crackable, and how strong?
  triage     — rank the crackable ones by ROI (SSID-name prior)
  forge      — build the targeted wordlist for each from its SSID

"I have 40 handshakes" becomes "crack these 6 first, here's the wordlist for
each." Pure/deterministic: the CLI does the file I/O and hands the already-
computed pieces in, so the composition is testable without hardware or hashcat.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

from .triage import CrackTarget


@dataclass(frozen=True)
class PrepItem:
    ssid: str
    bssid: str
    artifacts: Tuple[str, ...]
    tier: str
    prob: float
    strong: bool                     # at least one authenticated handshake / valid PMKID
    wordlist_hint: str
    forged_words: Tuple[str, ...]

    @property
    def summary(self) -> str:
        q = "strong" if self.strong else "weak"
        return (f"[{self.tier:6}] p≈{self.prob:.2f} {q:6} {self.ssid or '(hidden)'} {self.bssid} "
                f"{{{','.join(self.artifacts)}}} — {len(self.forged_words)} forged candidate(s)")


def crack_prep(triaged: Sequence[CrackTarget], strong_bssids: Set[str], *, forge_fn,
               limit: Optional[int] = None, tokens: Sequence[str] = ()) -> List[PrepItem]:
    """Compose a ranked work order. `triaged` is triage_crack_queue() output (already
    ROI-sorted); `strong_bssids` are the BSSIDs preflight judged strong; `forge_fn`
    is redux.crack.forge.forge (injectable for tests)."""
    chosen = triaged[:limit] if limit else list(triaged)
    out: List[PrepItem] = []
    for t in chosen:
        words = tuple(forge_fn(t.ssid, tokens=list(tokens)).words) if t.ssid else ()
        out.append(PrepItem(
            ssid=t.ssid, bssid=t.bssid, artifacts=t.artifacts, tier=t.tier, prob=t.prob,
            strong=t.bssid in strong_bssids, wordlist_hint=t.wordlist_hint, forged_words=words))
    return out
