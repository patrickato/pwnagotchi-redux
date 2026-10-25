"""Adaptive unleashed — let the autopilot learn what works in YOUR cage.

Plain unleashed hits targets in whatever order they arrive. This reorders each
cycle's targets by what the box has actually learned: the experience store
records capture successes by dimension (channel/band/hour/vendor/kind), and
`rank()` scores each with an exploration-aware UCB bonus. We rank by **vendor**
(via OUI) — the dimension that maps cleanly onto a target BSSID — so the
autopilot tries the vendors that have paid off first, while the UCB bonus keeps
an untried vendor from being starved (explore, don't greedily lock in).

Pure: the experience rows and the vendor lookup are injected, so the ordering
is deterministic and testable; the CLI wires the live ExperienceStore + OUI.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence


def by_vendor_success(rows: Sequence, vendor_lookup: Callable[[str], Optional[str]],
                      *, dimension: str = "vendor") -> Callable[[Sequence[str]], List[str]]:
    """Return a prioritiser(targets)->ordered that sorts BSSIDs by their vendor's
    learned capture score (highest first; ties keep the original order)."""
    from ..core.experience import rank

    def order(targets: Sequence[str]) -> List[str]:
        tlist = list(targets)
        vmap = {t: (vendor_lookup(t) or "?") for t in tlist}
        stats = {s.value: s.score for s in rank(list(rows), dimension,
                                                candidates=sorted(set(vmap.values())))}
        # stable sort by -score keeps input order within equal scores
        return sorted(tlist, key=lambda t: -stats.get(vmap[t], 0.0))

    return order
