# Live passive Redux runtime (active Redux workbench)

## What starts on a built image

Two independent systemd units now have clear, separate jobs:

- `redux.service` continues the boot checkpoint and hardware watchdog loop;
- `redux-live.service` owns **one** Bettercap child and the real Augur
  event pump. It discovers the available radios, selects a monitor-capable
  interface using Redux's existing Radio Orchestrator, configures that
  interface to monitor mode if required, and starts Bettercap with a private
  generated caplet. It never launches active radio commands.
- `redux-capture-ingest.timer` processes completed capture files into
  `.hc22000` artifacts in the background, independently from radio timing.
- `redux-capture-audit.timer` remains disabled and requires an explicit
  configured test profile and a usable on-Pi Hashcat backend.

The image's writable REDUXCAP filesystem is `/captures`. The runtime stores
its private caplet, process log, GeoDB, and status in `/captures/redux`;
Bettercap stores its current aggregate handshake file in `/captures/active`.
Only after that process exits is a nonempty, closed file synced, hard-linked
into `/captures/incoming` without clobbering an existing name, and removed
from the active directory. A power interruption between the link and removal
is safe to resume: Redux recognizes the same inode on both names.
The file processor watches only the incoming directory
and preserves its own database in `/captures/jobs.db`.

This architecture does **not** depend on Jayofelony Pwnagotchi, and the
original main branch remains untouched.

## Why the capture owner is separate

It uses the actual `radio.probe`, `radio.decide`, `BettercapDriver`,
`Supervisor`, `SignalBus`, `Augur.pump`, and `SightingStore`
implementations. No stub radios are used. Existing live radio capture examples
work by explicitly selecting an interface; the runtime now does so automatically
and retries when the adapter appears.

The generated caplet sets `wifi.txpower 0` so the passive runtime does not
request a transmit-power change. On-device regulatory-domain checks remain a
physical acceptance gate; Redux does not assume a country code.

The Bettercap REST listener binds only to `127.0.0.1` on port 8081 with
a randomly generated password. The caplet is private (mode 0600).
The web dashboard binds to **localhost only**, on port 8080, and reads
cached Augur snapshots without touching SQLite from its HTTP thread.
**It starts independently of Bettercap/radio readiness**: a missing Wi-Fi
adapter or failed Bettercap session no longer makes the local diagnostic
dashboard disappear. If port 8080 is in use, Redux reports a dashboard error
while keeping the radio runtime running and retries the listener at a bounded
30-second cadence; it also detects a dead dashboard server thread.

The dashboard now includes a read-only **Doctor** panel with severity,
coverage gaps and actionable remediation drawn from real Augur state plus
the live process, storage, capture-handoff and listener observations.
A missing hardware reading is shown as **UNKNOWN**, never silently passed.
The local JSON route `/api/status` exposes that same Doctor report.
`/captures/redux/live.json` stores a small checkpoint with health severity,
unassessed-area count and dashboard availability, not fictitious telemetry.

This does not yet offer authenticated LAN access to the dashboard.
Do not expose the Bettercap REST port or share its caplet or private files.

## How to inspect a booted test image

This is for the future **separate development SD card** only, not the current
Jayofelony installation.

```bash
sudo systemctl status redux.service redux-live.service redux-capture-ingest.timer --no-pager
sudo env PYTHONPATH=/opt/redux python3 -m redux.core.live_runtime --config /etc/redux/live.toml --check
sudo journalctl -u redux-live.service -n 80 --no-pager
sudo cat /captures/redux/live.json
iw dev
mount | grep ' /captures '
find /captures/incoming -maxdepth 1 -type f -name '*.pcap' -printf '%f %s bytes\n'
sudo env PYTHONPATH=/opt/redux python3 -m redux.crack.ingest --config /etc/redux/pipeline.toml --once
curl -fsS http://127.0.0.1:8080/api/status
```

For complete basic diagnostics, `sudo redux-live-diag`
from this source tree is also provided. It intentionally does not print
the REST password, hash material, or the private caplet.

For live troubleshooting, report the diagnostic output, `bettercap -version`,
`iw dev`, `iw phy`, and the installed converter version (not captures
containing real private data). Valid conversion must be tested on a
controlled sample of the owner's own AP.

## Recovery and limitations

When there is no safe monitor-capable adapter, the service reports `degraded`
and retries; no phantom devices are reported. Recovery scanning tolerates
intermittent directory-enumeration and per-file I/O faults without terminating
the radio supervisor; failed handoffs remain visible in Doctor and the status
snapshot. No automatic capture deletion occurs. When Bettercap exits or the
radio disappears, the child is reaped, the SQLite store is closed, and
the supervisor retries. Each restart creates a *new* capture file so
completed files can settle and convert. Logs are kept private; startup
rotates the Bettercap log at 4 MiB.

The service prefers an existing monitor virtual interface on each physical
radio and ignores PHY devices that lack a usable network interface. With
multiple capture-capable radios, it skips an active connected uplink and uses
a free alternative. By default it refuses
to switch a currently connected Wi-Fi uplink into monitor mode (so SSH/internet
connections are not unexpectedly disconnected). Operators using a dedicated
capture device can override this behavior with `allow_connected_capture = true`
in `/etc/redux/live.toml`; the override explicitly permits disconnecting that
interface. Network managers that independently own the same interface may
still interfere and will require configuration based on the physical Pi.

## Sighting data integrity and SQLite recovery

Augur batches passive radio observations before writing to the SQLite spatial
database. A batch is now removed from memory **only after the entire SQLite
transaction commits**. SQLite rolls back a failed batch, including validation
errors midway through that batch, so later retries do not inherit partial writes.

If an on-device SQLite operation temporarily fails, the live supervisor enters
`telemetry_degraded`, leaves the Bettercap child running, and holds the
uncommitted observations for a bounded retry after `retry_seconds`. It does
not continue fetching new event batches while the previously received records
cannot be persisted, avoiding unbounded accumulation in Python. When the
database recovers, the saved batch is committed before normal pumping resumes.

The live `/api/status` snapshot and the localhost Doctor page report:
- `runtime.sightings_pending`: observations still awaiting a database commit;
- `runtime.sightings_write_failures` / `sightings_write_error`: observed
  failures, not assumptions that the database is healthy;
- `runtime.sightings_lost_on_restart`: explicit count of buffered records that
  could not be saved even on the final shutdown/rotation flush;
- Doctor's **sighting persistence** finding, with a severity and suggested
  operator action. A connected engine is not proof its SQLite writes succeeded.

This does **not** promise lossless recording during long storage failures.
Bettercap has its own finite event retention, and abrupt power loss can destroy
uncommitted RAM buffers. The original queued capture files are not automatically
deleted or treated as a full event replay. Always treat nonzero
`sightings_lost_on_restart` as a real loss indication, not something Redux
quietly repaired. The database's existing `prune` operations are now atomic;
this work does **not** enable automatic sighting pruning or remove observations
behind the operator's back.

## Release gates not yet completed

CI exercises the above with injected radios/transports/processes; it cannot
verify real RF driver compatibility, the Pi's kernel permissions, Bettercap
REST version behavior, genuine handshake conversion, display/touch layout,
high-load memory and storage recovery, or a fully built and flashed ARM64 image.

A flashable release therefore **must not be labeled tested on hardware** until
those checks have been performed.
