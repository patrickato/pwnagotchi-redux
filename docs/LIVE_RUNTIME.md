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
redux live status   # compact, durable checkpoint on /captures
redux live doctor   # full, measured Doctor findings from localhost:8080
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
completed files can settle and convert. Logs are kept private; the supervisor enforces a configurable
Bettercap log-size budget during operation.

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

## Bounded Bettercap logs and REDUXCAP mount safety

A running Bettercap process keeps its private diagnostic log open. Redux now
checks that same append-only file descriptor each supervisor tick and truncates
the log when it reaches the configured `max_log_bytes` budget (4 MiB by
default, accepted range 64 KiB–64 MiB). This prevents unbounded growth
during **normal responsive supervisor operation** without restarting capture.
The rollover is intentionally lossy: diagnostic logs are expendable, unlike
saved captures and the SQLite sightings database. A sudden large write can
temporarily exceed the budget before the next tick; this is not a guaranteed
kernel-enforced byte quota.

Current and old log paths are checked against symlinks and hardlinks before
cleanup. The legacy `bettercap.log.previous` archive is reduced if it exceeds
the configured size when the engine starts. The live status checkpoint reports
`log_bytes`, `log_truncations`, and `log_error`; a failure to enforce the
limit appears as a separate degraded **engine logging** Doctor finding.

The baked image relies on a separately mounted REDUXCAP partition at
`/captures`. On that production layout, the runtime now refuses to start
if `/captures` is not mounted, *before creating any state or capture paths*.
If the mount disappears while running, Redux terminates its owned capture
process, does **not** hand its capture file to rootfs fallback directories,
and makes an in-memory Doctor report with capture storage marked
**ACTION REQUIRED**. It intentionally skips writing a checkpoint while the
mount is missing. Any closed capture that could not be handed off remains for
recovery on the next startup when its original partition is accessible.

Remounting does not automatically resume capture in the same process:
the prior owner lock refers to the original filesystem. After verifying the
mount and its contents, restart `redux-live.service` to reacquire that lock.
This protects against two capture owners writing to a newly mounted volume.

```sh
findmnt /captures
sudo systemctl status redux-live.service --no-pager
sudo systemctl restart redux-live.service    # only after remount/recovery
redux live doctor
```

For development installations using directories outside `/captures`, the
production mount requirement is not imposed; configuration still requires
distinct, compatible active/incoming filesystems.

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

The full set of real-time findings is also available through
`redux live doctor [--port 8080]`; it reads **only the loopback runtime**
and refuses to substitute an empty/synthetic Doctor if the listener is down.
Use `redux live status` to inspect the last durable checkpoint while offline.
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

## Capture-processing heartbeat and freshness

The periodic `redux-capture-ingest.timer` worker now writes one small
`pipeline_meta.last_scan` record **inside its existing `/captures/jobs.db`**
when a bounded pass finishes. The record includes a real completion timestamp,
number of examined files and that pass's outcome counts. It is also written for
empty passes and low-storage pauses. No extra daemon or independent polling
write process is introduced.

`redux-live.service` observes that ledger **read-only** and supplies the
result to both `/api/status` and the dashboard's **Capture processing** panel.
Doctor distinguishes:

- **UNKNOWN:** the ledger is missing or predates scan heartbeats; no
  completed worker scan has been verified
- **OK:** a scan was completed within the freshness budget, without a reported
  worker failure; this only confirms the worker executed, **not** that a
  handshake was found or that any hash was cryptographically verified
- **ATTENTION:** an invalid/future timestamp or earlier failed artifacts that
  remain on record
- **DEGRADED:** no completed scan inside the configured freshness budget,
  an invalid ledger record, or conversion errors in the latest scan
- **ACTION REQUIRED:** the latest worker pass paused to protect the storage
  free-space reserve

The live configuration defaults to `pipeline_database = "/captures/jobs.db"`
on the image (or a sibling `jobs.db` next to the configured incoming directory
for a source checkout). The default `pipeline_stale_seconds = 180` deliberately
allows for the timer's startup delay and processing time rather than making
the worker appear stalled immediately after boot. The live process never
starts a converter, edits worker records, or deletes raw capture files.

Use `redux live doctor` for the reason behind a processing alert,
`redux pipeline status` to inspect artifact counts, and
`journalctl -u redux-capture-ingest.service` to investigate a failed pass.
A crashed scan may leave no new heartbeat; its previously recorded timestamp
will eventually become overdue. No data is invented to cover that interval.

## Confirmed process shutdown and closed-capture handoff

The supervisor now treats stopping Bettercap as a **verified operation**,
not simply a successful call to `terminate()`. It waits for the child to
exit, escalates to `kill()` after a bounded timeout, and reaps it before
closing the shared diagnostic log or moving a captured file to the processing
queue. An already-exited child is also reaped.

If either process signal or the final wait fails, Redux enters
`termination_pending`, preserves the original child reference and active
capture, and **does not start a second engine or hand off that open file**.
Subsequent supervisor cycles retry stopping it. A successful retry only
returns to normal capture startup after the configured backoff. On service
shutdown an unreaped child makes Redux exit with an error instead of reporting
a successful stop. The packaged systemd unit explicitly specifies
`KillMode=control-group` so the service manager cleans up descendants in the
same unit before restart.

These guards prevent incorrectly publishing a still-open capture. They do
not guarantee an interrupted process wrote a valid, complete capture or that
the service can overcome kernel-level uninterruptible I/O. The original
`.pcap` remains in the active directory for recovery when handoff is unsafe.

## Bounded scan selection and dashboard query cost

