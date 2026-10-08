# redux.vault — at-rest encryption

A field drop can be lost or left behind. What it captured is sensitive not only to
the operator but to the people whose networks were seen, so the vault seals that
data: a misplaced card yields ciphertext, not someone's handshakes.

**This is data protection, not anti-forensics.** There is deliberately no
"wipe / destroy on tamper" — the answer to a lost card is that the data is already
encrypted, not that it self-destructs.

## How it works

- **Authenticated encryption via Fernet** (AES-128-CBC + HMAC-SHA256) from the
  `cryptography` package. We don't roll our own crypto.
- **Key from a passphrase via scrypt** (stdlib `hashlib`). A fresh random salt per
  seal rides in the envelope (`AUGURv1` magic + salt + token), so the same plaintext
  seals differently every time and no separate keyfile is needed to open it.
- **Honest, fail-closed.** Encryption is the optional `crypto` extra. If it isn't
  installed, `Vault` **refuses to construct** rather than silently writing
  plaintext — `crypto_available()` reports the truth and the CLI says so.
- **Passphrase never via argv.** `AUGUR_PASSPHRASE` or an interactive prompt, so it
  can't leak into the process list or shell history.

## Use

```
pip install 'pwnagotchi-redux[crypto]'          # the optional backend

redux vault seal --in loot.pcap --out loot.vault     # passphrase via env/prompt
redux vault open --in loot.vault --out loot.pcap
redux cache export --out cache.jsonl.vault --encrypt # Cache sealed in memory
```

Library: `Vault(passphrase).seal(bytes) -> bytes` / `.unseal(blob) -> bytes`, plus
`seal_file` / `unseal_file`. Wrong passphrase or a tampered byte raises
`BadVaultData` (the HMAC catches it); a missing backend raises `VaultUnavailable`.

## Still to wire

Sealing the **live** on-device stores in place (seal at shutdown, open at boot) and
the **swarm-key lifecycle** (generate / rotate / exchange) build on this primitive.
