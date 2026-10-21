"""Require signed Debian Bookworm metadata in the pinned pi-gen bootstrap.

Upstream pi-gen delegates to debootstrap and can continue after a warning
about a missing archive keyring. This source-only staging transform adds
--force-check-gpg to the exact pinned bootstrap command, so it cannot
silently downgrade verification. It never downloads packages or calls sudo.
"""
from __future__ import annotations

import argparse
from pathlib import Path


ANCHOR = "\tBOOTSTRAP_ARGS+=(--arch arm64)\n\tBOOTSTRAP_ARGS+=(--include gnupg)"
REPLACEMENT = ("\tBOOTSTRAP_ARGS+=(--arch arm64)\n"
               "\tBOOTSTRAP_ARGS+=(--force-check-gpg)\n"
               "\tBOOTSTRAP_ARGS+=(--include gnupg)")


def adapt(pi_gen_tree):
    path = Path(pi_gen_tree) / "scripts/common"
    if path.is_symlink() or not path.is_file():
        raise ValueError("pinned pi-gen scripts/common missing or linked")
    script = path.read_text(encoding="utf-8")
    if script.count(ANCHOR) != 1:
        raise ValueError("pinned debootstrap anchor drifted; refuse unsigned build")
    if ("--force-check-gpg" in script or "--no-check-gpg" in script
            or "--allow-unauthenticated" in script):
        raise ValueError("unexpected existing bootstrap signature override")
    # Preserve every other byte of pinned upstream. Only the immutable,
    # audited debootstrap argument list is modified.
    path.write_text(script.replace(ANCHOR, REPLACEMENT, 1), encoding="utf-8")
    return {"signed_release_required": True,
            "debootstrap_option": "--force-check-gpg",
            "staged_only": True, "image_built": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pi_gen_tree", type=Path)
    args = parser.parse_args(argv)
    try:
        result = adapt(args.pi_gen_tree)
    except (OSError, ValueError, UnicodeError) as error:
        parser.error(str(error))
    print("pi-gen debootstrap Release verification: --force-check-gpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
