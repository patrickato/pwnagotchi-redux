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
import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple
from .control import NAME, read_data, validate_options


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

    manifest_sha256: str = ""
    conflicts: Tuple[str, ...] = ()
    options: dict = field(default_factory=dict)
    adapter: str = "redux"
    entrypoint: str = ""
    sha256: str = ""
    side_effects: bool = False
    requirements: Tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.name, str) or not NAME.fullmatch(self.name):
            raise ValueError("pack manifest needs a name")
        if self.kind not in _KINDS:
            raise ValueError(f"pack '{self.name}': kind must be one of {_KINDS}, got {self.kind!r}")


def from_dict(data: dict, root: str = "", manifest_sha256: str = "") -> Pack:
    if not isinstance(data, dict):
        raise ValueError("manifest must be an object")
    for key in ('requires', 'conflicts', 'provides', 'apt', 'requirements'):
        values = data.get(key, [])
        if not isinstance(values, list) or len(values) > 128 or any(not isinstance(v, str) or not NAME.fullmatch(v) for v in values):
            raise ValueError("invalid manifest relationship")
    for key in ('name', 'version', 'description', 'kind', 'module', 'adapter', 'entrypoint', 'sha256'):
        if key in data and (not isinstance(data[key], str) or len(data[key]) > 2048 or any(ord(c) < 32 for c in data[key])):
            raise ValueError("invalid manifest text")
    options = data.get('options', {})
    if not isinstance(options, dict) or len(options) > 128:
        raise ValueError("invalid options schema")
    for key, spec in options.items():
        if not NAME.fullmatch(key) or not isinstance(spec, dict) or spec.get('type') not in ('boolean', 'string', 'number', 'enum', 'path'):
            raise ValueError("invalid option schema")
        if type(spec.get('sensitive', False)) is not bool or type(spec.get('required', False)) is not bool:
            raise ValueError("invalid option flags")
        if spec.get('type') == 'number':
            import math
            bounds = (spec.get('min', -1e12), spec.get('max', 1e12))
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds) or bounds[0] > bounds[1]:
                raise ValueError("invalid numeric bounds")
        if spec.get('type') == 'enum':
            choices = spec.get('choices')
            if not isinstance(choices, list) or not 0 < len(choices) <= 128 or any(not isinstance(v, str) or len(v) > 1024 for v in choices):
                raise ValueError("invalid enum choices")
        if spec.get('type') == 'path'  and spec.get('root', '/captures/plugins') != '/captures/plugins':
            raise ValueError("path options must remain under /captures/plugins")
        if not isinstance(spec.get('max_length', 1024), int) or not 0 < spec.get('max_length', 1024) <= 4096:
            raise ValueError("invalid string bound")
        if 'default' in spec:
            validate_options({key: spec}, {key: spec['default']})
    if type(data.get('side_effects', False)) is not bool:
        raise ValueError("invalid side effects declaration")
    return Pack(
        manifest_sha256=manifest_sha256,
        conflicts=tuple(data.get('conflicts', [])), options=options,
        adapter=data.get('adapter', 'redux'), entrypoint=data.get('entrypoint', ''),
        sha256=data.get('sha256', ''), side_effects=data.get('side_effects', False),
        requirements=tuple(data.get('requirements', [])),
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
    raw = read_data(p)
    if p.suffix == ".toml":
        data = tomllib.loads(raw.decode("utf-8"))
    elif p.suffix == ".json":
        data = json.loads(raw.decode("utf-8"))
    else:
        raise ValueError(f"unknown manifest type: {p.name} (want pack.toml or pack.json)")
    # a [pack] table is allowed in TOML; accept either top-level or nested
    if "pack" in data and isinstance(data["pack"], dict):
        data = data["pack"]
    return from_dict(data, root=str(p.parent), manifest_sha256=hashlib.sha256(raw).hexdigest())


#: manifest filenames discovery looks for, in order
MANIFEST_NAMES = ("pack.toml", "pack.json")
