"""Device re-identification across randomized MACs (defeat MAC randomization).

Modern phones rotate locally-administered (random) MACs to dodge tracking — but
they still probe for their *saved* networks, and that preferred-network list
(PNL) belongs to the physical device, not the MAC. So two randomized MACs that
probe for the same distinctive SSID set are almost certainly the same device.

This fuses the per-MAC PNLs the ProbeHarvester already records into device
"entities": MACs are linked when they share SSIDs, weighted by how *rare* each
shared SSID is across all seen MACs (a shared `xfinitywifi` is weak evidence; a
shared `Bob-iPhone-Hotspot` is strong). Ubiquitous SSIDs are excluded entirely
so they never create false links. Union-find groups the linked MACs.

Pure/deterministic. Glass-box: every entity carries the exact SSIDs that linked
it and a confidence from the rarity of that evidence. Nothing is fabricated — a
MAC with no distinctive shared SSID stays its own identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .mac_normalize import normalize_mac, is_valid_mac, is_locally_administered


@dataclass(frozen=True)
class Entity:
    macs: Tuple[str, ...]          # the MACs believed to be one physical device
    randomized: int               # how many of them are locally-administered (random)
    shared_ssids: Tuple[str, ...]  # the PNL evidence that linked them
    probes: int                   # total probe requests across the group
    confidence: str               # strong | medium | weak (from evidence rarity)


def reidentify(clients: Sequence[dict], *, min_link_weight: float = 0.5,
               max_common_fraction: float = 0.5) -> List[Entity]:
    """Group MACs into physical-device entities from their probe PNLs.

    `clients`: the ProbeHarvester.clients() shape — dicts with "client" (MAC),
    "ssids" (list), and "probes" (count). Two MACs link when the rarity-weighted
    sum over their shared, non-ubiquitous SSIDs meets `min_link_weight`.
    """
    pnl: Dict[str, set] = {}
    probes: Dict[str, int] = {}
    for c in clients:
        mac = normalize_mac(str(c.get("client", "")))
        if not is_valid_mac(mac):
            continue
        ssids = {s for s in c.get("ssids", []) if isinstance(s, str) and s.strip()}
        if not ssids:
            continue
        pnl[mac] = ssids
        try:
            probes[mac] = int(c.get("probes", 0) or 0)
        except (TypeError, ValueError):
            probes[mac] = 0
    macs = sorted(pnl)
    n = len(macs)
    if n < 2:
        return []

    ssid_df: Dict[str, int] = {}           # how many MACs probe each SSID
    for ssids in pnl.values():
        for s in ssids:
            ssid_df[s] = ssid_df.get(s, 0) + 1
    common_cap = max(3, int(max_common_fraction * n))   # SSIDs more common than this are noise

    def selective(s: str) -> bool:
        return ssid_df.get(s, 0) <= common_cap

    parent = {m: m for m in macs}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    pair_evidence: Dict[frozenset, List[str]] = {}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = macs[i], macs[j]
            shared = [s for s in (pnl[a] & pnl[b]) if selective(s)]
            if not shared:
                continue
            weight = sum(1.0 / ssid_df[s] for s in shared)
            if weight >= min_link_weight:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
                pair_evidence[frozenset((a, b))] = shared

    groups: Dict[str, set] = {}
    for m in macs:
        groups.setdefault(find(m), set()).add(m)

    entities: List[Entity] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        evidence: set = set()
        for pair, shared in pair_evidence.items():
            if pair <= members:
                evidence.update(shared)
        member_list = tuple(sorted(members))
        randomized = sum(1 for m in member_list if is_locally_administered(m))
        min_df = min((ssid_df[s] for s in evidence), default=n)
        confidence = ("strong" if min_df <= 2 else
                      "medium" if min_df <= max(3, common_cap // 2) else "weak")
        entities.append(Entity(
            macs=member_list, randomized=randomized,
            shared_ssids=tuple(sorted(evidence)),
            probes=sum(probes[m] for m in member_list), confidence=confidence))
    entities.sort(key=lambda e: (-len(e.macs), -e.probes))
    return entities


# --- device dossier: reid entity × sightings × OUI (feed fusion) ------------ #

@dataclass(frozen=True)
class Dossier:
    """A re-identified device enriched with where/when it was seen and (where
    knowable) its vendor. The join is on the client MAC, which the daemon records
    in BOTH the sightings store (client.new) and the probe PNL (client.probe)."""
    macs: Tuple[str, ...]
    randomized: int
    confidence: str
    shared_ssids: Tuple[str, ...]
    vendor: Optional[str]            # None for a fully-randomized device (OUI is meaningless)
    sightings: int
    last_seen: Optional[float]
    strongest_rssi: Optional[int]
    places: Tuple[str, ...]          # geohash cells where it was seen (empty if no GPS fix)
    summary: str


def enrich_entities(entities: Sequence[Entity], sightings_by_mac: Dict[str, list], *,
                    vendor_lookup=None, precision: int = 7) -> List[Dossier]:
    """Turn reid entities into dossiers using sightings indexed by lower-case MAC.
    `vendor_lookup(mac)->Optional[str]` is applied only to non-randomized MACs (a
    randomized MAC's OUI is noise). Pure: pass rows in, get dossiers out."""
    from .geohash import encode as _gh
    out: List[Dossier] = []
    for e in entities:
        sights = []
        for m in e.macs:
            sights.extend(sightings_by_mac.get(m.lower(), []))
        tss = [s.ts for s in sights if getattr(s, "ts", None) is not None]
        rssis = [s.rssi for s in sights if getattr(s, "rssi", None) is not None]
        places = set()
        for s in sights:
            lat, lon = getattr(s, "lat", None), getattr(s, "lon", None)
            if lat is not None and lon is not None:
                try:
                    places.add(_gh(float(lat), float(lon), precision))
                except (TypeError, ValueError):
                    pass
        vendor = None
        if vendor_lookup:
            for m in e.macs:
                if not is_locally_administered(m):
                    v = vendor_lookup(m)
                    if v:
                        vendor = v
                        break
        last_seen = max(tss) if tss else None
        strongest = max(rssis) if rssis else None
        bits = [f"{len(e.macs)} MACs", vendor or ("randomized — vendor unknown" if e.randomized == len(e.macs) else "vendor unknown")]
        if sights:
            bits.append(f"seen {len(sights)}×")
            if strongest is not None:
                bits.append(f"strongest {strongest} dBm")
            if places:
                bits.append(f"{len(places)} place(s)")
        else:
            bits.append("not yet in the sightings store (probe-only)")
        out.append(Dossier(
            macs=e.macs, randomized=e.randomized, confidence=e.confidence,
            shared_ssids=e.shared_ssids, vendor=vendor, sightings=len(sights),
            last_seen=last_seen, strongest_rssi=strongest,
            places=tuple(sorted(places)), summary=" · ".join(bits)))
    return out
