#!/bin/bash
set -euo pipefail
install -m 0644 files/boot/packs.py "$ROOTFS_DIR/opt/redux/boot/packs.py"
install -d "$ROOTFS_DIR/usr/local/bin"
printf '#!/bin/sh\nexport PYTHONPATH=/opt/redux\nexec /usr/bin/python3 /opt/redux/boot/packs.py "$@"\n' > "$ROOTFS_DIR/usr/local/bin/packs"
chmod 0755 "$ROOTFS_DIR/usr/local/bin/packs"
