"""Loot — the durable, authlog-linked store of recovered credentials, and the
scope-gated pivot that turns a cracked key into a network-layer foothold."""
from .vault import LootVault, LootEntry, default_loot_path
from .pivot import pivot, PivotResult
from .triage import triage, triage_file

__all__ = ["LootVault", "LootEntry", "default_loot_path", "pivot", "PivotResult",
           "triage", "triage_file"]
