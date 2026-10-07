#!/bin/bash
set -euo pipefail
LOOP_DEV=$(losetup -n -O NAME -j "${STAGE_WORK_DIR}/${IMG_FILENAME}${IMG_SUFFIX}.img")
ROOT_PARTUUID=$(blkid -s PARTUUID -o value "${LOOP_DEV}p4")
# Boot mounts come from the slot-aware generator; /dev/root is selected by the kernel.
sed -i '/[[:space:]]\/boot\/firmware[[:space:]]/d; /[[:space:]]\/captures[[:space:]]/d; s/ROOTDEV/\/dev\/root/; s/BOOTDEV/\/dev\/null/' "$ROOTFS_DIR/etc/fstab"
sed -i '\|[[:space:]]/[[:space:]]|s/[[:space:]][0-9]$/ 0/' "$ROOTFS_DIR/etc/fstab"
PYTHONPATH="$ROOTFS_DIR/opt/redux" python3 - "$ROOTFS_DIR" "$ROOT_PARTUUID" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]) / 'opt/redux/boot'))
from ota import patch_cmdline
p = Path(sys.argv[1]) / 'boot/firmware/cmdline.txt'
p.write_text(patch_cmdline(p.read_text(), 'A', sys.argv[2]))
PY
