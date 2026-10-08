"""Swarm-key lifecycle — generate, rotate, exchange, and store the mesh key.

The mesh authenticates every Scope delta with a shared HMAC key (see
`scope_sync.py`). That key needs a life story, or it becomes the weak point:

  - **Generate** a fresh random key when you arm a lab/job.
  - **Rotate** it per job — a new key becomes current, recent keys stay valid for a
    short grace window so in-flight deltas still verify; then they age out.
  - **Expiry** rides on each key, so a lapsed key stops authorizing itself (the same
    "lapsed permission stops authorizing" rule the Scope uses).
  - **Exchange** out-of-band via a compact token you can show as a QR or send over
    the LoRa lane to a trusted peer node.
  - **Store at rest sealed** through `redux.vault` — the key is exactly the kind of
    secret a lost card must not spill — so persistence is honest/fail-closed: no
    crypto backend, no plaintext keystore.

The keyring and token logic are pure/stdlib and need no crypto backend; only the
at-rest `save`/`load` do (they go through the Vault).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

PathLike = Union[str, Path]
_TOKEN_PREFIX = "AKEY1:"


@dataclass(frozen=True)
class SwarmKey:
    key: bytes
    created: float
    label: str = ""
    expires: Optional[float] = None

    @property
    def kid(self) -> str:
        """Short public fingerprint — safe to log/show; not the key material."""
        return hashlib.sha256(self.key).hexdigest()[:12]

    def is_expired(self, now: float) -> bool:
        return self.expires is not None and now >= self.expires


def generate_key(label: str = "", ttl: Optional[float] = None,
                 now: Optional[float] = None, *, nbytes: int = 32) -> SwarmKey:
    now = time.time() if now is None else now
    return SwarmKey(key=os.urandom(nbytes), created=now, label=label,
                    expires=(now + ttl) if ttl else None)


# --- out-of-band exchange token (QR / LoRa) --------------------------------- #

def export_token(k: SwarmKey) -> str:
    """A compact portable token carrying the key + metadata, for a QR or a LoRa
    hand-off to a trusted peer. It contains key material by design — share it over
    a channel you trust, not a broadcast."""
    body = {"k": base64.urlsafe_b64encode(k.key).decode(), "c": k.created,
            "l": k.label, "e": k.expires}
    return _TOKEN_PREFIX + base64.urlsafe_b64encode(json.dumps(body).encode()).decode()


def import_token(tok: str) -> SwarmKey:
    if not tok.startswith(_TOKEN_PREFIX):
        raise ValueError("not an Augur swarm-key token")
    try:
        body = json.loads(base64.urlsafe_b64decode(tok[len(_TOKEN_PREFIX):]))
        return SwarmKey(key=base64.urlsafe_b64decode(body["k"]), created=float(body["c"]),
                        label=body.get("l", ""), expires=body.get("e"))
    except Exception as e:
        raise ValueError("corrupt swarm-key token") from e


@dataclass
class SwarmKeyring:
    """Current key + a bounded tail of recent keys (the rotation grace window)."""
    keys: List[SwarmKey] = field(default_factory=list)   # newest first
    grace: int = 3                                        # recent keys kept for verify

    def add(self, k: SwarmKey) -> None:
        self.keys.insert(0, k)
        del self.keys[1 + self.grace:]                    # bound the history

    def active(self, now: Optional[float] = None) -> Optional[SwarmKey]:
        """The newest non-expired key — the one to sign with."""
        now = time.time() if now is None else now
        for k in self.keys:
            if not k.is_expired(now):
                return k
        return None

    def verifiers(self, now: Optional[float] = None) -> List[SwarmKey]:
        """All non-expired keys — accepted when verifying during a rotation grace."""
        now = time.time() if now is None else now
        return [k for k in self.keys if not k.is_expired(now)]

    def rotate(self, *, label: str = "", ttl: Optional[float] = None,
               now: Optional[float] = None) -> SwarmKey:
        now = time.time() if now is None else now
        k = generate_key(label=label, ttl=ttl, now=now)
        self.add(k)
        return k

    def import_token(self, tok: str) -> SwarmKey:
        k = import_token(tok)
        self.add(k)
        return k

    def prune(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        before = len(self.keys)
        self.keys = [k for k in self.keys if not k.is_expired(now)]
        return before - len(self.keys)

    # --- serialization ----------------------------------------------------- #

    def to_json(self) -> str:
        return json.dumps({"grace": self.grace, "keys": [
            {"k": base64.urlsafe_b64encode(k.key).decode(), "c": k.created,
             "l": k.label, "e": k.expires} for k in self.keys]})

    @classmethod
    def from_json(cls, s: str) -> "SwarmKeyring":
        d = json.loads(s)
        keys = [SwarmKey(key=base64.urlsafe_b64decode(x["k"]), created=float(x["c"]),
                         label=x.get("l", ""), expires=x.get("e")) for x in d.get("keys", [])]
        return cls(keys=keys, grace=int(d.get("grace", 3)))


def scopesync_keys(ring: SwarmKeyring, now: Optional[float] = None) -> Tuple[bytes, List[bytes]]:
    """(signing_key, [extra_verify_keys]) for wiring a ScopeSync: sign with the
    newest active key, still verify deltas signed by recent (grace) keys."""
    vs = ring.verifiers(now)
    if not vs:
        raise ValueError("no active swarm key (generate or rotate one)")
    return vs[0].key, [k.key for k in vs[1:]]


# --- sealed at-rest keystore (needs redux.vault / the crypto extra) --------- #

def save(ring: SwarmKeyring, path: PathLike, passphrase: Union[str, bytes]) -> int:
    """Seal the keyring to disk. Fail-closed: without the crypto backend this
    raises (via Vault) rather than writing the key in plaintext."""
    from ..vault import Vault
    blob = Vault(passphrase).seal(ring.to_json().encode())
    Path(path).write_bytes(blob)
    return len(blob)


def load(path: PathLike, passphrase: Union[str, bytes]) -> SwarmKeyring:
    from ..vault import Vault
    data = Vault(passphrase).unseal(Path(path).read_bytes())
    return SwarmKeyring.from_json(data.decode())
