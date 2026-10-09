# Native Redux capture processing (development branch)

This module is **part of the Redux source tree** on branch
`feature/redux-capture-pipeline-integration`. No changes to `main` are needed.

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
end-to-end Pi restart/SD-full tests, production radio service ownership,
TFT/interactive UI controls, and a reproducible image build.

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
