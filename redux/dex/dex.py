"""Field Dex aggregation over the sightings store.

Pure read-side derivation — no writes, no fabrication. Rarity is computed from
*your own* data (how common a device's vendor OUI is across everything you've
seen), so it means "how often do I run into this kind of thing," not a made-up
score. "Departed" marks something known for a while that has gone quiet.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

_DAY = 86400.0


class Rarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"   # a one-off vendor OUI in your data


def _oui(mac: str) -> str:
    """First three octets (vendor prefix), lowercased. '' if not a MAC."""
    parts = (mac or "").lower().split(":")
    return ":".join(parts[:3]) if len(parts) >= 3 else ""


def _rarity(oui_count: int) -> Rarity:
    if oui_count >= 10:
        return Rarity.COMMON
    if oui_count >= 5:
        return Rarity.UNCOMMON
    if oui_count >= 3:
        return Rarity.RARE
    if oui_count == 2:
        return Rarity.EPIC
    return Rarity.LEGENDARY     # count == 1


@dataclass(frozen=True)
class DexEntry:
    kind: str
    mac: str
    ssid: str
    oui: str
    first_seen: float
    last_seen: float
    known_days: float
    located: bool
    channel: Optional[int]
    rarity: Rarity
    departed: bool
    reason: str                # one-line glass-box description

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["rarity"] = self.rarity.value
        return d


@dataclass(frozen=True)
class DexView:
    entries: List[DexEntry]
    summary: dict

    def rarest(self, limit: int = 10) -> List[DexEntry]:
        order = {r: i for i, r in enumerate(
            [Rarity.LEGENDARY, Rarity.EPIC, Rarity.RARE, Rarity.UNCOMMON, Rarity.COMMON])}
        return sorted(self.entries, key=lambda e: (order[e.rarity], -e.known_days))[:limit]

    def departed(self) -> List[DexEntry]:
        return [e for e in self.entries if e.departed]


def build_dex(store, *, now: Optional[float] = None, departed_after_days: float = 7.0) -> DexView:
    """Build the Dex from the sighting store. `now` defaults to the latest
    sighting time (so a replayed dataset reads consistently); departed = not seen
    for `departed_after_days` relative to that reference."""
    sightings = store.query() if hasattr(store, "query") else []
    ref = now
    if ref is None:
        ref = max((s.ts for s in sightings), default=time.time())

    oui_counts: Dict[str, int] = {}
    for s in sightings:
        o = _oui(s.mac)
        if o:
            oui_counts[o] = oui_counts.get(o, 0) + 1

    entries: List[DexEntry] = []
    for s in sightings:
        first = s.first_seen if s.first_seen is not None else s.ts
        last = s.ts
        o = _oui(s.mac)
        rarity = _rarity(oui_counts.get(o, 1)) if o else Rarity.LEGENDARY
        known_days = max(0.0, (last - first) / _DAY)
        located = s.lat is not None and s.lon is not None
        departed = (ref - last) > departed_after_days * _DAY
        label = s.ssid or s.mac or "(unknown)"
        if departed:
            reason = f"departed: {label}, known {known_days:.0f}d, last seen {(ref - last)/_DAY:.0f}d ago"
        else:
            reason = f"{label} — {rarity.value}, known {known_days:.0f}d" + (", located" if located else "")
        entries.append(DexEntry(
            kind=s.kind, mac=s.mac, ssid=s.ssid, oui=o,
            first_seen=first, last_seen=last, known_days=known_days,
            located=located, channel=s.channel, rarity=rarity,
            departed=departed, reason=reason,
        ))

    by_kind: Dict[str, int] = {}
    by_rarity: Dict[str, int] = {}
    for e in entries:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        by_rarity[e.rarity.value] = by_rarity.get(e.rarity.value, 0) + 1
    summary = {
        "total": len(entries),
        "by_kind": by_kind,
        "by_rarity": by_rarity,
        "located": sum(1 for e in entries if e.located),
        "departed": sum(1 for e in entries if e.departed),
        "unique_vendors": len([o for o in oui_counts if o]),
    }
    return DexView(entries=entries, summary=summary)
