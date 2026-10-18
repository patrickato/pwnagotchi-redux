# Redux completion scorecard — updated 2026-10-17

> **Overall estimate: approximately 60–65% complete / 35–40% remaining**
> for the ambitious **standalone Augur / Redux v1 product**, not just the
> capture MVP. This remains a judgment-based, weighted estimate with
> approximately ±10 percentage points of uncertainty; tests and code volume
> do not substitute for physical acceptance. The nearer-term **Pi 4 usable
> capture MVP** has different, stricter pass/fail gates below.

## October 17 source-only UI increment (percentage unchanged)

The native 480×320 renderer now has **five** pages: Overview, Captures,
Doctor, Radios and **Trends**. The Trends page uses the *existing* Augur
runtime counters to build a private, bounded 151-sample, five-minute history
of actual status events, handshake-event notifications and completed file
handoffs. The ring requires a positive measured engine PID, retains 150
intervals at the normal two-second cadence, and shows each latest rate beside
the graph. It discards stale/duplicate data, counter rollback, engine changes,
unexpected gaps and malformed measurements; it **never** labels these counts
verified 802.11 reception or valid handshake captures. Overview now shows
fresh retry/failure/start and REDUXCAP mount diagnostics. The Radios page
visually separates Bettercap's capture interface from onboard and USB adapter
observations, including the measured netdev name when available, while
explicitly leaving reception and USB process ownership unverified. The
installed service now samples `radio_inventory` through a bounded read-only
worker: sysfs identity is checked before and after one `iw dev` query, virtual
interfaces are grouped by physical device, and unknown transports or duplicate
physical radios on one transport fail closed. Samples are attempted every
10 seconds and expire in the UI after 20 seconds. This reports metadata only;
it does not change radios or establish packet reception. Touchscreen
navigation, previews, runtime diagnostics, and regression coverage were
extended without enabling the real TFT or changing the Pi.

The Radios page now reconciles the two source clocks and reports their ages
plus fail-closed readiness blockers. Cross-generation timestamp skew,
duplicate interface identities, runtime/inventory iface disagreement and
physical adapter replacement are rejected. Even fully compatible kernel
metadata remains `UNVERIFIED` while policy, exclusive ownership, decoded
reception and physical qualification are unmeasured. This is source-only UX
hardening and does not increase the completion percentage.
The async runtime boundary now normalizes and copies allowlisted inventory
fields before publication, preserves the prior timestamp on malformed refresh,
and discards late worker results during shutdown. These lifecycle safeguards
prevent caller mutation or a closing service from freshening radio evidence.

Native-VIF ownership analysis now has one shared classifier for the bounded
DOWN-only evidence format. It requires two identity-stable snapshots, a stable
NetworkManager unique D-Bus owner and agreement between both device inventory
APIs; missing NM enumeration remains unknown. The prepared operator procedure
is observation-only and not authorized. Display inventory now reports bounded,
explicit framebuffer/ILI9486/touch selection blockers before any physical test,
without promoting sysfs metadata to working pixels or touch. These close
source-side diagnostic gaps but leave both hardware gates open, so the overall
percentage remains unchanged.

The combined GitHub CI run `39065298631` passed **2,378 software tests and
651 image-contract checks**. This is source-level verification only.

The source work is meaningful progress in usability but **does not justify
raising the weighted ~62% overall score**. The standalone radio, real capture,
480×320 panel and Pi 5 gates are still open. Codex's separately implemented
Alfa passive qualification runner is synthetically tested, not approved for
hardware execution; see `docs/AWUS036ACM_PASSIVE_QUALIFICATION.md`.
The workbench remains ahead of the field-flashed Oct 15 image.

## Current physical evidence (supersedes older checkpoints in this file)

The signed ARM64 `4f907ff` image was independently qualified and **booted on
the owner's Pi 4**. October 16–17 field checks confirmed signed image/kernel/
driver provenance, Ethernet SSH, US regulatory state, REDUXCAP ext4, Redux
services, Bettercap REST authentication and recurring empty worker scans.
The selected primary `wlan0` remains **ARPHRD 1**, firmware monitor **0**,
even while `iw` labels it monitor. No closed PCAP, valid handoff or prepared
capture has been observed from this standalone image.

