"""Field Dex — your recon ledger, now with device identity.

A view over the sightings store: every network and device you've encountered,
with first/last-seen, how long you've known it, whether it's located, a rarity
tier derived from how common its vendor OUI is in your own data, and a
"departed" flag for long-known things that have stopped appearing.

The fingerprint layer (`fingerprint.py`) adds *device identity that survives MAC
randomization*: observations sharing a MAC-independent signature (PNL / IE
template) collapse into one device even across different MACs — "devices I've
seen," not just "MACs I've seen." It invents nothing and never links two devices
off a MAC-derived signal alone.
"""
from .dex import DexEntry, DexView, Rarity, build_dex
from .fingerprint import (
    DeviceObservation, Fingerprint, DeviceIdentity, DeviceLinker,
    fingerprint, is_randomized, observations_from_store, link_store,
)

__all__ = [
    "DexEntry", "DexView", "Rarity", "build_dex",
    "DeviceObservation", "Fingerprint", "DeviceIdentity", "DeviceLinker",
    "fingerprint", "is_randomized", "observations_from_store", "link_store",
]
