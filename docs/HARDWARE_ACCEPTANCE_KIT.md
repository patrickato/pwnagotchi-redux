# First-boot and hardware acceptance kit

For a single bounded, separately authorized read-only SSH bring-up session, use
the existing entrypoint's `session-plan`, `session-collect` and `session-inspect`
commands described in [BRINGUP_SESSION.md](BRINGUP_SESSION.md). Ordinary offline
acceptance modes never connect to a device.

`python3 scripts/redux-acceptance.py` is the host entrypoint. Preflight,
inspection, evaluation, comparison and replay work offline. They never SSH,
discover devices, install packages, invoke sudo, flash media, change Wi-Fi,
restart services or execute recovery. Pi 4/5 ARM64 are the supported targets.

## Offline workflow

```sh
# Existing extracted roots only; no mounts or image writes. Country is an
# operator selection for this release, not a command to change hardware.
python3 scripts/redux-acceptance.py --json preflight \
  --root /private/root-A --other-root /private/root-B --profile ab --country US

python3 scripts/redux-acceptance.py --json --report /tmp/replay-new.json \
  replay --scenario healthy
python3 scripts/redux-acceptance.py --json replay --scenario ethernet_framing
python3 scripts/redux-acceptance.py --json inspect --package /private/observations.json

python3 scripts/redux-acceptance.py --json --report /private/acceptance-new.json evaluate \
  --package /private/observations.json \
  --revision FULL_40_HEX_REVISION --release-sha256 IMAGE_64_HEX_SHA256 \
  --device-id DEVICE_64_HEX_PSEUDONYM --model pi4 --profile ab
```

For Lite omit `--other-root`. Use the exact source revision, model/profile and
compressed-image digest being assessed. A random stable device pseudonym, encoded
as 64 hex digits, identifies the device; never use a MAC, serial, SSID or a
reversible hash of one. Keep its private device mapping outside the repository.
An independently trusted image digest must come from a separate trusted channel,
not the adjacent checksum file.

Repeat `--gate radio --gate storage`, etc. to assess gates independently.
`compare --before OLD.json --after NEW.json` accepts the same target arguments
and evaluates both independently. It never merges observations across boots or
devices. `--now SECONDS` is a replay clock and disables physical provenance;
backdating evaluation cannot freshen a hardware attestation.

Reports contain only safe identity hashes, enums, counts, ages and fixed reasons.
Producer text, mount sources, packets, touch samples, SSIDs, MACs, keys and raw
exception/path text are not copied into acceptance reports. Keep input packages
private too; this is not a publishing tool for arbitrary legacy reports.
`--report` writes a new 0600 file, refusing links and overwrite.

Exit codes: **0** selected gates PASS/NOT APPLICABLE; **1** failure/invalid input;
**3** unknown evidence. Combined evaluation additionally requires executed source
and actual candidate qualification before PASS. Inspection and successful offline
preflight return 3: neither proves physical boot. Comparisons remain UNKNOWN
unless they report a failure. No gate selection alone establishes complete
acceptance, and `production_ready` always remains false.

## First-boot checks reuse the image contract

Preflight calls `image/root_payload.py`, also used by export and candidate
verification. Its effective service contract now verifies directive sections,
duplicate/reset overrides, unsupported drop-ins, ordering cycles, optional
radio/display dependencies, executable commands, recovery timer cadence,
ownership creation, capability sets and writable paths. A/B health/deadline units
use the same section checks. Uninterpreted local drop-ins fail closed.

The gate retains immutable overlay, separate REDUXCAP, masks, configuration,
runtime and installed-package checks. The worker now has a strict system sandbox,
only `/captures` writable and empty capability/ambient sets. It retains periodic
timer recovery and independence from radio, Bettercap and TFT. Live keeps its
NET_ADMIN/NET_RAW bounds and sole Bettercap ownership. Bootstrap still creates
redux-owned 0700 `/captures/boot`. No capture/radio action is introduced.

Runtime evidence is separate: active services do not prove a writable mount,
recovered worker, fresh sensor or reception. Missing startup/checkpoint/worker
prerequisites remain UNKNOWN. Incorrect mount identity, stopped runtime versus
active service, processing errors and incorrect framing are explicit failures.

