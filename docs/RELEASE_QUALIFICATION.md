# Offline release, update and recovery qualification

Sprint 02 adds `scripts/redux-release.py`, a read-only host command with three
modes. It never connects to a Pi, opens block devices, mounts a filesystem,
installs an update, executes guest hooks, changes a selector or requests reboot.
Only private, disposable WSL temporary files are written. No signing keys ship
in the repository. Test signing keys are generated inside pytest temporary
folders and are never production credentials.

## Candidate gate

```bash
python3 scripts/redux-release.py candidate \
  --image /path/redux.img.xz --profile lite \
  --revision <full-source-SHA> --country <operator-selected-country> \
  --trusted-sha256 <digest-from-an-independent-trusted-channel>
```

For an A/B candidate add `--profile ab --bundle /path/update.raucb
--keyring /path/trusted-public-certificates.pem`, and optionally `--version` to
pin its human-readable release version. Both artifacts must identify the same
full source revision. Use `--json` **before** the mode for machine output.
A missing bundle/keyring, missing independent image digest or unavailable parser
is UNKNOWN, never an authenticity PASS. Lite updates are deliberately masked;
presenting an A/B bundle for Lite is FAIL, not a supported single-slot update.

The gate reuses `verify_release.verify`, `release_manifest.inspect_image`,
`root_payload.verify`, `boot_payload.verify`, the actual OTA configuration
renderer/context validator and the actual backend transition policy. It checks:

- Actual xz bytes and SHA256, exact companion manifest/checksum, selected source
  revision/profile, primary and backup GPT or MBR, partition bounds/filesystem
  signatures/labels and required A/B type GUIDs. The stream has a 64-GiB expanded
  size budget; malformed compressed input cannot expand without a limit.
- Actual embedded runtime files, configuration modes, enabled/masked services,
  RAM overlay policy, separate REDUXCAP paths, passive capture policy and the
  existing disabled-by-default unqualified USB/PAN/audit paths.
- Actual Pi 4/5 boot files, paired A/B kernel root PARTUUIDs and factory selector,
  using the existing boot contract. File presence is not execution evidence.
- Both A/B roots' public update keyring, generated configuration policy, paired
  boot/root slots, generator registration, exact helper/backend code and bounded
  health/deadline/watchdog configuration. Other bootloaders/backends are unsupported.
- RAUC's certificate verification **and full verity payload verification** of
  a supplied bundle. A restricted SquashFS inventory confines RAUC extraction
  to four regular filenames before any files are extracted. Hooks never execute.
  Every tar member is inspected for traversal, duplicates, special files, link
  ancestors and protected captures/control content. Only allowlisted runtime
  files are materialized, without `tar.extract`/`extractall` or device creation.
  Signed build identity, archive size/digests, the fixed hook wrapper and selected
  runtime/boot payloads must match the measured release image.

Artifact and public trust inputs are snapshotted from bounded, no-follow regular
files. Tools reopen only those private snapshots. Source changes, unsafe links,
contradictory metadata, wrong signer/build, interrupted bundles and malformed
archives fail closed. Capture partitions are never materialized, enumerated or exported; the compressed
release input is snapshotted as a whole, then only selected boot/root partitions are copied, and allowlisted content is read using
read-only debugfs and FAT tooling. No guest binaries or shell commands from an
artifact execute. External parser behavior and temporary free space are checked;
tool failures/deadlines are failures, not a fabricated empty filesystem.

Output contains distinct PASS/FAIL/UNKNOWN checks and `offline_qualified`.
A fully satisfied software candidate has `offline_qualified: true` while the
physical check remains UNKNOWN. `install_permitted`, `rollback_permitted`,
`automatic_rollback_verified` and `physical_boot_verified` remain false. This
command is an evidence gate, not an installer or operator authorization gesture.
Exit codes: 0 all checks PASS; 1 an observed failure; 2 invalid CLI/input-context
arguments; 3 UNKNOWN without failures. In particular a valid offline candidate
still exits 3 because physical acceptance is open; automation must distinguish
that result from FAIL and may inspect `offline_qualified` for source readiness.

## Configuration readiness

```bash
python3 scripts/redux-release.py --json rauc \
  --root /path/to/read-only-root-snapshot --revision <full-source-SHA> \
  --context /path/to/saved-context.json
```

The root must be a local extracted snapshot, not a mounted physical Pi. Context
uses the existing `{current, devices}` schema with six distinct canonical
PARTUUID paths. No device discovery fills gaps. For factory roots whose
`/etc/rauc/system.conf` is the intentional `/run/redux-ota/system.conf` link,
the validated installed generator and the explicit context establish the static
configuration policy; they do not claim the generated runtime file exists.
If a saved generated file exists, it must exactly match that policy.

