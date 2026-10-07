"""Field Dex — your recon ledger.

A view over the sightings store: every network and device you've encountered,
with first/last-seen, how long you've known it, whether it's located, a rarity
tier derived from how common its vendor OUI is in your own data, and a
"departed" flag for long-known things that have stopped appearing. It invents
nothing — it only aggregates what was actually recorded.
"""
from .dex import DexEntry, DexView, Rarity, build_dex

__all__ = ["DexEntry", "DexView", "Rarity", "build_dex"]