## Bounded evidence contract

A package has `payload` and optional 128-hex Ed25519 `signature`. Payload fields:

- `format`: `redux-hardware-observations-v1`;
- `target`: `revision`, `release_sha256`, `device_id`, `model` (`pi4`/`pi5`),
  `profile` (`lite`/`ab`);
- `origin`: `physical`, `synthetic` or `historical`;
- `session_id`, `collector_sha256`: 64-hex scoped identities;
- `observed_at`: UTC Unix seconds; `observations`: at most 12 unique gates.

Each observation contains `gate`, the same `target`, pseudonymous 64-hex
`boot_id`, `observed_at` and gate-specific `data`. Ordinary single-boot records
must agree on boot identity; persistence/update traces explicitly identify their
separate boots. Parsing rejects duplicate JSON fields, nonfinite numbers,
links/hardlinks, files over 128 KiB, excessive nesting, wrong targets, unknown
fields and contradictory records. Inspection does not authenticate evidence.

Exact fields/limits live in `redux/hardware_acceptance.py`. Executable synthetic
examples are in `redux/acceptance_scenarios.py`. Gates are:

| Gate | Observations required |
| --- | --- |
| platform | Exact model/architecture; independently approved firmware and EEPROM hashes |
| storage | Kernel mounts, REDUXCAP identity, partition UUID/index/parent disk, A/B slot, ownership |
| services | Named systemd properties, lifecycle, valid fresh worker heartbeat |
| radio | Native ARPHRD/PCAP framing, advancing RX, validated decoded header counts, unchanged TX |
| processing | Closed handoff/conversion counts and scan outcomes; artifact integrity remains separate |
| display | Actual dimensions/rotation, physical corner/center samples, corresponding observed active UI coordinates and navigation |
| resources | Bounded measured-load series with complete temperature/load/RAM/frequency/flags |
| persistence | Different boots, unchanged nonempty inventory/storage identity, recovered services |
| update_install / trial_boot / rollback | Cross-boot backend transitions, independently authenticated bundle and preserved captures |
| recovery | Separately observed recovery medium/restored release, different boots, service observations and preserved captures |

Resource evaluation preserves Governor thresholds and requires a >=30-second
series including observed >=50% CPU utilization, complete sensors, a final sample
within the existing 10-second freshness limit, and no throttling/undervoltage
flags. Existing Governor survival temperature is a failure; high CPU utilization
by itself is measured load, not thermal failure. Unknown flag bits stay UNKNOWN.
This is not endurance, battery or power-supply certification. Missing
battery is never healthy. Firmware version strings alone do not establish EEPROM
or tryboot compatibility. Pending slots or a stable snapshot do not prove rollback.
Lite update gates are NOT APPLICABLE only without contradictory A/B observations.

A fittable touch calibration is only a candidate. Display PASS also requires
`observed_points`, the active UI's corresponding pixel outputs for the physical
corner/center targets, within 20 pixels of the targets. Missing outputs stay
UNKNOWN; a mirrored or incorrectly configured active mapping fails even when the
raw samples could be fitted to a different, correct candidate calibration.

## External authorization and attestation

Unsigned/self-authored JSON cannot produce hardware PASS. Synthetic/historical
origins cannot be promoted by signatures. `observation_status` is only a diagnostic
calculation; `status` includes provenance, freshness and authorization.

A separately reviewed attestor must authorize a collector and sign the measured
payload. Supply its external policy with `--trust-policy /external/session.json`.
Fields are `schema:1`, `target`, `session_id`, `collector_sha256`, `public_key`
(64-hex Ed25519 public key), `authorized_from`, `authorized_until`,
`max_age_seconds`, `gate_scope`, `firmware_sha256`, `eeprom_sha256` (approved hash
lists). Authorization lasts at most 24 hours and binds device, release, source,
model, profile, collector, session and gates. Update/failure testing needs separate
operator authorization. Policy is never taken from the package itself.

