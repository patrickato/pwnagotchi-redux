#!/bin/bash
set -euo pipefail
python3 files/boot/boot_policy.py "$ROOTFS_DIR"
install -D -m 0755 files/boot/analyze.sh "$ROOTFS_DIR/usr/local/bin/redux-boot-report"
# Generate unit masks from the same reason-bearing policy used in the manifest.
python3 - "$ROOTFS_DIR" <<'PY'
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location('policy', 'files/boot/boot_policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
units = Path(sys.argv[1]) / 'etc/systemd/system'
units.mkdir(parents=True, exist_ok=True)
for name in policy.MASK_UNITS:
    path = units / name
    if path.exists() or path.is_symlink():
        path.unlink()
    path.symlink_to('/dev/null')
PY
