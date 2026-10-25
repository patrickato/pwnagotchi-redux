"""Signal Dex — one browsable catalog of everything the device has seen or held.

WiFi / BLE / ISM / ADS-B / pager / ACARS live in the sighting store (and the
Field Dex already enriches them with vendor + rarity). Captured **sub-GHz**
signals live in the SubGhz library; learned **IR** codes live in the IR library.
This read-side aggregator folds all of them into one list with a common shape,
so "everything I've encountered, across every radio and medium" is a single
view instead of six separate places — exactly the kind of at-a-glance catalog
that helps while learning.

Pure derivation, no fabrication: it reads the real stores/libraries you pass in
(all injectable), normalizes each into a `SignalEntry`, and never invents a row.
Any source you don't pass is simply absent from the view.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .dex import build_dex

# the mediums a signal can belong to, ordered for display
DOMAINS = ("wifi", "ble", "ism", "adsb", "pocsag", "acars", "subghz", "ir")


@dataclass(frozen=True)
class SignalEntry:
    domain: str                 # one of DOMAINS (or a sighting kind)
    identifier: str             # mac / signal id / code name
    label: str                  # human label (ssid, vendor, name)
    detail: str                 # one-line glass-box description
    source: str                 # "sightings" | "subghz" | "ir"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class SignalDexView:
    entries: List[SignalEntry]
    summary: dict

    def by_domain(self, domain: str) -> List[SignalEntry]:
        return [e for e in self.entries if e.domain == domain]


def _from_sightings(store) -> List[SignalEntry]:
    dex = build_dex(store)
    out = []
    for e in dex.entries:
        # e is a DexEntry (kind, mac, ssid, vendor, randomized, rarity, reason, ...)
        who = getattr(e, "vendor", None) or (e.ssid or e.mac)
        bits = [e.rarity.value if hasattr(e.rarity, "value") else str(e.rarity)]
        if getattr(e, "vendor", None):
            bits.append(e.vendor)
        if getattr(e, "randomized", False):
            bits.append("randomized MAC")
        out.append(SignalEntry(domain=e.kind, identifier=e.mac,
                               label=e.ssid or who or e.mac,
                               detail=", ".join(b for b in bits if b), source="sightings"))
    return out


def _from_subghz(lib) -> List[SignalEntry]:
    out = []
    for s in lib.list():
        detail = f"{s.freq_hz/1e6:g} MHz, {s.protocol or s.modulation}"
        if s.rolling:
            detail += " [rolling — won't replay]"
        out.append(SignalEntry("subghz", s.id, s.name, detail, "subghz"))
    return out


def _from_ir(lib) -> List[SignalEntry]:
    out = []
    for c in lib.list():
        out.append(SignalEntry("ir", c.name, c.name,
                               c.protocol or c.kind, "ir"))
    return out


def build_signal_dex(*, sighting_store=None, subghz_lib=None, ir_lib=None) -> SignalDexView:
    """Aggregate every available source into one catalog. Each source is
    optional; omit it and it's simply not in the view."""
    entries: List[SignalEntry] = []
    if sighting_store is not None:
        entries += _from_sightings(sighting_store)
    if subghz_lib is not None:
        entries += _from_subghz(subghz_lib)
    if ir_lib is not None:
        entries += _from_ir(ir_lib)

    by_domain: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    for e in entries:
        by_domain[e.domain] = by_domain.get(e.domain, 0) + 1
        by_source[e.source] = by_source.get(e.source, 0) + 1
    # stable display order: known domains first, then any extras
    order = {d: i for i, d in enumerate(DOMAINS)}
    entries.sort(key=lambda e: (order.get(e.domain, 99), e.label.lower()))
    summary = {
        "total": len(entries),
        "by_domain": dict(sorted(by_domain.items(),
                                 key=lambda kv: order.get(kv[0], 99))),
        "by_source": by_source,
    }
    return SignalDexView(entries=entries, summary=summary)
