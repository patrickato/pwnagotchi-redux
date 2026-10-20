# Pi 4 bring-up session: observation before changes

Sprint 08 starts at `e7a116d7dc1c4e96957c888831f4870878794af0`. The
`b6ed0aa` WAL connector test correction reproduces successfully. Baseline unified
qualification executed 3,133 passing Python tests and 1,049 passing image tests;
compilation, shell syntax and whitespace passed. These are host-only results.
There is no new physical observation or disposable-card identification in them.

## One offline plan, one explicitly approved read-only session

The existing acceptance entrypoint now organizes the existing probes:

```
python3 scripts/redux-acceptance.py --json session-plan
```

Exit 3 is intentional: this prints the exact scope and SHA-256 of the in-memory
collector without connecting, discovering a device, using an SSH identity,
installing, or qualifying hardware. Source files in the bundle are the existing
read-only radio/ownership collector, mount validator, display inventory and cached
status sanitizer. There is no second acceptance evaluator. Existing signed
hardware-package evaluation and genuine candidate verification remain the
qualification authorities.

Before any SSH, obtain explicit operator authorization for this scope and an
independently verified Pi IPv4 address. Confirm Ethernet management, the existing
trusted host key and which physical card is booted. A historical IP is not proof.
The exact command, **only after that authorization**, is:

```
python3 scripts/redux-acceptance.py session-collect \
  --host VERIFIED_PRIVATE_IPV4 --key ~/.ssh/redux_field_ed25519 \
  --card-role disposable-redux --output /private/new-session.json \
  --authorize-read-only
python3 scripts/redux-acceptance.py session-inspect \
  --session-file /private/new-session.json
```

The output parent must already exist; choose a new private local filename. The
card role is an operator declaration, never independently measured card identity
and never permission to overwrite a card. Use `unverified` when unsure. No part
of this tool flashes or changes a device.

One SSH process has a 45-second outer deadline and 128-KiB combined output bound.
The existing collector has a 35-second shared command budget and short command
deadlines. Public-key-only BatchMode, strict known-host checking and the existing
identity are mandatory. SSH configuration/proxies/forwarding/connection reuse and
host-key updates are disabled. Unknown or changed keys stop the session. There
is no password fallback, sudo, install, retry, automatic discovery or Pi file.
Source executes in process memory through the existing memory loader; `-B`
prevents bytecode writes. OS SSH authentication/audit machinery may record the
connection normally; the tool does not request persistent device artifacts.

The approved scope is:

- Read the embedded revision, model, architecture, kernel and boot pseudonym.
  Hash bounded `vcgencmd version` and `bootloader_version` outputs. These are
  version-output identities, **not firmware binary hashes or compatibility proof**.
- Reuse one radio/ownership/regulatory/service inventory; read two sysfs RX/TX
  counter snapshots with a one-second interval, preserving interface generation
  and actual ARPHRD. No packet socket, scan, retune or mode change.
- Reuse mount parsing/REDUXCAP identity checks; report kernel device major/minor
  and capacity only for a verified captures mount. Root filesystem space cannot
  stand in for a missing capture partition.
- Read cached dashboard JSON once for Doctor, telemetry, runtime handoff counters,
  worker aggregate outcomes and plugin-inventory availability. No ledger scan,
  artifact hashing, capture-content opening or path-based existence assertion.
- Reuse sysfs framebuffer/SPI/touch metadata inventory. No pixels, input events,
  calibration, display configuration or physical touch certification.

Sensitive network identifiers, serials, raw logs, captures, credentials and
configuration contents are excluded. Only the private host report is written
(mode 0600, no overwrite). Missing permissions, old producers and unavailable
sensors remain unavailable. Do not rerun as root merely to fill unknowns.

`session-inspect` reads bounded saved JSON offline. It reports saved observation
checks and exact embedded versus host revision separately. Importing a report
cannot authenticate its assertions; the physical acceptance, release identity,
card identity and update/rollback gates always remain UNKNOWN. A successful SSH
connection is not hardware acceptance. Origin/collector hashes do not substitute
for the external trust policy used by the existing hardware evaluator.

## Current open Pi 4 gates

| Gate | Current session evidence | Next measurement/acceptance requirement |
| --- | --- | --- |
| Running image and firmware | UNKNOWN | Approved read-only session; record embedded revision independently of host revision |
| Boot/overlay/REDUXCAP | UNKNOWN | Current mount/label identity and capacity; later approved reboot/preservation test |
| Native RF framing | UNKNOWN | Selected interface actual ARPHRD; historical Ethernet type 1 is not current measurement |
| Physical passive reception | UNKNOWN | Counter deltas alone do not prove decoded 802.11; separate passive reception authorization |
| Handoff/conversion | UNKNOWN | Fresh aggregate evidence first; valid closed-file conversion requires separately scoped evidence |
| TFT/touch | UNKNOWN | Metadata first; physical pixels, mapping and navigation require later operator testing |
| Thermals/resources | UNKNOWN | Existing timestamped producer values; missing battery/power cannot be healthy |
| Plugin inventory | UNKNOWN | Older image may not expose Sprint 07; no install/restart is part of this session |
| Update/rollback | UNKNOWN | Signed genuine A/B candidate, correct backend/layout and separately approved trial/failure/recovery |

If an older image is running, observations apply to its embedded revision only.
No source update, service restart, overlay change, radio mutation or speculative
Pi fix is authorized by this document or by possession of the SD card. Prepare
and test a concrete change plus bounded restoration before requesting approval.

## SD transition and genuine Lite candidate

1. Physically identify and label the disposable Redux card. Keep the existing
   Jayofelony/Pwnagotchi card removed and protected. Until the operator identifies
   the card and source block device, card identity stays UNKNOWN.
