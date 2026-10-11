"""Wire the boot payload gate into the pinned pi-gen exporter.

Default Lite: validate its mounted final boot partition before unmount.
Optional A/B: clone validates both mounted boot slots and selector.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from export_layout import replace_once


def prepare(source, tree, profile):
    if profile not in {"lite", "ab"}:
        raise ValueError("boot gate profile must be lite or ab")
    source, tree = Path(source), Path(tree)
    stage = tree / "export-image"
    finalise = stage / "05-finalise/01-run.sh"
    target = stage / "redux-boot-payload.py"
    if target.exists():
        raise ValueError("export boot gate already prepared")
    script = finalise.read_text()
    if profile == "lite":
        if "redux-ota-clone.sh" in script:
            raise ValueError("Lite boot gate refuses an A/B exporter")
        script = replace_once(
            script, 'unmount "${ROOTFS_DIR}"',
            'python3 "${STAGE_DIR}/redux-boot-payload.py" --profile lite --root "${ROOTFS_DIR}"\n'
            'unmount "${ROOTFS_DIR}"')
        finalise.write_text(script)
    elif ('redux-ota-clone.sh' not in script
          or 'redux-boot-payload.py' not in (stage / "redux-ota-clone.sh").read_text()):
        raise ValueError("A/B gate must run while both boot slots are mounted")
    shutil.copyfile(source / "boot_payload.py", target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("tree", type=Path)
    parser.add_argument("--profile", required=True, choices=["lite", "ab"])
    args = parser.parse_args(argv)
    try:
        prepare(args.source, args.tree, args.profile)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
