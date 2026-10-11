"""Write unique, private build-local first-user credentials for pinned pi-gen.

pi-gen requires FIRST_USER_PASS when the interactive first-boot user rename
is disabled. Standard images use a per-build random secret; opt-in disposable development
images allow a short local password with remote SSH explicitly disabled.
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


def generate(config, credential, *, random_hex=None, dev_login=False):
    config, credential = Path(config), Path(credential)
    if config.is_symlink() or not config.is_file():
        raise ValueError("pi-gen config must be a regular file")
    if credential.exists() or credential.is_symlink():
        raise ValueError("build credential file already exists; choose a fresh build")
    if credential.parent != config.parent.parent:
        raise ValueError("operator credential must stay in the build workspace root")
    if config.parent.name != "pi-gen":
        raise ValueError("expected pi-gen directory within a fresh build workspace")
    if dev_login:
        if random_hex is not None:
            raise ValueError("development login cannot inject a random password")
        secret = "redux"
    else:
        secret = random_hex() if random_hex is not None else secrets.token_hex(PASSWORD_HEX_BYTES)
        if len(secret) != PASSWORD_HEX_BYTES * 2 or any(ch not in "0123456789abcdef" for ch in secret):
            raise ValueError("credential generation did not yield 192-bit hex entropy")
    # Fail closed if the destination is unexpectedly already present. The
    # credential file is never readable by ordinary users in the build host.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(credential, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(("Redux disposable test-console credentials (never for release).\n" if dev_login else "Redux per-image local console password (keep private).\n"))
            handle.write("Username: " + ("pi" if dev_login else "field") + "\n")
            handle.write("Password: " + secret + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(config, 0o600)
        with config.open("a", encoding="utf-8") as out:
            # ASCII hex is safe in pi-gen's shell config and chpasswd.
            out.write("\n# Unique build-time first-user password; no shared default\n")
            if dev_login:
                out.write("# Development-only physical-console account; SSH forced off\n")
                out.write("FIRST_USER_NAME='pi'\nENABLE_SSH=0\nPUBKEY_ONLY_SSH=0\n")
            out.write("FIRST_USER_PASS='" + secret + "'\n")
    except BaseException:
        credential.unlink(missing_ok=True)
        raise
    return {"password_bits": 0 if dev_login else PASSWORD_BITS, "dev_login": dev_login, "credential_mode": "0600",
            "credential_path": str(credential)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pi_gen_config", type=Path)
    parser.add_argument("credential_file", type=Path)
    parser.add_argument("--dev-login", action="store_true", help="explicit test-only pi/redux local login; SSH off")
    args = parser.parse_args(argv)
    try:
        result = generate(args.pi_gen_config, args.credential_file, dev_login=args.dev_login)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print("Generated private per-image login secret: " + result["credential_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