**October 17 native-VIF milestone:** After a scoped runtime NM isolation
and one interface-add operation, the disposable Pi created exactly one
verified temporary native monitor VIF on phy0 with **ARPHRD 803 (radiotap)**,
ifindex 5, and firmware monitor 0 *before activation*. NetworkManager had not
enumerated the VIF, so affirmative unmanaged ownership was unavailable;
the runner stopped without activating the VIF or receiving packets.
The reviewed rollback fully restored the original primary mode/UP,
NetworkManager state and running Redux. Thus **native interface creation
is demonstrated, but native RF activation, decoded reception and capture
processing remain unverified**. Do not mistake ARPHRD 803 on a transient,
unactivated VIF for actual RF capture.

See [NATIVE_VIF_TRIAL_02080F0_20261017.md](NATIVE_VIF_TRIAL_02080F0_20261017.md)
for exact current physical evidence. The new source commits after the frozen
Pi image are **not** deployed merely by passing GitHub CI. Only a
1920×1080 framebuffer has been observed, not the 480×320 TFT; Pi 5 remains
physically untested.

## Shortest safe critical path to a genuinely usable Pi 4

1. **P0 RF:** Native temporary VIF creation and radiotap link type **803**
   now passed on the real Pi 4; runtime per-device NM isolation is reproducible
   and original-state restoration has been verified. **The remaining immediate
   blocker is positive ownership evidence** for a newly created monitor VIF
   not yet enumerated by NetworkManager. Investigate source-only alternate
   read-only verification, independently review it, then request a *new*
   bounded hardware approval. Do not bypass the unknown-ownership stop or
   attempt activation/receive without a reviewed ownership proof.
2. **P0 capture:** If native 802.11/radiotap framing and *decoded packets*
   are measured, then validate closed PCAP rotation/handoff and honest
   ingest/conversion status using only the operator's test AP. Empty scans
   cannot close this gate.
3. **P1 screen:** Diagnose the missing 480×320 framebuffer and input device
   from read-only inventory; select/configure the actual TFT overlay only
   with operator approval, then physical pixel and touch verification.
4. **P1 stability:** Cold-boot persistence, recovery after controlled faults,
   at least a 24-hour soak; check no capture or state loss.
5. **P2 release:** Consolidate verified fixes into a fresh signed ARM64 build
   **only when required**, qualify it, and test the actual replacement image.

Codex should work on physical tests and laptop builds; chat-side lead on
independent source fixes and CI. Keep the existing Pi stable between gates.
This prevents serial idle time, speculative reconfiguration and unnecessary
image rebuilds. No percentage advances for undocumented assumptions.

## Weighted assessment

| Workstream | Weight | Approx. completion | Current evidence / biggest gap |
|---|---:|---:|---|
| Core architecture and runtime logic | 12% | 85% | Scope, capability graph, Augur/Doctor, bus and orchestration are implemented and extensively tested; production lifecycle needs work |
| Radios, Bettercap and capture processing | 20% | 70% | Live supervisor + passive processing and recovery integrated; real standalone Bettercap handshake and hardware-specific validation incomplete |
| Detection, intelligence and supporting subsystems | 18% | 70% | Broad modules and synthetic coverage; real multi-feature signal flow and physical sensors need validation |
| Image, boot, storage and recoverability | 15% | 70% | Signed image independently qualified and Pi 4 booted; endurance, rollback and recoverability remain unverified |
| Web/TFT and operational UX | 15% | 42% | Browser and RGB565 code pass CI; real panel not enumerated, physical pixels/touch and runtime controls unverified |
| Physical Pi 4/5 verification | 12% | 35% | Signed standalone image boot, provenance/storage/services verified on Pi 4; native RF fails and TFT/Pi 5/endurance unverified |
| Release, documentation and packaging | 8% | 50% | Verified signed compressed artifact, manifest, Pi 4 field runbook and CI; field acceptance/rollback still open |

Weighted estimate is roughly **62%**; expressed as **~60–65%** to avoid false precision.
No percentage is promoted solely by adding passing unit tests.

## Demonstrated versus pending