Signing uses `redux.hardware_acceptance.canonical(payload)` bytes. No production
keys or signing authority are generated by the kit; credentials stay with the
external attestor. Signatures authenticate **who attested bytes**, not physics.
The operator must independently verify collector execution, acquisition method,
device identity and actual observations. A dishonest trusted attestor can lie;
software cannot solve that trust problem. Without independent review, do not
supply a trust policy or call a signed assertion physical acceptance. PASS never
causes deployment, certification or an update.

## Explicit local acquisition: future authorization required

**Do not run this on the Pi without separate approval.** This sprint does not
deploy the toolkit. Full reviewed source/dependencies must first be made available
through a separately approved process; existing installed images need not contain
the new host CLI or its `image/` and `boot/` helper modules.

`collect-local` accepts the target arguments, `--session-id SESSION_64_HEX` and
optional `--sample-seconds 2..60`. It verifies local model/embedded revision and
consumes existing cached loopback telemetry/worker data; it creates no duplicate
sensor collector, database scan or load generator. Fixed loopback requests disable
proxies/redirects, time out after two seconds and read at most 64 KiB. Protected or
absent status leaves measurements unavailable. Four named unit queries reuse the
existing read-only probe. Storage inspection reads mounts, udev/sysfs identities
and ownership without opening block contents.

Collection makes at most 31 cached reads, caps sampling at 60 seconds plus bounded
query time, deduplicates sensor timestamps, detects source/boot changes and strips
arbitrary producer text. Independent producer failures leave coverage UNKNOWN.
It deliberately does not collect firmware approvals, RF frames, capture contents,
touch events, persistence inventories or update outcomes. No RF, converter,
calibration, reboot, installation or failure experiment runs.

Output is unsigned JSON on **stdout only**, exit 3. `--report` is refused in this
mode. It writes no evidence, bytecode, receipts or configuration on the Pi. Any
approved transport and host-side saving must be explicitly arranged; no SSH is
embedded. Default/short sampling cannot establish a 30-second load trial, and
missing load/sensors remains UNKNOWN.

## Source and release integration

`evaluate --qualify-source` executes the existing complete `redux-qualify.py
--beta` runner against the exact clean revision, checked before and after. It does
not import saved “tests passed” JSON or duplicate test discovery.
Executed test totals and redacted gate failures are retained in the source result.
Raw tracebacks stay out of public reports. The runner inherits `PYTEST_ADDOPTS`;
if selecting ext4 scratch for large image fixtures, use a fresh **short** basetemp
path (for example an ignored `build/s6q/pytest`). Linux Unix-domain socket tests
cannot bind paths over the kernel socket-path limit. An overlong custom path is
reported as FAIL with a diagnostic hint; no tests are skipped or failures masked.
`--candidate-dir DIR` executes factory artifact verification; A/B requires
`--keyring /external/trusted-public-keyring.pem`. `--trusted-image-sha256` must be
independently trusted. `--scratch-dir` selects existing inspection storage.

Update observations cannot PASS without a freshly authenticated bundle matching
the trace digest. The beta matrix keeps independent physical RF/display and
actual-update/rollback UNKNOWN rows, with the attestation matrix attached
alongside. Synthetic images never establish physical success.
`--recovery-root SAVED_ROOT` reuses offline recovery planning and emits only
redacted phase/status; it never executes recovery.

## Next approved physical session

1. Qualify a genuine image with [RELEASE_FACTORY.md](RELEASE_FACTORY.md); record
   exact source/release identity and independently trusted signing evidence.
2. Obtain bounded authorization naming the device/model, acquisition methods,
   passive RF/touch work and any invasive update/reboot sequence. Scope capture
   preservation and recovery separately. Neither this document nor a policy file
   grants permission to perform physical actions.
3. Review collector/attestor identity; acquire read-only startup observations first.
   Old v1 reports lack authenticated session/timestamp provenance and cannot be
   silently upgraded. Keep originals private and save redacted reports separately.
4. Evaluate each gate and compare before/after evidence. Obtain missing observations
   only through separately approved experiments; preserve original media/captures.
5. Repeat source/candidate gates and review the combined matrix. Pi 4/5 RF/display,
   reboot persistence, signed installation, failed-trial rollback and recovery
   remain open until independently witnessed and recorded.
