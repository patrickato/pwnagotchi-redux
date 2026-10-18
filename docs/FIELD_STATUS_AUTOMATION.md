# One-command live status qualification

The operator no longer needs to paste individual Pi commands to diagnose
missing telemetry. Run from the existing Ubuntu WSL checkout:

```bash
cd ~/redux-workbench-src && git pull --ff-only && python3 scripts/field-status.py
```

The command contacts the previously provisioned Pi at `192.168.12.115`
using `~/.ssh/redux_field_ed25519` and an already trusted SSH host key.
Override with `--host` if DHCP changes the address. It executes a bounded
standard-library probe over the Pi's own **127.0.0.1:8080** status endpoint.
It uses no sudo, copies no files, installs nothing, never changes network
interfaces or captures, and never restarts services.

Its output has distinct PASS, FAIL, and UNKNOWN states. An old Pi image
without the newly coded telemetry is **UNKNOWN**, not an accidental success.
Temperature is accepted as observed only when it comes from a numeric status
value. Missing battery data is never fabricated. The full status JSON does
not leave the Pi; only allowlisted overall Doctor status, a few coarse area
severities, and numeric resource telemetry are sent to WSL. No finding
reasons, SSIDs, MACs, secrets, captures, caplets, or nearby network labels
are exported.

This command **does not deploy telemetry**. The read-only Pi overlay means
the device can run an older image while the workbench branch contains the
new code. RAUC is present but its slot and rollback configuration still
requires acceptance before using it for a release. Do not patch the Pi
root layer as a substitute.

Existing complementary commands:
- `python3 scripts/field-acceptance.py --host 192.168.12.115 --country US`
  collects the full bounded field report and tests radio/storage/engine gates.
- `python3 -m pytest -q tests/test_field_status.py` validates this tool offline.

These commands reduce **routine read-only evidence gathering** to a small
number of executions; changes that control hardware still require explicit
operator approval.
