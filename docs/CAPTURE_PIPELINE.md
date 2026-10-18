# Native Redux capture processing (development branch)

The active development branch is `human/redux-workbench`. The original
`main` branch remains unchanged.

## Architecture

- `redux.crack.capture` selects AngryOxide or Bettercap. Passive AngryOxide planning now
  works without any configured target, with `--notransmit`. Active policy is unchanged.
- Bettercap's normal `wifi.client.handshake` events continue on the existing bus.
  Independent ingestion scans the actual output directories, because an event is **not**
  proof a complete file or valid handshake was saved.
- `redux.crack.ingest` converts completed `.pcap/.pcapng/.cap` files with
  `hcxpcapngtool`, and also accepts existing `.hc22000` records. It structurally
  screens WPA*01 and WPA*02 records (not cryptographic proof), hashes and deduplicates
  artifacts, and persists state in SQLite.
- `redux.crack.audit` is an **off-by-default** bounded Pi-local Hashcat 22000 worker.
  It requires an existing local wordlist, Hashcat binary/backend, and Redux Scope entries
  for **every** BSSID in a prepared artifact. No radio commands are issued by this worker.
- `redux pipeline status` and `Augur.status()['capture_processing']` expose counts
  only. The existing web UI's `/api/status` obtains the same counts; it does **not**
  publish hashes or recovered credentials.
- Independent systemd oneshots/timers avoid blocking the radio event loop or web UI.

## REDUXCAP mount identity and safe processing

On a standalone Redux image, the ingestion worker refuses to initialize its
SQLite ledger or output directories unless `/captures` is an observable
ext4 mount. It pins the kernel mount ID, device number and root inode for
the worker's entire lifetime. Startup, file conversion, result publication,
ledger updates, and scan heartbeats recheck that identity. A missing or
replaced mount stops processing with a `CaptureMountLost` error rather than
writing into a rootfs fallback directory or recording a false successful
pass. Loss is latched: a worker restart is required after storage recovery.

Manually mounted/test installations whose processing paths do **not**
reference `/captures` remain supported without this production-specific
check. The systemd service's `RequiresMountsFor=/captures` is kept as a
second layer. These are software safeguards, not proof of physical SD
fault tolerance; transient unmounts during external conversion remain a
physical device acceptance case.

## In the standalone pi-gen image

The image-build stage now copies the same Redux code, pipeline TOML, and
oneshot/timer units into its root filesystem. `hcxtools` is installed during
image creation; the Hashcat compute backend is **not** presumed available.
The image's `REDUXCAP` partition mounts at **`/captures`**; all processing
state and prepared output are kept there, not under the read-only root.
The systemd units require this mount. The *passive ingestion* timer and
`redux-live.service` are enabled in the development image; the audit timer
remains disabled. A dedicated Redux live process owns the Bettercap child.
The stock Bettercap service remains masked to prevent duplicate radio ownership.

On a future successfully booted **development image**, inspect
`mount | grep ' /captures '` and `systemctl cat redux-capture-ingest.service`
to confirm the already-enabled ingestion timer is healthy. Do **not** run the source
installer on a baked image unless you intentionally want a second, separate
copy of Redux under `/opt/redux-pipeline`.

## Install on a disposable Pi / test system

**This is not yet a standalone bootable image or a hardware-qualified release.**
Use a Redux test installation, not your only working Pwnagotchi SD card.

1. Obtain this complete branch as a checkout or a GitHub source archive. Do not use `main`.
2. Install Python 3.11+ and `hcxtools` on the target image. Check `hcxpcapngtool -h`.
   Optional audits require compatible `hashcat` and a usable compute backend:
   check `hashcat -I` before attempting a local test.
3. From the extracted branch root, run:

   ```bash
   sudo bash scripts/install-capture-pipeline.sh
   sudo nano /etc/redux/pipeline.toml
   sudo env PYTHONPATH=/opt/redux-pipeline/app python3 -m redux.crack.ingest --config /etc/redux/pipeline.toml --once
   sudo env PYTHONPATH=/opt/redux-pipeline/app python3 -m redux.crack.audit --config /etc/redux/pipeline.toml
   ```

   The audit command returns `disabled` unless the owner explicitly opts in.
   Ensure the `inputs` paths match the capture engine's **actual** output folders.

4. Only after one-pass ingestion works:

   ```bash
   sudo systemctl enable --now redux-capture-ingest.timer
   systemctl status redux-capture-ingest.timer
   sudo journalctl -u redux-capture-ingest.service -n 60 --no-pager
   ```

   Alternatively, installing with `--enable-ingest` starts the passive timer.
   **The installer never enables the audit timer.**

5. For the normal installed Redux CLI (not the isolated service tree):

   ```bash
   redux pipeline --config /etc/redux/pipeline.toml status
   redux pipeline --config /etc/redux/pipeline.toml ingest
   ```

