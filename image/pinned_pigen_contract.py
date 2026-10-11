"""Read-only compatibility gate for pinned RPi-Distro/pi-gen export hooks."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

# Exact anchors from immutable PI_GEN_REV in image/sources.sh.
_CONTRACT = {
    "export-image/prerun.sh": (
        'IMG_SIZE=$((BOOT_PART_START + BOOT_PART_SIZE + ROOT_PART_SIZE))',
        'echo "Creating loop device..."',
        'ROOT_DEV="${LOOP_DEV}p2"',
        'parted --script "${IMG_FILE}" mklabel msdos',
        'mkdosfs -n bootfs -F "$FAT_SIZE"',
        'mkfs.ext4 -L rootfs',
        'mount -v "$BOOT_DEV" "${ROOTFS_DIR}/boot/firmware" -t vfat',
    ),
    "export-image/04-set-partuuid/00-run.sh": (
        'sed -i "s/BOOTDEV/PARTUUID=${BOOT_PARTUUID}/" "${ROOTFS_DIR}/etc/fstab"',
        'sed -i "s/ROOTDEV/PARTUUID=${ROOT_PARTUUID}/" "${ROOTFS_DIR}/etc/fstab"',
        'sed -i "s/ROOTDEV/PARTUUID=${ROOT_PARTUUID}/" "${ROOTFS_DIR}/boot/firmware/cmdline.txt"',
    ),
    "export-image/05-finalise/01-run.sh": (
        'update-initramfs -k all -c',
        'unmount "${ROOTFS_DIR}"',
        'zerofree "${ROOT_DEV}"',
        'case "${DEPLOY_COMPRESSION}" in',
        'xz --compress --force',
    ),
}
_STAGES = ("stage0/02-firmware", "stage2", "export-image")


def verify(tree):
    root = Path(tree)
    checks = {}
    for name in _STAGES:
        path = root / name
        ok = path.is_dir() and not path.is_symlink()
        checks["directory:" + name] = {
            "status": "ok" if ok else "blocker",
            "detail": "expected pi-gen stage exists" if ok else
                      "expected pi-gen stage missing or linked",
        }
    for name, anchors in _CONTRACT.items():
        path = root / name
        if path.is_symlink() or not path.is_file():
            checks[name] = {"status": "blocker",
                            "detail": "required pi-gen export script missing or linked",
                            "missing_anchors": list(anchors)}
            continue
        try:
            if path.stat().st_size > 128 * 1024:
                raise ValueError("export script unexpectedly large")
            contents = path.read_text(encoding="utf-8")
            missing = [anchor for anchor in anchors if contents.count(anchor) != 1]
            if name.endswith("05-finalise/01-run.sh") and not missing:
                if not (contents.index("update-initramfs -k all -c")
                        < contents.index('unmount "${ROOTFS_DIR}"')
                        < contents.index('zerofree "${ROOT_DEV}"')):
                    missing.append("initramfs -> unmount -> zerofree ordering")
        except (OSError, UnicodeError, ValueError) as error:
            missing = ["cannot safely inspect exporter: " + str(error)]
        checks[name] = {"status": "blocker" if missing else "ok",
                        "detail": "pinned exporter anchors match" if not missing else
                                  "pi-gen exporter drift; do not patch or build",
                        "missing_anchors": missing}
    marker = root / "stage2/EXPORT_IMAGE"
    valid = marker.is_file() and not marker.is_symlink()
    checks["stage2/EXPORT_IMAGE"] = {
        "status": "ok" if valid else "blocker",
        "detail": "Lite image export marker exists" if valid else
                  "pinned stage2 image export marker missing",
    }
    blockers = [key for key, result in checks.items() if result["status"] == "blocker"]
    return {"schema": 1, "overall": "blocked" if blockers else "contract_ok",
            "checks": checks, "blockers": blockers, "source_fetched": True,
            "image_built": False, "hardware_boot_verified": False,
            "note": "Anchor compatibility only; not a real build or physical boot result"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pi_gen_checkout", type=Path)
    args = parser.parse_args(argv)
    result = verify(args.pi_gen_checkout)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