2. Preserve the redacted session report on the host. Copying private installed
   configuration or a complete card backup is a different operation and may
   contain secrets/captures: obtain that scope, store encrypted outside Git and
   never publish it. A full read-back backup needs a separately approved safe
   shutdown/card removal, a positively identified host source device, adequate
   space, a new output file, hash and read-back verification. No such backup has
   been made by this sprint.
3. Use the existing release factory from an exact clean revision on a qualified
   build host. Select country explicitly for the intended location:

   ```
   python3 scripts/redux-release.py --json factory preflight \
     --revision FULL_COMMITTED_SHA --profile lite --country OPERATOR_COUNTRY \
     --workspace "$PWD/build/rc-work-NEW" --out "$PWD/build/rc-lite-NEW"
   ```

   Preflight is read-only. Use `factory build` with the same inputs only in an
   explicitly provided privileged host build session. Never invoke sudo
   implicitly. Reuse factory `inspect`/`verify` and the existing `candidate`
   gate for the actual finished compressed image, digest, runtime payload and
   partition contract; tests and synthetic images are not release artifacts.
4. Host preflight in this session measured UID 1000, about 834 GiB free on the
   WSL ext4 repository filesystem and qualified installed build dependencies.
   Its only reported blocker was `privilege`: full image assembly requires
   root. No build was attempted and no genuine candidate was created. Production
   A/B signing credentials were not supplied; no private key was generated.
5. Flashing needs separate approval identifying the disposable destination,
   verified backup and image digest, with the Jayofelony card excluded. No
   flashing, resizing, partitioning or shutdown/reboot is part of read-only SSH.
6. First boot requires separately approved installation/boot and known-host
   verification through an independent channel if identity changes. Repeat
   current-source metadata checks before physical TFT/radio tests. Each radio
   mutation or reception experiment needs an explicit scope, stop conditions and
   bounded cleanup; no previous approval carries forward.
7. Lite does not establish A/B rollback. Later A/B work requires a genuine signed
   candidate, compatible bootloader/storage/backend, capture preservation proof,
   and explicit approval for trial boot, intentional failure, rollback and
   recovery. RAUC being installed is insufficient.

See [FIELD_ACCEPTANCE.md](FIELD_ACCEPTANCE.md),
[HARDWARE_ACCEPTANCE_KIT.md](HARDWARE_ACCEPTANCE_KIT.md),
[RELEASE_FACTORY.md](RELEASE_FACTORY.md) and [BETA_HANDOFF.md](BETA_HANDOFF.md).

## Tested host handoff

After integrating the capture-integrity owner's concurrent commits through
`49c3247`, final qualification passed 3,166 Python tests and 1,076 image tests,
with zero failures or skips (one existing PyGI deprecation warning). The focused
session/read-only/hardware-acquisition selection passed 153 tests, including 27
new deterministic session cases. Unified qualification passed all 24 checks with
exit 0; compilation, shell syntax and whitespace passed.

All session hardware data exercised by those tests is synthetic. No physical
session is implied. The operator's explicit read-only authorization, verified
Ethernet Pi address and card identification remain prerequisites for acquisition.
Continue with the offline plan, approve the documented exact scope, execute once,
inspect the saved report and choose the next operation from actual evidence.
Do not turn missing measurements into a speculative Pi repair or a root retry.

## October 20, 2026: collector bundle dependency correction

The Pi 4 Rev 1.5 at the previously used Ethernet address responded to a
strict-host-key, public-key-only SSH request. The returned hardware model is
direct physical metadata; it does not certify the booted card or Redux image.
The original memory-only `session-collect` failed without saving a JSON
session. Inspection of the bundler found that `redux.display.kiosk` imports
`redux.display.local_http`, but the memory module bundle omitted that module.
An older installed image cannot supply a missing dependency because the in-memory
package namespace intentionally hides unbundled installed sources.

The host bundler now includes the missing dependency, and the
`test_extended_memory_bundle_imports_without_installed_source` regression
imports the boot-readiness and display modules under isolated Python `-I`
to catch this transitive dependency. This correction was made entirely in
the host repository; no Pi file, config or service was changed. Run the
offline regression after `git pull --ff-only` and review its result before
conducting a new separately scoped read-only collection. Use a fresh
report filename; do not inspect an unsaved report or change SSH host keys.

## October 20: safe collector failure differentiation

Strict SSH authentication to the Pi 4 Rev 1.5 at the established host address
succeeded, but two attempts at `session-collect` returned generic UNKNOWN and
no report. A prior isolated test exercised memory bundle *imports* but not the
entrypoint's real execution path. A later host-only correction adds a bounded
remote exception **class-only** error envelope and distinguishes known SSH
transport failures from remote Python exceptions. It never transmits raw stderr,
exception messages, paths, filenames, packet captures or credentials. Failure
reports remain UNKNOWN and are not saved as physical evidence.

The new offline test
`test_full_memory_collector_executes_without_installed_redux` launches the
complete bundled entrypoint in an isolated Linux interpreter with a synthetic
SSH connection header and no remote host, to find import-time and call-time
problems before any hardware retry. Additional tests cover the redacted
diagnostic envelope and a failed SSH process.

After pulling, run this offline focused test first:

```sh
python3 -m pytest -q \
  tests/test_acceptance_session.py::test_full_memory_collector_executes_without_installed_redux \
  tests/test_acceptance_session.py::test_remote_collector_failure_is_classified_without_raw_error \
  tests/test_acceptance_session.py::test_failed_ssh_process_is_distinct_from_remote_python_fault
```

Only after passing tests and reviewing the approved read-only scope should an
operator retry with a **new** host report filename. A remote collector failure
now displays a fixed class such as `remote_ModuleNotFoundError` rather than
falsely suggesting an SSH-key failure. A failure result never qualifies hardware.