Conditions are explicit: **unconfigured** (RAUC masked/no slots), **unsupported**
(other backend or an installed backend differing from the inspected policy),
**unverified** (incomplete context/evidence), and **configured** (supported static
policy verified). Contradictory masks, missing slots/registration, changed
helpers/services, disabled watchdog policy, invalid/expired keyrings or wrong
release identity FAIL. Configured always retains the physical rollback UNKNOWN.
A development image with PID 1's hardware watchdog disabled cannot pass update
readiness merely because RAUC is installed.

## Recovery planning

```bash
python3 scripts/redux-release.py recovery --root /path/to/saved-system-snapshot
```

The saved selector, kernel cmdline, canonical runtime context and private OTA
ledger must be present and structurally consistent. Missing ledgers are not
silently treated as proof of a factory state. Add `--trial` only for an
independently recorded tryboot observation, not because a pending slot exists.
This remains an offline observation supplied by the operator, not live proof.

Plans distinguish stable state, candidate trial, interrupted selector commit,
staged-or-fallback ambiguity and blocked/contradictory evidence. An A boot with
pending B cannot prove whether B was merely staged or actually rolled back.
Interrupted-commit reconciliation is simulated through the existing transition
function and never persisted. Plans preserve the committed selector/captures
and give descriptions, not executable recovery commands. Snapshot age, running
health, fallback boot and inactive-slot integrity remain unverified.

## Host tools and repeatability

The ordinary `python3 scripts/redux-qualify.py` preflight now reports release
tools separately; no script installs them. Ubuntu release-test packages are
`rauc mtools e2fsprogs dosfstools squashfs-tools`; Python needs the existing
`.[dev,crypto]` extras. RAUC >=1.10 with authenticated `json-2` metadata is the
supported host inspection interface; integration is exercised with RAUC 1.15.1.
OpenSSL/RAUC time-valid trust certificates are required. Keep private signing
keys outside the checkout and image; `make-bundle.sh` now takes the canonical
build revision from the mounted root and includes it in signed metadata.
Legacy bundles without that identity require rebuilding/re-signing, not bypassing
verification. The backend preinstall policy also rejects missing build/version,
extra handlers/hooks, path redirection and non-verity formats.

`image/ci.sh` requires these tools and runs the release/update tests alongside
its existing contracts. GitHub's ephemeral CI installs the host tools and crypto
extra. Pure policy tests run without them; real-artifact integration tests have
explicit missing-tool skip reasons in a basic pytest invocation. Complete image
CI fails preflight if tools are absent. A skipped optional parser test is not
release evidence. Python caches remain in the existing owner-writable temporary
locations; no sudo or global Python installation is performed by these commands.

## Remaining physical acceptance

No physical release/update/recovery is qualified by offline tests. Pi 4 and Pi 5
still require observed compatible EEPROM GPT/tryboot support, provisioned and
tested BOOT_WATCHDOG_TIMEOUT, successful A→B→A signed trials, rejection before
slot writes, candidate service/kernel/root failure resets, interrupted installs,
selector FAT power-loss behavior and capture preservation. Bootloader fallback,
loaded service policy, immutable mount behavior and display/radio operation must
be measured independently on an explicitly approved disposable medium. There is
no automatic repair/reflash/rollback execution in the new framework, and no
monotonic anti-downgrade counter is claimed.


## Verified Sprint 02 source run — 2026-10-18

The combined workbench based on `9a3246f` passed these local WSL checks:

| Command/check | Result | Exit |
| --- | --- | --- |
| `python3 scripts/redux-qualify.py` | 24 PASS, 0 FAIL, 0 UNKNOWN | 0 |
| `python3 -m pytest -q` | 2,757 passed, no skips | 0 |
| `bash image/ci.sh` | 816 passed, no skips | 0 |
| Targeted update/release/OTA-state/audit/build regressions | 253 passed | 0 |
| Python compile, tracked shell syntax/ShellCheck, `git diff --check` | PASS | 0 |

The full suite has one existing host `PyGIDeprecationWarning` for
`GLib.unix_signal_add_full`; there are no unresolved test failures. Host parser
support used RAUC 1.15.1 and FAT tools extracted under `/tmp`, plus the existing
standalone Node runtime for headless dashboard tests. No global host packages
were installed by this sprint. Ephemeral CI has explicit dependency provisioning.

Integration fixtures contain real, locally formatted FAT/ext4 filesystems,
checksummed GPT/MBR disks, and genuinely signed/verity-verified RAUC bundles.
They are synthetic miniature artifacts with temporary test certificates; they
are not bootable release qualification or physical Pi measurements. Tests cover
wrong signers/builds, payload damage/truncation, contradictory factory roots,
missing slot/lifecycle evidence, path/link/special-file attacks, interrupted
state transitions, parser deadlines/output limits and immutable data separation.
The test-only minimum image size override does not weaken the CLI's normal
512-MiB minimum or 64-GiB expanded inspection limit.

Concurrent capture-artifact audit commits were fast-forwarded and preserved;
none of that engineer's reserved files were edited. No production release
artifact, Pi update or physical rollback was qualified in this source sprint.
Main, the Pi, boot partitions, installed services/radios and capture data were
not changed. All remaining hardware acceptance gates above remain open.
