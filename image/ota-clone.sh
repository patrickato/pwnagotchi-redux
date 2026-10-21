#!/bin/bash
# Run after upstream finalisation has regenerated both initramfs files.
set -euo pipefail
LOOP_DEV=$(losetup -n -O NAME -j "${STAGE_WORK_DIR}/${IMG_FILENAME}${IMG_SUFFIX}.img")
other="$STAGE_WORK_DIR/slot-b"
mkdir -p "$other"
mount "${LOOP_DEV}p5" "$other"
trap 'unmount "$other"' EXIT
rsync -aHAXx --exclude /boot/firmware --exclude /boot/redux-control --exclude /captures "$ROOTFS_DIR/" "$other/"
mkdir -p "$other/boot/firmware" "$other/boot/redux-control" "$other/captures"
mount "${LOOP_DEV}p3" "$other/boot/firmware"
rsync -rtx "$ROOTFS_DIR/boot/firmware/" "$other/boot/firmware/"
ROOT_PARTUUID=$(blkid -s PARTUUID -o value "${LOOP_DEV}p5")
PYTHONPATH="$ROOTFS_DIR/opt/redux" python3 - "$ROOTFS_DIR" "$other" "$ROOT_PARTUUID" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]) / 'opt/redux/boot'))
from ota import patch_cmdline
p = Path(sys.argv[2]) / 'boot/firmware/cmdline.txt'
p.write_text(patch_cmdline(p.read_text(), 'B', sys.argv[3]))
PY
# Validate both finished boot slots and selector before the inactive slot unmounts.
ROOT_A_PARTUUID=$(blkid -s PARTUUID -o value "${LOOP_DEV}p4")
python3 "${STAGE_DIR}/redux-boot-payload.py" --profile ab \
    --root "$ROOTFS_DIR" --other-root "$other" \
    --a-root-partuuid "$ROOT_A_PARTUUID" --b-root-partuuid "$ROOT_PARTUUID"
sync
unmount "$other"
trap - EXIT