Capture ingest no longer loads and sorts **every filename** in
`/captures/incoming` before processing a bounded pass. The worker streams
the directory and retains only the lexicographically smallest
`max_files_per_pass` candidates after a durable `cursor_path`. At the end,
it wraps to the beginning and fills the remainder of the batch, if needed.
The cursor is committed with the completed-scan heartbeat. If a worker
crashes mid-pass, the cursor does not advance, and the existing content-hash
ledger makes replay idempotent. Existing numeric cursors from older images
are ignored once during migration; no original captures are deleted.

This limits candidate-list memory to **O(K)** where K is the configured
`max_files_per_pass`, instead of O(N) for N capture filenames. It still
enumerates all directory entries in O(N) time, with O(N log K) selection CPU;
wraparound may require two streaming passes. This is not a constant-time
directory index, nor a reason to leave unlimited captures on one partition.
Capture retention remains separate and opt-in.

The live dashboard's channel counts and RSSI histogram are now aggregated
inside SQLite without constructing Python objects for every saved sighting.
The newest-access-point list uses SQL `ORDER BY ts DESC LIMIT 16` rather
than reading and re-sorting the entire table. The Augur status and Doctor
now **share one read-only worker-ledger snapshot per checkpoint**, eliminating
the duplicate processing-DB read while preserving the same on-screen facts.
These optimizations change query cost, not the number or authenticity of
observations saved.

## Responsive live telemetry and touch-first browser behavior

The supervisor always computes its process, storage, handoff and Doctor
diagnostics as live state, but now samples the heavier Augur visual/GeoDB
queries at a bounded cadence. `status_sample_seconds = 5` in
`/etc/redux/live.toml` is the default (supported range 1–30 seconds).
During a quiet interval, channel counts, RSSI bars, recent APs and mapped
sightings reuse the latest measured view. A new passive event makes that
view eligible for refresh after a **minimum two-second spacing**, rather
than recomputing thousands of stored observations on every high-rate event.
If the configured interval is shorter than two seconds, that shorter
interval is respected. Event counters and Doctor findings still refresh at
the normal supervisor checkpoint cadence.

`runtime.visual_sampled_utc` records the actual time of the successful
visual query, separate from `runtime.updated_utc` (the most recent live
supervisor checkpoint). `runtime.visual_revision` increases only when
a new visual sample has been computed. The dashboard redraws expensive
charts/maps only when that revision changes. This avoids falsely claiming
that unchanged visual telemetry is newly measured each second.

The browser never starts an overlapping `/api/status` request; each
request has a five-second abort deadline. On connection loss, HTTP errors
or a supervisor checkpoint that stops advancing for over 12 seconds, the
visible Doctor and capture-processing statuses change to **UNKNOWN · STALE**
rather than retaining old green readings. The previous details remain on
screen for troubleshooting but are explicitly labeled unverified.
When a fresh status arrives, severity and recovery advice are restored from
the actual runtime. A hidden browser tab suspends polling until visible,
reducing needless Pi and client work.

The browser has a narrower touch-oriented layout at widths up to 520px,
including shorter map presentation, responsive AP columns and larger tap
targets for essential controls. This is a code-level/browser behavioral
improvement, **not** a claim that the final 480×320 SPI TFT or its touch
controller has been qualified. All runtime values remain genuine
measurements; an unassessed area remains UNKNOWN.

## First interactive 480×320 workspace shell

The self-contained Augur dashboard now has **four operational, read-only
pages** instead of one tall collection of unrelated cards. On browser
viewports up to 520 pixels wide (including the target 480×320), the
navigation bar sits at the bottom with four 46px touch targets while only
the active page scrolls. Pages are:

- **Overview:** actual Augur face/persona, radio assignment and current
  recommendation, sightings/alerts and live narration
- **Radio:** measured location/airspace visualizations, channel/RSSI
  distributions, observed AP inventory and optional capability panels
- **Captures:** live capture lifecycle, the durable ingestion worker ledger,
  handoff count, pending sightings, and explicit handoff/error messages
- **Doctor:** health coverage, assessed findings, recovery advice and
  supervisor state/free space/log rotations/current error

The user can tap a navigation button, use left/right/Home/End keys while
the navigation bar is focused, or swipe horizontally on non-interactive
screen areas; vertical scroll gestures and swipes starting on buttons,
inputs or the map are left to the browser. The current page appears as a
shareable URL fragment (`#doctor` etc.) and is restored on refresh.
The page layout does not hardcode a fixed number of pages in its
JavaScript: new navigation buttons with `data-view="<id>"` and panels
with `data-page="<id>"` join the same registered navigation system.

All operational values come from the existing **read-only** live
`/api/status` payload. The interface does not pretend to start, stop
or reconfigure capture until an authenticated, scoped write API is
designed and tested. Missing status stays UNKNOWN; disconnected/stale
data loses any formerly healthy classification. Browser interaction
tests exercise keyboard/touch gestures, non-overlapping status requests,
visual sampling, and reconnection behavior using the shipped JS itself.

This is the first **functional touch-first navigation and information
architecture milestone**, not final art direction, not verified XPT2046
hardware gesture behavior, and not a replacement for an actual
480×320 rendered-image review on a physical Pi. The next gates remain
hardware framebuffer/touch validation, native deployment/browser shell
startup, screen tuning, theme/pack extension and scoped live controls.

## Release gates not yet completed

CI exercises the above with injected radios/transports/processes; it cannot
verify real RF driver compatibility, the Pi's kernel permissions, Bettercap
REST version behavior, genuine handshake conversion, display/touch layout,
high-load memory and storage recovery, or a fully built and flashed ARM64 image.

A flashable release therefore **must not be labeled tested on hardware** until
those checks have been performed.
