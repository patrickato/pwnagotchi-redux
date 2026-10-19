#!/bin/bash
set -euo pipefail
install -m 0644 files/boot/packs.py "$ROOTFS_DIR/opt/redux/boot/packs.py"
install -d "$ROOTFS_DIR/usr/local/bin"
printf '#!/bin/sh\nexport PYTHONPATH=/opt/redux\nexec /usr/bin/python3 /opt/redux/boot/packs.py "$@"\n' > "$ROOTFS_DIR/usr/local/bin/packs"
chmod 0755 "$ROOTFS_DIR/usr/local/bin/packs"

# Reviewed, data-only native catalog; no Python imports or automatic activation.
install -d "$ROOTFS_DIR/usr/share/redux/packs"
cp -a --no-preserve=ownership files/catalog/. "$ROOTFS_DIR/usr/share/redux/packs/"
chmod -R u=rwX,go=rX "$ROOTFS_DIR/usr/share/redux/packs"
# Record the final inventory after power/display, RAUC and packs dependencies.
on_chroot <<'EOF'
dpkg-query -W -f='${binary:Package}\t${Version}\n' > /usr/share/redux/packages.tsv
EOF
