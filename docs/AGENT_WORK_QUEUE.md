# Autonomous work queue — Redux workbench

**Branch:** `human/redux-workbench`. This is a shared engineering
prioritization file, **not** a remote command queue or a blanket authorization
to change the Pi. ChatGPT can modify the repo and review results; Codex CLI
runs locally in WSL and can reach the Pi over independently verified SSH.
Agents must pull/review updates and avoid overwriting concurrent work.
Read `AGENTS.md` and `docs/LOCAL_AGENT_HANDOFF.md` first.

## Current handoff rule

Complete the highest unblocked, safe item. Run a meaningful test before
committing code; report the commit and exact evidence. If physical Pi actions
require approval, request a *single, specific action* and continue with the
next hardware-free item. Do not wait idly for new instructions if there is
independent work to do. Human approval for a specific action covers only
that action's stated scope. Keep the separate working Pwnagotchi card intact.

**Public repo privacy:** never commit real captures, 22000 hashes, Wi-Fi
identifiers, private host keys, API secrets, unhashed credentials or raw logs.
Synthetic fixtures are fine when clearly labeled.

## P0 — standalone Pi 4 native RF capture hardware gate

**Evidence as of 2026-10-12:** boot, physical login, REDUXCAP mount, live
services, regulatory US, and loopback Bettercap listener tested. Subsequent
native monitor experiment created `reduxmon0` but failed before capture
sampling. Cleanup deleted the temporary netdev, yet `wlan0` recovery
failed with brcmfmac `-16` busy; `redux-live` was degraded.
`0f01d80` implemented a **source-only**, un-deployed cfg80211 monitor
VIF destructor fix; CI passed. No valid frame capture/handoff demonstrated.

1. **Unblocked (safe; agent-owned):** Review the source fix against pinned
   Nexmon branches/kernel versions and check reference-counted netdev
   lifetime: success path, register failure path, teardown/destructor call,
   duplicate cleanup and VIF state. Harden synthetic unit tests and build
   checks as needed. **Do not assume CI green = compiled ARM64 module.**
2. **Unblocked (safe; agent-owned):** Extend read-only hardware diagnostics
   if needed to report the actual selected interface's ARPHRD link type
   (Ethernet 1 vs IEEE80211 801/radiotap 803), runtime error/retry,
   channel, RF blocks, capture file names and byte sizes, ingester
   heartbeat and count/state, without exposing raw captures or secrets.
   Existing field acceptance code may already cover link type; reuse
   it instead of duplicating output.
3. **Operator-approval gate:** Recover the installed brcmfmac driver, if
   approved, using existing Ethernet-only SSH; stop live, unload/reload
   **without force**, restart live and verify diagnostics. Stop on any
   unexpected dependency/network risk; do not deploy new driver as part
   of this gate. A prior suggestion from chat is not itself proof that
   the owner has authorized the action in the Codex terminal.
4. **Separate approval/build gate:** Compile the modified Nexmon module in
   an isolated arm64 image build with clearly captured compiler/dkms
   output and module/kernel provenance. Do not install it on the Pi,
   reflash, reboot or overwrite working cards without approval. Build
   only when source-level confidence is adequate.
5. **Physical test gate:** After approved deployment, capture real
   link-layer header evidence for the selected interface, real passive
   frame reception, verified events/sightings, a closed nonempty PCAP
   handoff on an operator-owned AP, successful ingester ledger, and
   persistence after cold boot. Never use synthetic events as evidence.

**Acceptance:** exact physical artifacts/probes meet
`docs/FIELD_ACCEPTANCE.md`, not simply `iw dev type monitor` or six
event emissions.

## Independent Nexmon source review — 2026-10-12

Reviewed pinned `jayofelony/nexmon` commit
`1654e1857766df92086dbfbed5ffd288efc9bd8c`, notably
`brcmfmac_6.12.y-nexmon/core.c` and `cfg80211.c`:

