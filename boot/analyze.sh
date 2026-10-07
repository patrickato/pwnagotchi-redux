#!/bin/bash
set -euo pipefail
echo 'Measured board and boot (no estimated timings):'
cat /proc/device-tree/model
echo
uname -a
cat /proc/sys/kernel/random/boot_id
systemd-analyze --no-pager time
systemd-analyze --no-pager blame
systemd-analyze --no-pager critical-chain redux.service
systemctl show redux.service -p ActiveState -p ExecMainStartTimestampMonotonic
