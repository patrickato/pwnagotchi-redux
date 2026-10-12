"""Wire the boot payload gate into the pinned pi-gen exporter.

Default Lite: validate assembled root and boot partition before unmount.
Optional A/B: clone validates both mounted root/boot slots and selector.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from export_layout import replace_once


def prepare(source, tree, profile, *, expected_country=None):
    if expected_country is not None:
        if (len(expected_country) != 2 or not expected_country.isascii()
                or not expected_country.isalpha() or not expected_country.isupper()
                or expected_country in ("AA", "ZZ")):
            raise ValueError("invalid operator-selected regulatory country")
    country_arg = f" --expected-country {expected_country}" if expected_country else ""
    if profile not in {"lite", "ab"}:
        raise ValueError("boot gate profile must be lite or ab")
    source, tree = Path(source), Path(tree)
    stage = tree / "export-image"
    finalise = stage / "05-finalise/01-run.sh"
    target = stage / "redux-boot-payload.py"
    root_target = stage / "redux-root-payload.py"
    if target.exists() or root_target.exists():
        raise ValueError("export boot/root gate already prepared")
    script = finalise.read_text()
    if profile == "lite":
        if "redux-ota-clone.sh" in script:
            raise ValueError("Lite boot gate refuses an A/B exporter")
        script = replace_once(
            script, 'unmount "${ROOTFS_DIR}"',
            f'python3 "${{STAGE_DIR}}/redux-root-payload.py" --profile lite --root "${{ROOTFS_DIR}}"{country_arg}\n'
            'python3 "${STAGE_DIR}/redux-boot-payload.py" --profile lite --root "${ROOTFS_DIR}"\n'
            'unmount "${ROOTFS_DIR}"')
        finalise.write_text(script)
    else:
        clone = stage / "redux-ota-clone.sh"
        if ("redux-ota-clone.sh" not in script or not clone.is_file()
                or "redux-boot-payload.py" not in clone.read_text()):
            raise ValueError("A/B gate must run while both boot slots are mounted")
        # The clone script still has BOTH root slots mounted here. Run the
        # assembled root contract before the existing boot signature gate.
        cloned = replace_once(clone.read_text(),
            'python3 "${STAGE_DIR}/redux-boot-payload.py" --profile ab',
            'python3 "${STAGE_DIR}/redux-root-payload.py" --profile ab ' 
            f'--root "$ROOTFS_DIR" --other-root "$other"{country_arg}\n'
            'python3 "${STAGE_DIR}/redux-boot-payload.py" --profile ab')
        clone.write_text(cloned)
    shutil.copyfile(source / "boot_payload.py", target)
    shutil.copyfile(source / "root_payload.py", root_target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("tree", type=Path)
    parser.add_argument("--profile", required=True, choices=["lite", "ab"])
    parser.add_argument("--expected-country")
    args = parser.parse_args(argv)
    try:
        prepare(args.source, args.tree, args.profile,
                expected_country=args.expected_country)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
