"""Unified operator config — one `config.toml` instead of scattered defaults.

Settings that an operator actually turns (where the Cache lives and how long it's
kept, how the dashboard is exposed and its token, the swarm keystore, the default
persona, which env var holds the at-rest passphrase) collected into one typed,
validated surface built on the fork's `defaults.toml` conventions — explicit
`>>> USER INPUT REQUIRED <<<` markers, no magic.

Read-only parse via stdlib `tomllib` (3.11+). A partial file merges over the
built-in defaults, so a sparse config is fine. `validate()` is glass-box: it
returns a list of human-readable problems rather than throwing, so the operator
can see everything wrong at once (`redux config check`).

Secrets are NOT stored here: the dashboard token is the one low-value shared
secret that lives in the file, and the at-rest passphrase is referenced only by
the *name* of the env var that holds it — never the value.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Union

PathLike = Union[str, Path]

DEFAULT_CONFIG_PATH = "/etc/redux/config.toml"
MARKER = ">>> USER INPUT REQUIRED <<<"

_BIND_SCOPES = ("localhost", "lan", "tailscale", "auto")
_PERSONAS = ("recon", "red", "blue", "purple", "mesh", "sigint")


@dataclass
class CacheCfg:
    db_path: str = "/var/lib/redux/geo/sightings.db"
    retention_days: float = 30.0      # prune sightings older than this (0 = keep forever)
    max_rows: int = 500_000           # hard cap; oldest beyond this are pruned


@dataclass
class WebCfg:
    bind_scope: str = "localhost"     # localhost | lan | tailscale | auto
    port: int = 8080
    token: str = ""                   # required for any off-box bind


@dataclass
class MeshCfg:
    keystore: str = "/etc/redux/swarm.keys"
    node_id: str = "augur-01"


@dataclass
class PersonaCfg:
    default: str = "recon"


@dataclass
class VaultCfg:
    passphrase_env: str = "AUGUR_PASSPHRASE"   # name of the env var; never the value


@dataclass
class TftCfg:
    face_pack: str = "augur"                   # augur | owl | fox


@dataclass
class AugurConfig:
    cache: CacheCfg = field(default_factory=CacheCfg)
    web: WebCfg = field(default_factory=WebCfg)
    mesh: MeshCfg = field(default_factory=MeshCfg)
    persona: PersonaCfg = field(default_factory=PersonaCfg)
    vault: VaultCfg = field(default_factory=VaultCfg)
    tft: TftCfg = field(default_factory=TftCfg)

    _SECTIONS = {"cache": CacheCfg, "web": WebCfg, "mesh": MeshCfg,
                 "persona": PersonaCfg, "vault": VaultCfg, "tft": TftCfg}

    @classmethod
    def from_dict(cls, d: Dict) -> "AugurConfig":
        """Merge a (possibly partial) dict over the defaults. Unknown sections and
        keys are ignored rather than fatal — forward/backward tolerant."""
        kw = {}
        for name, klass in cls._SECTIONS.items():
            section = d.get(name) or {}
            known = {f for f in klass().__dataclass_fields__}  # type: ignore[attr-defined]
            kw[name] = klass(**{k: v for k, v in section.items() if k in known})
        return cls(**kw)

    @classmethod
    def load(cls, path: PathLike) -> "AugurConfig":
        """Parse a TOML file (must exist) and merge over defaults."""
        with open(path, "rb") as f:
            return cls.from_dict(tomllib.load(f))

    @classmethod
    def resolve(cls, path: PathLike | None = None) -> "AugurConfig":
        """Config from `path` if given and present, else the built-in defaults."""
        if path and Path(path).exists():
            return cls.load(path)
        return cls()

    def validate(self) -> List[str]:
        """Human-readable problems (empty list = good). Never raises."""
        p: List[str] = []
        if self.web.bind_scope not in _BIND_SCOPES:
            p.append(f"web.bind_scope '{self.web.bind_scope}' invalid (use one of {', '.join(_BIND_SCOPES)})")
        if self.web.bind_scope != "localhost" and not self.web.token:
            p.append(f"web.token is required for an off-box bind ({self.web.bind_scope})")
        if not isinstance(self.web.port, int) or not (1 <= self.web.port <= 65535):
            p.append(f"web.port {self.web.port!r} out of range")
        if not isinstance(self.cache.retention_days, (int, float)) or isinstance(self.cache.retention_days, bool):
            p.append(f"cache.retention_days must be a number (got {self.cache.retention_days!r})")
        elif self.cache.retention_days < 0:
            p.append("cache.retention_days must be >= 0 (0 = keep forever)")
        if not isinstance(self.cache.max_rows, int) or isinstance(self.cache.max_rows, bool):
            p.append(f"cache.max_rows must be an integer (got {self.cache.max_rows!r})")
        elif self.cache.max_rows <= 0:
            p.append("cache.max_rows must be > 0")
        if self.persona.default not in _PERSONAS:
            p.append(f"persona.default '{self.persona.default}' unknown (use one of {', '.join(_PERSONAS)})")
        if not isinstance(self.mesh.node_id, str) or not self.mesh.node_id or MARKER in self.mesh.node_id:
            p.append("mesh.node_id must be set to a unique value")
        if not self.vault.passphrase_env:
            p.append("vault.passphrase_env must name the env var holding the passphrase")
        try:
            from ..face import list_packs
            packs = list_packs()
        except Exception:
            packs = ["augur", "owl", "fox"]
        if self.tft.face_pack not in packs:
            p.append(f"tft.face_pack '{self.tft.face_pack}' unknown (use one of {', '.join(packs)})")
        return p

    def to_display(self) -> Dict:
        """asdict with the one secret masked, for `config show`."""
        d = asdict(self)
        d["web"]["token"] = "(set)" if self.web.token else "(unset)"
        return d


def template() -> str:
    """A ready-to-edit config.toml, modeled on the fork's defaults.toml style with
    explicit USER INPUT markers. Parses clean and validates on localhost."""
    return f"""# Augur configuration (pwnagotchi-redux). Place at {DEFAULT_CONFIG_PATH} and edit.
# Fields marked {MARKER} must be set before the feature that uses them.

[cache]
db_path = "/var/lib/redux/geo/sightings.db"
retention_days = 30          # prune sightings older than N days (0 = keep forever)
max_rows = 500000            # hard cap; oldest rows beyond this are pruned

[web]
bind_scope = "localhost"     # localhost | lan | tailscale | auto
port = 8080
token = ""                   # {MARKER} if bind_scope != localhost (empty is fine on localhost)

[mesh]
keystore = "/etc/redux/swarm.keys"   # sealed swarm-key store (see: redux mesh key ...)
node_id = "augur-01"         # {MARKER} unique per device

[persona]
default = "recon"            # recon | red | blue | purple | mesh | sigint

[vault]
passphrase_env = "AUGUR_PASSPHRASE"   # env var that holds the at-rest passphrase (never the value)

[tft]
face_pack = "augur"          # on-device face look: augur (corvid) | owl | fox
"""
