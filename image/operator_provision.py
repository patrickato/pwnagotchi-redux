"""Render safe pi-gen SSH public-key onboarding without a default password.

The key is user-supplied at build time, never fetched or generated in-image.
The output is POSIX shell config for pi-gen; no private key is accepted.
"""
from __future__ import annotations

import argparse
import base64
from pathlib import Path
import shlex
import stat

_TYPES = frozenset(("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"))


def public_key(path):
    keyfile = Path(path)
    info = keyfile.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 8192 or info.st_size < 45:
        raise ValueError("SSH public key must be a regular, nonempty file; no symlinks")
    content = keyfile.read_text(encoding="utf-8")
    if any(ord(ch) < 32 and ch not in "\r\n" for ch in content):
        raise ValueError("SSH public key contains invalid control characters")
    lines = content.splitlines()
    if len(lines) != 1:
        raise ValueError("SSH public-key file must contain exactly one key line")
    line = lines[0].strip()
    fields = line.split(maxsplit=2)
    if len(fields) < 2 or fields[0] not in _TYPES:
        raise ValueError("expected one OpenSSH ed25519, ECDSA or RSA public key")
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("SSH public-key body is not valid base64") from exc
    if len(blob) < 8:
        raise ValueError("SSH public key is truncated")
    count = int.from_bytes(blob[:4], "big")
    if count != len(fields[0]) or blob[4:4 + count] != fields[0].encode("ascii"):
        raise ValueError("SSH public-key encoding does not match its declared type")
    if fields[0] == "ssh-ed25519":
        # Key data is uint32 name length, name, uint32 public key length, 32-byte key.
        offset = 4 + count
        if len(blob) != offset + 4 + 32 or int.from_bytes(blob[offset:offset+4], "big") != 32:
            raise ValueError("SSH ed25519 key payload is malformed")
    if "\n" in line or "\r" in line:
        raise ValueError("invalid SSH key line ending")
    return line


def config_fragment(path):
    key = public_key(path)
    return "\n".join((
        "# Redux operator-provided SSH public-key-only boot access",
        "DISABLE_FIRST_BOOT_USER_RENAME=1",
        "ENABLE_SSH=1",
        "PUBKEY_ONLY_SSH=1",
        "PUBKEY_SSH_FIRST_USER=" + shlex.quote(key),
        "",
    ))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_key_file", type=Path)
    args = parser.parse_args(argv)
    try:
        print(config_fragment(args.public_key_file), end="")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