- **Demonstrated in GitHub CI:** cross-module Python tests, shell/source/image staging, structural image verifier synthetic fixtures, real driver API URL construction, artificial failure/retry and pipeline end-to-end simulation.
- **Recorded Pi 4 physical evidence:** the earlier 2026-10-08 passive test processed 703 real frames, observing two APs and three identities **outside** the final standalone image. On 2026-10-12 the owner built and qualified a separate ARM64 field image, flashed a disposable card, booted it on the Pi 4 and logged in. Two on-device reports observed Redux services active, a mounted REDUXCAP ext4 partition, US regulatory domain, monitor-labeled wlan0, an authenticated Bettercap loopback listener, and event counters increasing from 6 to 12. Those reports did **not** establish native frame reception, a closed nonempty PCAP, or valid handshake material.
- **Current Pi 4 blocker (updated October 17):** Earlier native-interface testing failed with `-16` busy, and the loaded signed-image brcmfmac patch was independently verified. After scoped isolation, `iw phy ... interface add ... type monitor` **succeeded** on the Pi and the temporary interface was measured as **radiotap ARPHRD 803** on phy0. The strict post-create gate stopped because NM did not enumerate the VIF (`temp_nm_managed=null`), so ownership remained UNKNOWN; activation and decoded packets were **not tested**. The original `wlan0` remains Ethernet ARPHRD 1/firmware0 after fully verified rollback. No capture or handoff was proven. See `docs/NATIVE_VIF_TRIAL_02080F0_20261017.md`.
- **Unverified hardware-dependent gates:** real 802.11/radiotap ingress in this Redux image, native driver interface lifecycle, supported kernel/Nexmon compatibility, capture-to-hcx conversion against an operator-owned AP, Pi 5 image build/boot, full TFT and touch, sustained faults/restarts, OTA/UPS, SDR/CSI and optional audit hardware.
- **New software-only operational capability (2026-10-12):** `redux pipeline recover` now inspects previously failed conversions and missing prepared outputs without mutation, and `--apply` performs bounded, lock-coordinated retries without deleting capture evidence. Doctor links measured conversion errors to this operator workflow. Python/CLI integration is CI-tested; real ARM64 converter and power-loss behavior still require field qualification.
- **New native screen build milestone (2026-10-13):** the ARM64 image now packages a lightweight browser-free RGB565 dashboard with actual runtime/Doctor/capture telemetry, plus optional manually selected ADS7846/XPT2046 swipe paging. A disabled systemd service, persistent operator-config template and rootfs export checks ensure no accidental TFT startup before hardware qualification. Source/rendering/synthetic touch/image-stage/root payload checks pass CI; no LCD pixels or tap calibration have been verified on a real Pi yet.
- **Product integration remains:** user-visible control/config workflows, multi-subsystem event coherence, broader storage housekeeping, update/rollback, permissions, status, sustained-use recovery and quality.

## Milestones remaining (sequence, not a percentage promise)

1. **Reliable passive standalone image:** ARM64 build and Pi 4 first boot now observed; finish verified native radiotap ingress, storage capture handoff, ingestion, reliable driver lifecycle/recovery, and Pi 5 boot.
2. **Field-stable device:** physical unplug/hotplug tests, recovery after reboots and near-full SD, first-class storage management, 24–72-hour soak testing and actionable diagnostics.
3. **Great user interface:** live dashboard and actual Pi TFT touch interaction, runtime controls, feature discovery and settings that show real telemetry and states.
4. **Full-capability integration:** qualify remaining sensors, blue/purple features, packs, sync/OTA and all permission boundaries on real supported hardware.
5. **Release candidate:** documented flash/upgrade/restore process, privacy checks, checksummed image, hardening review, owner acceptance and rollback.

## Current development boundary

Active development is integrated directly on `human/redux-workbench`, shared
by the chat-side engineering lead and the owner's WSL Codex agent.
Do **not** merge to or push to `main`. Historical PR
[#144](https://github.com/patrickato/pwnagotchi-redux/pull/144) is not the
active workspace. A physically booted Pi 4 image is a meaningful but
partial qualification, not proof of a production-ready radio/capture stack.

A conservative, manual-only raw capture retention command now exists:

```bash
redux pipeline --config /etc/redux/pipeline.toml prune --older-than-days 30
redux pipeline --config /etc/redux/pipeline.toml prune --older-than-days 30 --apply
```

It requires both source and prepared artifact hashes to match and leaves
converted records intact. Deletion is **never automatic** on image boot.
