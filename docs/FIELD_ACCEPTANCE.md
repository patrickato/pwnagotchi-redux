# Redux Pi 4 — Consolidated Field Acceptance (disposable SD only)

**Status:** Engineering checklist with partial Pi 4 hardware evidence.
The disposable image has booted and its services/regulatory recovery have
been observed. Native packet decoding, closed capture handoff and conversion
remain unverified. The separate existing Jayofelony SD card is out of scope
and must remain untouched. See `docs/IMAGE_BUILD.md` for timestamped evidence.

| Gate | Pi 4 evidence | Remaining qualification |
|---|---|---|
| Boot and storage | Console boot/login, REDUXCAP and services observed | Cold-boot persistence and endurance |
| Engine and country | Running supervisor, Bettercap 401, US restored after reload | Full radio/driver recovery behavior |
| Native RF capture | Firmware-mode test increased RX; selected netdev remains Ethernet type 1 | 802.11/radiotap decoding, sightings and handshake/handoff |
| Ingestion | Fresh empty worker scans | Real closed-file conversion and ledger consistency |
| TFT/touch | No physical qualification | Display, touch and kiosk checks |
| Pi 5 | No physical qualification | Build, boot and all hardware gates |

## Preconditions — before touching a Pi

- [ ] The current workbench checkout is clean on
  `human/redux-workbench`. A GitHub CI pass is **offline evidence only**.
- [ ] Work on the ASUS's **Ubuntu WSL ext4 filesystem**, with at least
  20 GiB free (30 GiB preferred), operational ARM64 binfmt, pi-gen tools,
  and `sudo`. The build preflight must pass.
- [ ] You are physically in the selected regulatory country. For the
  present US field card, `REDUX_REGULATORY_COUNTRY=US`. Never blindly
  reuse US when traveling or distributing images.
- [ ] Build the field image with
  `cd ~/redux-workbench-src && git pull --ff-only && bash scripts/build-field-image.sh`.
  The command generates the `.img.xz` and produces a release manifest
  and checksum. It **qualifies** those bytes and the export country gate
  before updating `~/redux-field-latest`. On error, DO NOT flash.
- [ ] Inspect the manifest/checksum in the **new** deploy folder.
  `physical_boot_verified` and `radio_capture_verified` must still be
  **false**; their absence from offline proof is intentional.
- [ ] Record the Git SHA, image filename, SHA-256, selected country,
  and target SD capacity. Use Raspberry Pi Imager to flash the **spare**
  card only. Flashing destroys all data on the selected card.

## Gate 1 — Boot and safe remote access

- [ ] Pi 4 8GB boots the disposable field image without repeated reboots.
  Connect the Pi Ethernet port to the trusted router/LAN.
- [ ] Device hostname is `redux`. Console `pi/redux` is *field-mode
  only*; SSH must remain public-key-only and reject password logins.
- [ ] Confirm the Pi's LAN IP from router DHCP or the laptop's network
  discovery. Never assume an old DHCP lease is still current.
- [ ] From ASUS WSL, run ONE read-only summary (replace the IP with the
  newly confirmed one):

      `python3 scripts/field-acceptance.py --host 192.168.12.115 --country US`

  This reports PASS/FAIL/UNKNOWN and runs only `sudo -n redux-report --full`
  over the previously trusted SSH key. The known host key must be verified;
  a changed key needs investigation, not `StrictHostKeyChecking=no`.
- [ ] `redux.service`, `redux-live.service`, and
  `redux-capture-ingest.timer` are active. Dashboard responds locally.
- [ ] REDUXCAP is mounted as a dedicated ext4 volume. The overlay root
  is read-only; active captures and status write only under `/captures`.

**STOP** if the Pi reboots in a loop, shows an unexpected filesystem,
mounts capture data on root, exposes password-based SSH, or the hardware
fingerprint/host identity differs from what was expected.

## Gate 2 — Country, radio and Bettercap integration

- [ ] `redux-regdom.service` has successfully applied the country's
  global cfg80211 policy at boot. For the US card, the report must say
  `country US`, *not* `country 00`. Neither software nor hardware
  rfkill is blocked.
- [ ] The **selected capture-radio interface itself** is in monitor mode,
  not merely a different Wi-Fi interface. The laptop checker matches the
  selected interface against its own `iw dev` block; unknown or missing
  interface data does not count as a pass. Verify no Wi-Fi device reports
  a software or hardware RF block. The supervisor must not disrupt Ethernet.
