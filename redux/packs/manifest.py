"""Pack manifest — the unit of opt-in capability (task 2.4).

A pack is how redux stays lean: the base image ships small, and everything else —
a plugin, a tool bundle (e.g. the opt-in Kali-tools pack), a detector/geo suite —
is a pack you choose to install. A pack declares what it is, what it provides, and
what it depends on, so the manager can resolve a safe install order.

Manifests are plain data (TOML `pack.toml` or JSON `pack.json`) so a pack is
inspectable before anything runs. Nothing here executes a pack — it only describes.
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple


_KINDS = ("plugin", "tool", "suite")


@dataclass(frozen=True)
class Pack:
    name: str
    version: str = "0.0.0"
    description: str = ""
    kind: str = "plugin"                 # plugin | tool | suite
    provides: Tuple[str, ...] = ()       # capability names this pack offers
    requires: Tuple[str, ...] = ()       # pack names this pack depends on
    module: str = ""                     # import path for a plugin pack
    apt: Tuple[str, ...] = ()            # apt packages for a tool pack (installed by the image side)
    root: str = ""                       # where the pack lives on disk (set by discovery)

    def __post_init__(self):
        if not self.name:
            raise ValueError("pack manifest needs a name")
        if self.kind not in _KINDS:
            raise ValueError(f"pack '{self.name}': kind must be one of {_KINDS}, got {self.kind!r}")


def from_dict(data: dict, root: str = "") -> Pack:
    return Pack(
        name=str(data.get("name", "")),
        version=str(data.get("version", "0.0.0")),
        description=str(data.get("description", "")),
        kind=str(data.get("kind", "plugin")),
        provides=tuple(data.get("provides", ()) or ()),
        requires=tuple(data.get("requires", ()) or ()),
        module=str(data.get("module", "")),
        apt=tuple(data.get("apt", ()) or ()),
        root=root,
    )


def load_manifest(path) -> Pack:
    """Load a single pack manifest file (.toml or .json)."""
    p = Path(path)
    raw = p.read_bytes()
    if p.suffix == ".toml":
        data = tomllib.loads(raw.decode("utf-8"))
    elif p.suffix == ".json":
        data = json.loads(raw.decode("utf-8"))
    else:
        raise ValueError(f"unknown manifest type: {p.name} (want pack.toml or pack.json)")
    # a [pack] table is allowed in TOML; accept either top-level or nested
    if "pack" in data and isinstance(data["pack"], dict):
        data = data["pack"]
    return from_dict(data, root=str(p.parent))


#: manifest filenames discovery looks for, in order
MANIFEST_NAMES = ("pack.toml", "pack.json")
