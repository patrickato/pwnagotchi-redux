#!/bin/bash
# GPT topology follows Raspberry Pi's documented tryboot partition flow.
set -euo pipefail
IMG_FILE="${STAGE_WORK_DIR}/${IMG_FILENAME}${IMG_SUFFIX}.img"
unmount_image "$IMG_FILE"
[[ ! -e $IMG_FILE ]] || { echo 'A/B export refuses to replace an existing image.' >&2; exit 1; }
[[ ! -e $ROOTFS_DIR ]] || { echo 'A/B export requires a fresh root mountpoint.' >&2; exit 1; }
mkdir -p "$ROOTFS_DIR"
ALIGN=$((8 * 1024 * 1024))
CONTROL_SIZE=$((32 * 1024 * 1024))
BOOT_SIZE=$((512 * 1024 * 1024))
ROOT_SIZE=$(du -x --apparent-size -s "$EXPORT_ROOTFS_DIR" --exclude var/cache/apt/archives --exclude boot/firmware --block-size=1 | cut -f1)
ROOT_PART_SIZE=$((${REDUX_ROOT_SLOT_MIB:-4096} * 1024 * 1024))
[[ $ROOT_PART_SIZE -ge $((ROOT_SIZE + ROOT_SIZE / 5 + 200 * 1024 * 1024)) ]] || { echo 'Root slot too small; increase REDUX_ROOT_SLOT_MIB.' >&2; exit 1; }
CAPTURE_SIZE=$((${REDUX_CAPTURE_MIB:-512} * 1024 * 1024))
IMG_SIZE=$((ALIGN + CONTROL_SIZE + BOOT_SIZE * 2 + ROOT_PART_SIZE * 2 + CAPTURE_SIZE + ALIGN))
truncate -s "$IMG_SIZE" "$IMG_FILE"
parted --script "$IMG_FILE" mklabel gpt
start=$ALIGN
names=(REDUXCTRL REDUXBOOTA REDUXBOOTB REDUXROOTA REDUXROOTB REDUXCAP)
types=(fat32 fat32 fat32 ext4 ext4 ext4)
sizes=($CONTROL_SIZE $BOOT_SIZE $BOOT_SIZE $ROOT_PART_SIZE $ROOT_PART_SIZE $CAPTURE_SIZE)
for index in {0..5}; do
    end=$((start + sizes[index] - 1))
    parted --script "$IMG_FILE" unit B mkpart "${names[index]}" "${types[index]}" "$start" "$end"
    start=$((end + 1))
done
parted --script "$IMG_FILE" set 1 boot on
ensure_next_loopdev
LOOP_DEV=$(losetup --show --find --partscan "$IMG_FILE")
ensure_loopdev_partitions "$LOOP_DEV"
BOOT_DEV="${LOOP_DEV}p2"
ROOT_DEV="${LOOP_DEV}p4"
mkdosfs -n REDUXCTRL -F 16 "${LOOP_DEV}p1" >/dev/null
for number in 2 3; do mkdosfs -n "${names[number-1]}" -F 32 "${LOOP_DEV}p${number}" >/dev/null; done
for number in 4 5 6; do mkfs.ext4 -L "${names[number-1]}" -O '^huge_file,^64bit' "${LOOP_DEV}p${number}" >/dev/null; done
mount "$ROOT_DEV" "$ROOTFS_DIR" -t ext4
mkdir -p "$ROOTFS_DIR/boot/firmware" "$ROOTFS_DIR/boot/redux-control" "$ROOTFS_DIR/captures"
mount "$BOOT_DEV" "$ROOTFS_DIR/boot/firmware" -t vfat
mount "${LOOP_DEV}p1" "$ROOTFS_DIR/boot/redux-control" -t vfat
mount "${LOOP_DEV}p6" "$ROOTFS_DIR/captures" -t ext4
rsync -aHAXx --exclude /var/cache/apt/archives --exclude /boot/firmware --exclude /boot/redux-control --exclude /captures "$EXPORT_ROOTFS_DIR/" "$ROOTFS_DIR/"
rsync -rtx "$EXPORT_ROOTFS_DIR/boot/firmware/" "$ROOTFS_DIR/boot/firmware/"
printf '[all]\ntryboot_a_b=1\nboot_partition=2\n[tryboot]\nboot_partition=3\n' > "$ROOTFS_DIR/boot/redux-control/autoboot.txt"
# Pi 4's firmware initially classifies a FAT partition by start.elf; Pi 5 by config.txt.
cp "$ROOTFS_DIR/boot/firmware/start4.elf" "$ROOTFS_DIR/boot/redux-control/start.elf"
printf '[all]\narm_64bit=1\n' > "$ROOTFS_DIR/boot/redux-control/config.txt"
mkdir -p "$ROOTFS_DIR/captures/redux" "$ROOTFS_DIR/captures/rauc"
redux_ids=$(awk -F: '$1 == "redux" {print $3 ":" $4}' "$ROOTFS_DIR/etc/passwd")
[[ -n $redux_ids ]]
chown "$redux_ids" "$ROOTFS_DIR/captures/redux"
chmod 0700 "$ROOTFS_DIR/captures/redux" "$ROOTFS_DIR/captures/rauc"