- [ ] The selected interface exposes raw 802.11 or radiotap packets
  (`capture_link_type` ARPHRD 801 or 803). An Ethernet link type (1)
  does not pass merely because `iw` reports monitor mode. A missing
  measurement remains UNKNOWN until the field helper is updated.
- [ ] Bettercap is alive, and the REST interface is reachable on
  `127.0.0.1:8081`. An **unauthenticated** probe should return HTTP 401
  (authentication required); HTTP 400 is unexpected, while HTTP 200
  needs review of API authentication.
- [ ] Live state reaches `running`. A listening REST port or active
  systemd service **alone does not pass this gate**. Check Doctor's
  `last_error`, attempts, failure streak, and retry countdown.
- [ ] The earlier `/root/bettercap.aliases` permission errors and
  interactive `Are you sure you want to quit` prompts do not recur in
  **new** log lines. Old lines may remain in an append-only log.

**STOP** if Bettercap repeatedly restarts, the session API returns 400,
a blocked country persists, or the app reports `starting_engine` /
`degraded` continuously. Collect one report; do not reflash simply
to reattempt the same fault.

## Gate 3 — Passive observation and closed capture handoff

- [ ] On an operator-owned test network/AP, confirm Redux observes
  actual live events. The `events_seen` counter must increment from
  real activity, not simulated data. This counter includes runtime/service
  events; an increment alone is not proof of decoded Wi-Fi frames. Confirm
  real sightings independently before claiming RF reception.
- [ ] Wait at least one configured rotation interval (default five
  minutes, or use a dedicated controlled test profile), then confirm
  `handed_off > 0` and a completed file is no longer owned by
  Bettercap in `/captures/active`.
- [ ] The capture-ingest worker records a completed processing job.
  Confirm the file ledger and ready-output state agree; no duplicate
  processing, uncontrolled file growth, leaked private caplet, or
  conversion of an in-progress `active` file.
- [ ] If testing handshake conversion, use **only the operator's own**
  AP/client, with authorization and controlled traffic. No deauth or
  intrusive traffic is required to assess the passive pipeline.
  A successful event count alone is not proof of a valid handshake.
- [ ] Test storage failure behavior: insufficient REDUXCAP free space
  should pause capture cleanly, preserve completed files, and report
  Doctor warning. Do this with a controlled test image and test data,
  **not by filling the working card**.

## Gate 4 — Cold reboot persistence

- [ ] Cleanly shut down and restart the **same** field SD.
- [ ] Re-run the one-command field acceptance report. Confirm selected
  country, unblocked Wi-Fi, monitor interface, REDUXCAP, Bettercap REST,
  and service states **without reapplying any manual field patch**.
- [ ] Existing completed capture/ledger data persists under REDUXCAP;
  temporary overlay-root changes do not leak across boots.
- [ ] No hardware-watchdog reboot loop; observe a reasonably steady
  runtime and temperature with genuine Pi telemetry.

## Gate 5 — TFT/touch (later, separate hardware gate)

- [ ] The 3.5-inch 480x320 ILI9486 / ADS7846-XPT2046 panel and SPI
  overlay are explicitly configured for this Pi, not assumed from CI.
- [ ] Confirm real framebuffer output, rotation, X/Y calibration,
  responsive touch/tap/swipe/scroll and readable Doctor + radio pages.
- [ ] Confirm no display rendering depends on synthetic telemetry or
  causes the live capture supervisor to lose control of the radio.
- [ ] Only after these hardware checks consider enabling automatic kiosk
  launch. The headless/Lite build deliberately does not claim TFT readiness.

## Decision

A passing CI suite means **source/image-contract ready**. A produced,
SHA-256-verified `.img.xz` means **artifact ready to flash**. A Pi that
boots and passes Gates 1–4 is **field-capable**, but not yet a final
release. Full capture/processing, endurance, country/legal compliance and
TFT checks remain their own independent acceptance evidence.

The `scripts/field-acceptance.py` command does not change radio settings,
log in to the Bettercap REST API, retrieve captures, or claim successful
handshake conversion. Any **UNKNOWN** check remains open, never silently
becomes a PASS.

**Keep a record:** image SHA-256, code revision, date, device model,
regulatory country, run/report time, gate results, and observed defects.
Never upload raw capture data or private SSH keys to a public issue.
