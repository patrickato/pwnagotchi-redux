"""Write unique, private build-local first-user credentials for pinned pi-gen.

pi-gen requires FIRST_USER_PASS when the interactive first-boot user rename
is disabled. A per-build random secret is less risky than a shared password.
Never print the secret or copy the plaintext credential file into the image.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import stat

PASSWORD_BITS = 192
PASSWORD_HEX_BYTES = PASSWORD_BITS // 8


def generate(config, credential, *, random_hex=None):
    config, credential = Path(config), Path(credential)
    if config.is_symlink() or not config.is_file():
        raise ValueError("pi-gen config must be a regular file")
    if credential.exists() or credential.is_symlink():
        raise ValueError("build credential file already exists; choose a fresh build")
    if credential.parent != config.parent.parent:
        raise ValueError("operator credential must stay in the build workspace root")
    if config.parent.name != "pi-gen":
        raise ValueError("expected pi-gen directory within a fresh build workspace")
    secret = random_hex() if random_hex is not None else secrets.token_hex(PASSWORD_HEX_BYTES)
    if len(secret) != PASSWORD_HEX_BYTES * 2 or any(ch not in "0123456789abcdef" for ch in secret):
        raise ValueError("credential generation did not yield 192-bit hex entropy")
    # Fail closed if the destination is unexpectedly already present. The
    # credential file is never readable by ordinary users in the build host.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(credential, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write("Redux per-image local console password (keep private).\n")
            handle.write("Username: field (unless changed in image config)\n")
            handle.write("Password: " + secret + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(config, 0o600)
        with config.open("a", encoding="utf-8") as out:
            # ASCII hex is safe in pi-gen's shell config and chpasswd.
            out.write("\n# Unique build-time first-user password; no shared default\n")
            out.write("FIRST_USER_PASS='" + secret + "'\n")
    except BaseException:
        credential.unlink(missing_ok=True)
        raise
    return {"password_bits": PASSWORD_BITS, "credential_mode": "0600",
            "credential_path": str(credential)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pi_gen_config", type=Path)
    parser.add_argument("credential_file", type=Path)
    args = parser.parse_args(argv)
    try:
        result = generate(args.pi_gen_config, args.credential_file)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print("Generated private per-image login secret: " + result["credential_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
