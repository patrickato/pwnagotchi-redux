#!/usr/bin/env bash
# Disposable Redux Pi 4 field repair: avoid Bettercap stdin EOF shutdown.
# Apply ONLY to the separate test SD, over SSH from the operator's laptop.
# Modifies the writable RAM overlay of /opt/redux; does not reflash, remount,
# touch /captures packet data, or change the working Jayofelony SD card.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run over SSH with sudo -n, see docs/FIELD_TEST.md." >&2
  exit 2
fi
runtime=/opt/redux/redux/core/live_runtime.py
if [[ ! -f $runtime || ! -f /etc/redux/live.toml || ! -d /captures/live ]]; then
  echo "Not the expected booted Redux field image; no changes made." >&2
  exit 3
fi
if ! grep -Eq '^[[:space:]]*state_dir[[:space:]]*=[[:space:]]*"/captures/live"' /etc/redux/live.toml; then
  echo "Unexpected live state configuration; refusing to proceed." >&2
  exit 3
fi

# Validate the exact known first-boot source before stopping any service.
python3 - "$runtime" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
old = "stdin=subprocess.DEVNULL, stdout=self.log_handle, stderr=subprocess.STDOUT,"
new = "stdin=subprocess.PIPE, stdout=self.log_handle, stderr=subprocess.STDOUT,"
if source.count(old) != 1 and source.count(new) != 1:
    raise SystemExit("Unrecognized engine stdin source; refusing patch")
if "def _stop_engine_child" not in source or "def _drop(self)" not in source:
    raise SystemExit("Unrecognized runtime process ownership; refusing patch")
print("Known Redux supervisor source verified.")
PY

systemctl stop redux-live.service
python3 - "$runtime" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = p.read_text()
old = "stdin=subprocess.DEVNULL, stdout=self.log_handle, stderr=subprocess.STDOUT,"
new = "stdin=subprocess.PIPE, stdout=self.log_handle, stderr=subprocess.STDOUT,"
if old in source:
    source = source.replace(old, new, 1)
    # Close the parent's pipe after reaping the engine. Do not close early,
    # which would cause the exact EOF prompt this repair is addressing.
    anchor = "        self.process = None\n        return True\n\n    def _drop(self):"
    replacement = (
        "        input_pipe = getattr(child, \"stdin\", None)\n"
        "        if input_pipe is not None:\n"
        "            try:\n"
        "                input_pipe.close()\n"
        "            except OSError:\n"
        "                pass\n"
        "        self.process = None\n"
        "        return True\n\n"
        "    def _drop(self):")
    if source.count(anchor) != 1:
        raise SystemExit("Cannot locate safe child reap anchor; no changes made")
    source = source.replace(anchor, replacement, 1)
    p.write_text(source)
    print("Temporary noninteractive Bettercap pipe fix applied.")
else:
    print("Bettercap pipe fix already present.")
PY
python3 -m py_compile "$runtime"
systemctl restart redux-live.service
sleep 16
echo
echo '== Updated Redux field report =='
redux-report --full
echo
echo 'Field changes remain in this boot only; no reflash was performed.'
