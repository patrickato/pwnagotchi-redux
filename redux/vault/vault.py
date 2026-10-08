"""redux.vault — at-rest encryption for captured data.

A field drop can be lost or left behind. Handshakes, cracked PSKs, EAP hashes,
the Scope, the swarm key, and the Cache are sensitive — not only to the operator
but to the people whose networks were captured. This seals them so a misplaced
card yields ciphertext, not someone's data.

Design:
  - Authenticated encryption via **Fernet** (AES-128-CBC + HMAC-SHA256) from the
    `cryptography` package. We do NOT roll our own crypto.
  - Key derived from an operator passphrase with **scrypt** (stdlib hashlib). A
    fresh random salt per seal travels in the envelope, so the same plaintext
    seals differently each time and no separate keyfile is needed to open it.
  - **Honest, fail-closed absence:** if the optional `crypto` extra isn't
    installed, the Vault refuses to exist rather than silently writing plaintext.
    Glass-box — it never pretends to protect data it can't.

This is *data protection*, not anti-forensics: there is deliberately no
"wipe / destroy on tamper" here.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

MAGIC = b"AUGURv1\n"
_SALT_LEN = 16
# scrypt work factors — resist offline guessing of a lost card's passphrase while
# staying reasonable on a Pi 4 (~tens of ms per derive).
_N, _R, _P = 2 ** 15, 8, 1
_MAXMEM = 128 * 1024 * 1024


class VaultUnavailable(RuntimeError):
    """At-rest encryption was requested but the crypto backend isn't installed."""


class BadVaultData(ValueError):
    """Not a vault envelope, or the passphrase is wrong / the data is corrupt."""


def _fernet_cls():
    """The Fernet class, or None if the optional `crypto` extra isn't installed.
    Isolated so callers can check honestly and tests can force the absent path."""
    try:
        from cryptography.fernet import Fernet
        return Fernet
    except Exception:
        return None


def crypto_available() -> bool:
    return _fernet_cls() is not None


def derive_key(passphrase: Union[str, bytes], salt: bytes) -> bytes:
    """scrypt KDF → a urlsafe-base64 32-byte key suitable for Fernet."""
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")
    raw = hashlib.scrypt(passphrase, salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32)
    return base64.urlsafe_b64encode(raw)


def resolve_passphrase(env: str = "AUGUR_PASSPHRASE", *, confirm: bool = False) -> str:
    """Passphrase from the env var if set, else an interactive getpass prompt.
    Never read from argv (it would leak into the process list / shell history)."""
    v = os.environ.get(env)
    if v:
        return v
    import getpass
    v = getpass.getpass("vault passphrase: ")
    if confirm and v != getpass.getpass("confirm passphrase: "):
        raise ValueError("passphrases did not match")
    return v


class Vault:
    """Seals/opens bytes with a passphrase. Construction fails (honestly) when the
    crypto backend is absent — it never degrades to plaintext."""

    def __init__(self, passphrase: Union[str, bytes]):
        fernet = _fernet_cls()
        if fernet is None:
            raise VaultUnavailable(
                "at-rest encryption needs the crypto extra: "
                "pip install 'pwnagotchi-redux[crypto]'"
            )
        if not passphrase:
            raise ValueError("a non-empty passphrase is required")
        self._Fernet = fernet
        self._passphrase = passphrase

    def seal(self, data: bytes) -> bytes:
        salt = os.urandom(_SALT_LEN)
        token = self._Fernet(derive_key(self._passphrase, salt)).encrypt(data)
        return MAGIC + salt + token

    def unseal(self, blob: bytes) -> bytes:
        if not blob.startswith(MAGIC):
            raise BadVaultData("not an Augur vault envelope")
        body = blob[len(MAGIC):]
        salt, token = body[:_SALT_LEN], body[_SALT_LEN:]
        try:
            return self._Fernet(derive_key(self._passphrase, salt)).decrypt(token)
        except Exception as e:  # InvalidToken, or short/garbled data
            raise BadVaultData("wrong passphrase or corrupted data") from e

    def seal_file(self, src: PathLike, dst: PathLike) -> int:
        out = self.seal(Path(src).read_bytes())
        Path(dst).write_bytes(out)
        return len(out)

    def unseal_file(self, src: PathLike, dst: PathLike) -> int:
        data = self.unseal(Path(src).read_bytes())
        Path(dst).write_bytes(data)
        return len(data)
