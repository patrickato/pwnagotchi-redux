"""Patch the pinned pi-gen export layout; never silently accept upstream drift."""
from pathlib import Path


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"unsupported pi-gen export layout: expected one {old!r}")
    return text.replace(old, new, 1)


def captures_layout(text, size_mib=512):
    if not isinstance(size_mib, int) or size_mib < 64 or size_mib > 32768:
        raise ValueError("captures partition must be 64–32768 MiB")
    old = 'IMG_SIZE=$((BOOT_PART_START + BOOT_PART_SIZE + ROOT_PART_SIZE))'
    text = replace_once(text, old,
        f'CAPTURE_PART_START=$((ROOT_PART_START + ROOT_PART_SIZE))\n'
        f'CAPTURE_PART_SIZE=$(({size_mib} * 1024 * 1024))\n'
        'IMG_SIZE=$((CAPTURE_PART_START + CAPTURE_PART_SIZE))')
    anchor = 'echo "Creating loop device..."'
    text = replace_once(text, anchor,
        'parted --script "${IMG_FILE}" unit B mkpart primary ext4 "${CAPTURE_PART_START}" "$((IMG_SIZE - 1))"\n\n' + anchor)
    text = replace_once(text, 'ROOT_DEV="${LOOP_DEV}p2"',
        'ROOT_DEV="${LOOP_DEV}p2"\nCAPTURE_DEV="${LOOP_DEV}p3"')
    text += '''
# Only this separate filesystem persists field writes. Upper/root writes are RAM.
mkfs.ext4 -L REDUXCAP "$CAPTURE_DEV" > /dev/null
mkdir -p "$ROOTFS_DIR/captures"
mount "$CAPTURE_DEV" "$ROOTFS_DIR/captures"
mkdir -p "$ROOTFS_DIR/captures/redux"
redux_ids=$(awk -F: '$1 == "redux" {print $3 ":" $4}' "$ROOTFS_DIR/etc/passwd")
[[ -n $redux_ids ]] || { echo 'redux account missing from image' >&2; exit 1; }
chown "$redux_ids" "$ROOTFS_DIR/captures/redux"
chmod 0700 "$ROOTFS_DIR/captures/redux"
'''
    return text


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prerun", type=Path)
    parser.add_argument("--captures-mib", type=int, default=512)
    args = parser.parse_args()
    try:
        args.prerun.write_text(captures_layout(args.prerun.read_text(), args.captures_mib))
    except (OSError, ValueError) as error:
        parser.error(str(error))