`redux pipeline audit` checks the explicit `[audit] enabled` setting and current
Redux Scope file. On a device without a functional Hashcat backend, the binary may
exist without being usable; the complete test requires real Pi verification.

## Crash/interruption recovery — operator command

After a conversion error, unexpected worker shutdown, or lost prepared output,
Redux can now **inspect and selectively retry** previously recorded work
without blindly reprocessing the entire capture directory:

```bash
redux pipeline --config /etc/redux/pipeline.toml recover
# Review JSON: retryable, recorded_errors, missing_outputs, blocked, deferred
redux pipeline --config /etc/redux/pipeline.toml recover --max-files 50 --apply
```

The default invocation is genuinely **read-only**: it opens the existing
SQLite ledger in read-only mode, creates no database/lock/outputs, and returns
bounded counts **without printing filenames, hashes or MAC addresses**.
It distinguishes prior conversion errors, missing prepared artifacts,
and altered prepared bytes (`altered_outputs`). Existing prepared results
are checked against their recorded SHA-256 with bounded reads. A mismatched
or oversized result is reported as blocked and is never overwritten or
silently repaired. Missing source inputs, symlinks, unsettled captures and
suspicious paths are not retried.

`--apply` retries a bounded set of eligible sources using the very same
`CaptureIngestor`, protected by its exclusive `jobs.lock` and REDUXCAP
mount-identity checks. It preserves raw sources and never overwrites an
altered prepared artifact. The existing normal timer can continue afterward;
it does not automatically invoke this operator-only recovery pass.
`max_files` defaults to 100 (allowed 1–1000); inspection reads no more
than ten times that many candidate ledger records and reports
`truncated: true` if further work may remain. Repeat deliberately if
needed. Content deduplication can yield a `duplicate` outcome for
different captures containing the same valid record, which is expected.

**Limitations:** The inspection reports only previously ledgered errors
or missing prepared paths. It does not claim every unledgered capture is
healthy or that the converter or radio has been validated on physical
hardware. The operation is exercised by synthetic CI tests; actual
ARM64 capture conversion and power-loss recovery remain field gates.

## Long-running capture retention (explicit, reversible decision)

Raw captures can consume storage even after a verified conversion. Redux now
provides a **dry-run by default** storage inventory and an operator-only cleanup:

```sh
redux pipeline --config /etc/redux/pipeline.toml prune --older-than-days 30
# Review the candidate list and estimated bytes first.
redux pipeline --config /etc/redux/pipeline.toml prune --older-than-days 30 --apply
```

The cleanup command requires an explicit `--apply`; **there is no scheduled
automatic deletion**. It has a minimum age of seven days, a bounded batch
(default 100), and checks that the source path is an immediate child of a
configured capture input. It verifies source SHA-256 against the conversion
ledger and the prepared artifact SHA-256 against the actual output. It refuses
invalid/error results, missing or corrupted outputs, newer captures, symlinks,
and source files with additional hardlinks (including interrupted handoffs).
The prepared `.hc22000` artifacts and ledger are never deleted.

This intentionally cannot guarantee that an old capture has no other forensic
value. It is an *optional retention choice*, not a replacement for exporting
and backing up raw captures before cleanup. Cleanup is intended only for
capture files on systems the owner administers.

## Integrity and reproducibility

- The capture processor rejects symlinks and unstable files, limits bytes/time/jobs,
  and writes private, atomic `.hc22000` artifacts.
- File hashing is used for idempotency and tamper detection, not cryptographic
  validation of a handshake.
- The persistent SQLite ledger stores source/output digests, status and diagnostics,
  but not recovered credentials.
- Never add raw capture files, real SSIDs, recovered credentials or wordlists to Git.
- Copy the full extracted source branch for installation, not a patch ZIP over `main`.
- If you rebuild/reinstall the entire Redux image, ensure both Bettercap and
  AngryOxide output paths are correctly configured and radio ownership is exclusive.

## Remaining release gates

This integration still requires verified on-device capture paths/event schemas,
controlled sample WPA2 captures, a real converter, Hashcat backend capability,
end-to-end Pi restart/SD-full tests, real live-runtime and exclusive-radio verification,
TFT/interactive UI controls, and a fully built and boot-tested ARM64 image.

`redux campaign demo` still uses test executors and is not a production autonomous
campaign. Active deauthentication and password-audit targeting policies are not
changed by this branch.

## Test commands

```bash
python3 -m compileall -q redux
python3 -m pytest -q tests/test_capture_ingest.py tests/test_capture_audit.py tests/test_capture_pipeline_wiring.py
python3 -m pytest -q
bash -n scripts/install-capture-pipeline.sh
```
