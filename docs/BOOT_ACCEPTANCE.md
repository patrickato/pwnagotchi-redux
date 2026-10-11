# Redux first-boot acceptance (separate development SD)

This guide is for the standalone **Pwnagotchi Redux / Augur** ARM64 image on
Raspberry Pi 4 or Pi 5. **Do not use the owner's working Jayofelony card.**
Boot an independently flashed, checksum-verified development SD and keep a
console/SSH recovery method available.

## One-command, read-only evidence

Run on the **booted Redux device**:

```sh
redux-boot-check
# Also fail on things the tool cannot establish:
redux-boot-check --strict
```

The command prints one JSON object; it never modifies services, network,
filesystems, input devices, OTA slots, storage or radios. It reads the kernel's
mount inventory, Pi board model, architecture, baked source/hash-manifest
**presence**, four specific systemd unit states and the local Redux Doctor
endpoint on `127.0.0.1:8080`. It uses no shell or external network.

The diagnostics are deliberately staged as `redux-boot-check` in the image,
including the headless/Pi OS Lite default. An editable Python installation
also exposes the same command. **No diagnostic auto-runs at boot**; this is
for the operator to invoke when the device can be observed.

### Interpreting the verdict

- `software_checks_ok` means **every included, observable software check**
  passed. It does **not** mean the full Pi image, radio, capture, OTA, display,
  power-loss or 24–72-hour test is physically qualified.
- `action` means a known mismatch, such as `/captures` missing, a tmpfs
  overmount, REDUXCAP pointing at another block device, `/boot/firmware`
  writable, unexpected architecture, stopped services, or an unmasked stock
  Bettercap service. Exit status **2**.
- `unverified` means no observed mismatch but at least one required fact is
  unavailable (for example systemd isn't running, the REDUXCAP label link is
  missing, or the loopback Doctor isn't reachable). Normal exit is **0**;
  `--strict` exits **3** on an `unverified` result. Unknown is never success.

Every check has a status, detail, non-mutating remediation guidance and
bounded evidence. A missing or incomplete `/proc/self/mountinfo` is UNKNOWN,
not a passing storage result. Check the actual `mountinfo` filesystem and
mode, not a directory's mere existence; overlaying `/captures` with RAM is
an **action** error even if it is writable.

The label identity check compares the mounted `/captures` block source with
`/dev/disk/by-label/REDUXCAP` after device resolution, without depending on
stable `mmcblk` or USB names. If that identity cannot be observed, it does not
call the mount qualified.

`image_manifest` only verifies the **presence** of selected version and hash
files. It deliberately does *not* calculate or endorse firmware checksums.
Use the separate build guide's real `sha256sum -c` and loaded module/kernel
vermagic checks when qualifying each Raspberry Pi board.

## After the software snapshot

Observe and record separately on both Raspberry Pi 4 and Pi 5:

1. Image SHA256, board revision, bootloader version, selected kernel/module,
   separate development SD identity, and first/second boot results.
2. `/captures` persistence across reboot, stable read-only lower root and
   `/boot/firmware`, failure to start capture when REDUXCAP disappears.
3. Real Nexmon monitor mode and passive RF packets. For capture conversion,
   use **your own** access point and verify the actual closed-PCAP → hcx
   artifacts/ledger; never treat an empty scan as a successful handshake.
4. Display/touch on the physical SPI panel using
   [DISPLAY_ACCEPTANCE.md](DISPLAY_ACCEPTANCE.md).
5. Recovery behavior from process crash, controlled power interruption,
   full storage, unclean capture, and eventual 24–72-hour soak; preserve logs
   without publishing credentials or captures.

For detailed runtime/radio context use [LIVE_RUNTIME.md](LIVE_RUNTIME.md).
The legacy `redux-live-diag` remains available for human-readable service,
log and capture observations; `redux-boot-check` complements it with a bounded
machine-readable software verdict.

## Hardware-free verification (development host)

```sh
python -m pytest tests/test_boot_readiness.py
python -m compileall -q redux/boot_readiness.py
```

Synthetic mount records, systemd output, label symlinks, missing kernel data,
truncated evidence and CLI exit policy are exercised in CI. They cannot
substitute for an exported ARM64 image or physical Pi verification.
