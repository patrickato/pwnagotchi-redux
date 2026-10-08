"""redux.vault — at-rest encryption for captured data (optional `crypto` extra).
Honest, fail-closed: without the backend the Vault refuses rather than writing
plaintext. See vault.py. No wipe-on-tamper here — this is protection, not
anti-forensics."""
from .vault import (
    Vault, VaultUnavailable, BadVaultData,
    crypto_available, derive_key, resolve_passphrase, MAGIC,
)

__all__ = [
    "Vault", "VaultUnavailable", "BadVaultData",
    "crypto_available", "derive_key", "resolve_passphrase", "MAGIC",
]