- The ordinary `brcmf_net_attach` path installs
  `ndev->priv_destructor = brcmf_cfg80211_free_netdev` on successful
  netdev registration; the native monitor's `brcmf_net_mon_attach`
  lacked that assignment. The code's `brcmf_cfg80211_free_netdev`
  removes the attached `vif` from the driver list.
- Monitor deletion follows `brcmf_cfg80211_del_ap_iface` to
  `brcmf_remove_interface`, so the missing registered-netdev
  destructor plausibly leaves the VIF in the list and contributes to
  `brcmf_vif_add_validate` reporting `-EBUSY`.
- The source-only fix in `0f01d80` installs the same destructor after
  successful monitor registration; this is consistent with the ordinary
  attachment pattern. It does **not** establish firmware-level cleanup
  success, prove no latent netdev races, or prove a rebuilt kernel module
  works on either Pi. Build and physical tests remain mandatory.
- Nexmon's monitor path creates a **firmware AP-type interface**
  through `brcmf_cfg80211_request_ap_if`; don't equate cfg80211
  `monitor` label with native radiotap reception. Verify link type
  and real frame ingress.

Source links:
[pinned core.c](https://github.com/jayofelony/nexmon/blob/1654e1857766df92086dbfbed5ffd288efc9bd8c/patches/driver/brcmfmac_6.12.y-nexmon/core.c) and
[pinned cfg80211.c](https://github.com/jayofelony/nexmon/blob/1654e1857766df92086dbfbed5ffd288efc9bd8c/patches/driver/brcmfmac_6.12.y-nexmon/cfg80211.c).

## Independent capture-ingestion hardening — 2026-10-12

- `5320fa3` and `6a390a5` harden capture worker locking against
  symlink redirection and linked/nonregular inodes, with regression tests.
  No Pi changes or actual capture content touched.
- GitHub CI for `6a390a5`:
  [run 38334772850](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38334772850)
  **passed**; software-only validation, not RF validation.

## P1 — engineer a less manual field workflow

**Local progress checkpoint:** The single approved existing-driver recovery
completed removal/reload but initially failed runtime startup on global
country 00. A separately approved regulatory-service restart restored the
configured US policy; automatic live retry then reached running on wlan0.
Firmware monitor 0 and Ethernet link type 1 remain unqualified for RF.
No further driver/interface experiment, target module build or deployment
is authorized by those completed approvals.

P0.1 now includes executable synthetic C lifecycle checks for repeated
successful deletion, held references, registration failure and a negative
control exposing the original stale VIF. The patcher rejects unsafe existing
destructor placement. Actual ARM64 module compilation remains a separate
approval gate; host simulation is not kernel/hardware evidence.

- Keep physical collection **read-only by default** and driven from
  the owner's Ubuntu WSL via previously verified strict SSH.
- Prefer one bounded diagnostic collector over multiple on-Pi commands.
  Sanitize before output or GitHub commit; permit a summary-only mode.
- Ensure the operator's single local Codex session can pick the next
  unblocked queue item from this file after each task. Do not introduce
  a public GitHub self-hosted runner with LAN/SSH credentials.
- Require operator opt-in for any always-on background agent or
  private control bridge; no hidden daemons, public shell endpoints or
  credential uploads.

## Completed software-only hardening — 2026-10-12

The chat-side lead hardened the capture ingestion pipeline without touching
the Pi, RF state, or deployed drivers:

- `c5024b3` and `4fed2ea`: converted-artifact publication now uses an
  atomic no-clobber hard link in place of a potentially overwriting rename,
  with a regression covering another writer racing the destination name.
  CI [38335216928](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38335216928)
  passed.
- `ba5c7c6`, `9afa25d` and `ac77d28`: nofollow regular-file digest
  reads and matching original device/inode checks before accepting a
  converted source, including a deterministic replacement-race regression.
  CI [38335290544](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38335290544)
  passed.
- `fc1d4a6` and `ae98260`: bounded nofollow reads for direct
  `.hc22000` inputs, with swapped-symlink and oversize fixtures. Final
  CI [38335386221](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38335386221)
  **passed**. This is CI evidence only, not physical Pi validation.

**Converter path-race follow-up completed (source only):** Commits
`2f8da6e` / `d34da2d` pin the original verified capture inode via a
Linux read-only file descriptor, which `hcxpcapngtool` receives using
`/proc/self/fd/N` with `pass_fds`, rather than reopening the mutable
input pathname. Commits `cc398d4` / `9ad8702` bound and protect
converter output reads. E2E contract adjustments in `d4b7be0` /
`0d699b7` verify intended inode access. GitHub CI
[38336910184](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38336910184)
**passed** with synthetic fixtures; **actual hcxpcapngtool behavior on
real ARM64 Pi capture files is not yet qualified**. Check it during the
next approved field image validation. No change to the installed Pi was
made by this source-only work.

## Retention safety follow-up — 2026-10-12

The manually invoked `redux pipeline prune` command now uses nofollow,
regular/single-inode lock handling consistent with the ingestion worker
rather than blindly opening `jobs.lock` by pathname. Retention pins
the REDUXCAP mount identity and refuses to continue if the volume goes
missing or is substituted while evaluating an eligible raw source.
Dry runs never create a lock or silently repair its permissions; a
misconfigured existing lock is reported explicitly. Destructive apply
still requires deliberate operator invocation, a verified raw source
and a verified corresponding converted artifact. No automatic capture
deletion has been introduced.

- `2bda6cc`: fail-closed lock and capture mount checks.
- `dbaab4b`: symlink, hardlink, FIFO, missing-mount and swapped-mount tests.
- `62ea24b` / `09e793f`: dry-run lock permission immutability with
  explicit apply recovery test.
- GitHub CI
  [38337310532](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38337310532)
  **passed**. This proves the software regression suite, not Pi storage
  fault recovery under real power loss.

**Remaining:** fully vet actual `hcxpcapngtool` conversion on the
disposable Pi image, and test volume-removal behavior during controlled
field acceptance. Never test destructive retention against real
unbacked captures without operator approval.

## New operational capability — targeted capture recovery (2026-10-12)

The standalone operator now has `redux pipeline --config
/etc/redux/pipeline.toml recover` as an actual CLI command, with
`recover --apply` available for explicit bounded retries. Dry run is
read-only and suppresses sensitive filenames and capture content.
It identifies prior conversion errors and missing prepared outputs in the
durable SQLite ledger, skips suspicious/missing/unsettled input paths,
and reports retry/deferred/blocked counts. Apply uses the same
REDUXCAP-pinned ingestor and exclusive worker lock; raw captures and
previous prepared artifacts are never deleted by recovery.

Source: `redux/crack/recovery.py`, `redux/cli.py`. Tests:
`tests/test_capture_recovery.py`, `tests/test_capture_pipeline_wiring.py`;
Doctor now recommends this procedure for measured capture-processing
errors. Documentation: `docs/CAPTURE_PIPELINE.md`.
Hardware-free smoke and full CI passed at
[38338209966](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38338209966)
for the CLI E2E fixture; the subsequent Doctor integration CI is being
tracked separately.

**Not yet hardware accepted:** ARM64 `hcxpcapngtool` real capture conversion,
unexpected-power-loss recovery, and Pi 4/5 packaged image smoke tests.
These remain field gates. Do not represent a passing synthetic recovery
test as a measured handshake or radio recovery.

## Native TFT feature milestone — 2026-10-13

The chat-side lead implemented a **real, lightweight browser-free TFT
dashboard** for the standalone Lite image:

- `redux/display/fbui.py`: read-only localhost
  `/api/status` telemetry, honest stale/offline indication, Overview,
  Captures and Doctor views, RGB565/BGR565 byte packing, measured 480×320
  16bpp framebuffer preflight, explicit device selection, optional 180°
  rotation and bounded two-second refresh.
- `redux/display/touch_nav.py`: optional, operator-selected
  `/dev/input/eventN` classic BTN_TOUCH/ABS_X/Y **read-only**
  swipe paging. No EVIOCGRAB or calibration changes. Hardware orientation
  and touch node must be verified before enabling.
- Packaged `redux-fbui`, optional disabled `redux-fbui.service`,
  `/usr/share/redux/fbui.example.env`, font runtime and Pillow into the
  ARM64 image. **No display service is enabled by default.** Persistent
  opt-in config belongs on `/captures/config/fbui.env`, not the disposable
  overlayroot.
- The export root contract refuses missing fbui software, unsafe
  default enable links and baked-in framebuffer guesses. Tests:
  `tests/test_fbui.py`, `tests/test_touch_nav.py`,
  `tests/test_image_build.py`, `tests/test_root_payload.py`.
- Hardware procedure and CLI options: `docs/DISPLAY_ACCEPTANCE.md`.

**Status:** Source/UI tests, Pi-gen staging and browser-free rendering are
software validated in CI. The mounted-root gate check for the final new
fixtures is awaiting its latest CI result as this note is drafted.
The image has **not** been rebuilt/flashed/tested with these additions.

**Next physical gate, only when operator approves a new disposable image
build/flash:** enumerate actual `/dev/fbN` and `/dev/input/eventN`,
confirm 480×320/16bpp/stride, run `redux-fbui --check`, observe a single
native RGB565 pixel frame, validate rotation/color order and real telemetry,
then test manually selected swipe axis and touch latency. Never infer
touchscreen correctness from synthetic unit tests. Do not enable
`redux-fbui.service` until its specific hardware acceptance is complete.

## P0 build provenance follow-up — unsigned Debian keyring warning (2026-10-15)

The operator's active ASUS WSL image build at snapshot `0403c396`
reported:

`W: Cannot check Release signature; keyring file not available /usr/share/keyrings/debian-archive-keyring.gpg`.

The **old build is not release-trust qualified** even if the pi-gen process
finishes, produces an image, and its artifact checks are green. Those checks
verify compressed bytes and on-disk layout, not authenticated package
provenance. It should **not be flashed as a trusted new image**.

Lead-side workbench changes (all source-only; *not* in the running snapshot):

- `image/host_preflight.py`: requires a real Debian archive-keyring file
  with the published Bookworm archive and stable-release fingerprints,
  including valid packaged `.gpg -> .pgp` sibling aliases; checks for
  missing `gpgv` and isolates read-only GPG inspection in a temporary home.
  Blocks a new full build before fetching sources.
- `image/pigen_release_auth.py`: anchors pinned pi-gen `scripts/common`
  to inject `--force-check-gpg` and rejects upstream drift or insecure
  overrides. Called from `build.sh` before Nexmon download.
- `scripts/qualify-field-image.py`: refuses a finished image when the
  staged debootstrap script lacks the mandatory signed-Release policy,
  even if its SHA256, layout, and export gates pass.
- Regression tests: `tests/test_host_preflight.py`,
  `tests/test_pigen_release_auth.py`, `tests/test_image_build.py`,
  `tests/test_field_image_qualification.py`. Full unit/image CI passed at
  [38756359516](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38756359516).

**Local Codex follow-up:** after preserving any current image output, check
the actual build log for missing-keyring/signature warnings. Provision the
build host's Debian archive-keyring package from its trusted Ubuntu package
sources (requires specific owner approval for package installation). Once
the host passes the new keyring preflight, request a **fresh, isolated,
signed-build snapshot** from the updated workbench. Do not reuse an already
bootstrapped unsigned rootfs. Do not reflash/reboot the Pi, reload the radio
driver, or declare physical boot/capture acceptance by inference. Keep
source-only work going while privileges or an approved field test are
pending.

**Local verification checkpoint:** The running `0403c39` build linked both
ARM64 `6.12.109` target modules and advanced past vermagic checks into
export. The local agent independently confirmed the unsigned-bootstrap
warning, preserved its log/workspace and marked the build DO NOT FLASH.
Updated host preflight confirms the missing keyring. A fresh isolated
`240c51c` launcher is prepared; specific host-package approval and terminal
sudo authentication are pending. Full local tests: 1,519 passed, 2 skipped.
Neither trusted artifact qualification nor physical driver behavior is
claimed. No Pi mutation occurred.

## Native monitor-path source review — 2026-10-15

Review of pinned Nexmon `1654e185` distinguishes two separate paths:

- `brcmf_cfg80211_change_iface` accepts MONITOR under `NEXMON_MON_IF`
  and updates the wireless interface type; this path does not install
  monitor netdev operations, change ARPHRD to radiotap, or enable firmware
  monitor mode. The primary interface can therefore retain Ethernet packet
  type despite its cfg80211 monitor label. This is consistent with measured
  type 1 / firmware mode 0, not independent proof of the complete RF fault.
- `brcmf_mon_add_vif` creates a separate firmware interface, installs
  `brcmf_netdev_ops_mon`, and selects ARPHRD radiotap. Its
  `brcmf_net_mon_open` queries firmware mode and requests mode 2 for that
  link type. It refuses an already-enabled firmware mode. The primary
  interface-label path is not a substitute for this lifecycle.
- The existing VIF-destructor fix repairs teardown ownership; it does not
  make primary-interface relabeling activate radiotap. Keep those hypotheses
  separate during any later specifically approved hardware test.
- New executable host C channel-callback fixtures exercise both supported
  callback signatures with success and firmware errors (-5, -16, -95).
  The original callback is a negative control that hides errors; the Redux
  adaptation preserves them. Firmware calls are synthetic stubs, not RF
  measurements. Shared native compilation/linking is also used by the
  existing VIF lifetime fixtures.

The operator has approved WSL host keyring installation and a fresh isolated
signed image build. Noninteractive sudo still requires terminal
authentication; a consolidated local launcher is prepared from green
`bd0a953`. No physical Pi action is authorized by that approval.

## RF evidence integrated into live Doctor and native TFT — 2026-10-15

While the local agent conducts the signed ASUS image build, the chat-side
lead added read-only kernel ARPHRD capture-interface measurement
(`redux/radio/link_type.py`) to `redux-live`'s real status snapshots,
Doctor health and native RGB565 display:

- `runtime.capture_link_type`: measured decimal ARPHRD or null.
- `runtime.capture_link_assessment`: `ethernet_link_type`,
  `80211_link_type`, `other_link_type`, or `unverified`.
- When Bettercap is `running` but the **selected** interface reports
  type **1**, Doctor marks `native RF link type` **degraded**, and the
  TFT explicitly reads `ETHERNET 1 / NO NATIVE RF`. The running label
  no longer hides this real field blocker.
- Types **801/803** establish only suitable packet framing, **not**
  measured frame reception or a successful capture/handoff. Unknown
  values remain unknown; no radio configuration changes are made.
- Tests: `tests/test_radio_link_type.py`,
  `tests/test_live_health.py`, `tests/test_fbui.py`; full CI
  [38761496007](https://github.com/patrickato/pwnagotchi-redux/actions/runs/38761496007)
  **passed**. Documentation: `docs/LIVE_RUNTIME.md`.

**Important for Codex:** This is committed to the shared workbench after
the host image build snapshot was frozen. Do **not** interrupt or alter
the running build. Include the new telemetry in the **next** fresh
signed build when convenient, after verifying the package-signing gate
and preserving the running Pi. Physical RF frame capture remains P0.

## Signed artifact ready for approved field test — 2026-10-15

The fresh `4f907ff` image completed and independently passed exported-byte,
XZ/partition/provenance, signed-bootstrap and embedded Pi 4/5 driver checks.
This actual snapshot includes the measured ARPHRD live/Doctor/TFT changes;
the earlier assumption that they missed its source freeze is superseded.
Exact evidence and prioritized hardware steps are in
[FIELD_RUN_20261015.md](FIELD_RUN_20261015.md). READY TO FLASH means a
spare US field-test artifact only. No flash, boot, driver reload, service
change or further image build was performed by verification. The old
comparison card and unsigned diagnostic artifact remain preserved.
Next physical work requires specific approval; do not rebuild this image
without a demonstrated artifact defect.

## Post-flash read-only checkpoint — 2026-10-16

Strict Ethernet SSH and the signed image's Pi 4 module/kernel/firmware
provenance are verified. Services/US/storage/empty worker scans pass.
Native RF still fails (primary wlan0 type 1, firmware monitor 0); reception,
handoff/conversion and 480x320 TFT remain unqualified. See
[FIELD_DIAGNOSTICS_20261016.md](FIELD_DIAGNOSTICS_20261016.md) for the
consolidated report and bounded native-VIF test proposal requiring new
specific approval. No Pi changes or image build occurred. A source-only
Doctor fix now explicitly lists unmeasured native-link health as UNKNOWN
instead of omitting that coverage gap. Continue safe source work; do not
repeat a physical test, driver reload or deployment based on old approvals.

## Current hardware stop — approved 2b540ad trial, 2026-10-16

The single approved attempt stopped at NM isolation, BEFORE VIF creation:
managed=no also changed primary wlan0 to managed mode/DOWN. The runner's
baseline guard blocked further cleanup/restoration; redux-live is inactive,
NM managed=false/state10. Ethernet, US, storage and other services remain
intact. Do not repeat the trial, rebuild, reload or silently recover.
Read [NATIVE_VIF_TRIAL_20261016.md](NATIVE_VIF_TRIAL_20261016.md), which contains
the complete phase report and a new-approval-only primary mode/UP recovery
proposal. A source-only regression models the observed side effect and
improves phase diagnostics without relaxing any safety gate.

## P2 — independent verification and release preparation

**P0.2/P1 collector milestone:** Local source now provides
`redux-report --summary`, with allowlisted observations and bounded capture
inventories. It reuses the link-type probe and read-only ingest summary.
No raw engine logs, private error strings, credentials or RF identifiers
are emitted; non-generated capture filenames are withheld. The collector
was exercised read-only from stdin on the Pi without installation or file
writes: services/worker healthy, but native RF remains unqualified with
Ethernet link type 1 and no handoff. Source tests also cover safe bounded
checkpoint reads. Do not deploy it or infer RF success from that smoke test.

- Run full applicable unit tests, lint and image CI before source
  milestones; fix false greens and contradictory docs.
- Keep a concise hardware scorecard distinguishing **Pi 4 tested**,
  **Pi 5 untested**, simulated/hardware-free validations, and
  awaiting-approval checks.
- Review existing failure-path handling (storage mount loss, empty PCAP,
  interrupted rotation, queued capture processing). Address a verified
  regression with tests, rather than speculative rewrites.
- Preserve the workbench branch as a consistent integrated codebase.
  Never merge to `main` automatically.

## Sync protocol

**2026-10-15 local checkpoint:** Strict SSH reconnected at the previous
address without a host-key change. Services, regulatory US, worker scans
and Bettercap authentication responded; native RF remains unqualified
(packet type 1, firmware monitor 0, empty captures and ledger). The source
field checker now independently rejects stale/future/invalid runtime
checkpoints instead of trusting a durable running label. Full local tests:
1,502 passed, 2 skipped. A fresh local image build is explicitly authorized,
but requires the owner's interactive laptop sudo authentication; a private
launcher is prepared. No new artifact, deployment, reflash or Pi mutation
has occurred in this session. Continue independent safe work while that
local credential prerequisite is pending.

- Lead/chat: update priorities here, review GitHub commits and CI, do
  independent edits with collision checks, leave clear status notes.
- Local Codex: `git pull --ff-only` only with a clean worktree,
  execute next unblocked item, commit tested code to same workbench,
  note physical evidence without secrets. If chat-side changes arrive,
  reconcile before the next iteration.
- Operator: occasional physical actions, one-time approvals and final
  release decisions. No repeated copying of routine diagnostic commands.

No automatic push from the local Codex agent can be guaranteed while
its process is closed. This document is effective only when an active
agent reads it.
