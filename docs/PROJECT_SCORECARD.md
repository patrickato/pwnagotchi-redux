# Redux completion scorecard — updated 2026-10-12

> **Overall estimate: approximately 55% complete / 45% remaining** for the
> ambitious **standalone Augur / Redux v1 product**, not just the capture MVP.
> This is a judgment-based engineering estimate (rough uncertainty ±10 points),
> not a code-line, test-count, or checklist-average calculation.
> Working code, device-tested features, and release readiness are different.

## Weighted assessment

| Workstream | Weight | Approx. completion | Current evidence / biggest gap |
|---|---:|---:|---|
| Core architecture and runtime logic | 12% | 85% | Scope, capability graph, Augur/Doctor, bus and orchestration are implemented and extensively tested; production lifecycle needs work |
| Radios, Bettercap and capture processing | 20% | 70% | Live supervisor + passive processing and recovery integrated; real standalone Bettercap handshake and hardware-specific validation incomplete |
| Detection, intelligence and supporting subsystems | 18% | 70% | Broad modules and synthetic coverage; real multi-feature signal flow and physical sensors need validation |
| Image, boot, storage and recoverability | 15% | 55% | Pi-gen, nexmon build scripts, overlay, release verification and source retention exist; full production image build/boot/recovery not complete |
| Web/TFT and operational UX | 15% | 40% | Web/status and TFT modules exist; actual touchscreen UX, live controls, accessibility and field workflows need polish |
| Physical Pi 4/5 verification | 12% | 20% | Limited recorded Pi 4 passive field test exists; complete Pi 4 and Pi 5 image, RF, peripherals, stress and fault tests pending |
| Release, documentation and packaging | 8% | 30% | CI, docs and release manifests exist; reproducible artifacts, installer, field docs and acceptance gates incomplete |

Weighted estimate is roughly **56%**; expressed as **~55%** to avoid false precision.
No percentage is promoted solely by adding passing unit tests.

## Demonstrated versus pending

- **Demonstrated in GitHub CI:** cross-module Python tests, shell/source/image staging, structural image verifier synthetic fixtures, real driver API URL construction, artificial failure/retry and pipeline end-to-end simulation.
- **Recorded Pi 4 physical evidence:** the earlier 2026-10-08 passive test processed 703 real frames, observing two APs and three identities **outside** the final standalone image. On 2026-10-12 the owner built and qualified a separate ARM64 field image, flashed a disposable card, booted it on the Pi 4 and logged in. Two on-device reports observed Redux services active, a mounted REDUXCAP ext4 partition, US regulatory domain, monitor-labeled wlan0, an authenticated Bettercap loopback listener, and event counters increasing from 6 to 12. Those reports did **not** establish native frame reception, a closed nonempty PCAP, or valid handshake material.
- **Current Pi 4 blocker:** controlled native-interface testing subsequently failed before packet sampling; cleanup restored firmware mode but `wlan0` could not return to monitor mode because the installed brcmfmac driver reported `-16` busy. The runtime was recorded **degraded**, not restored. Nexmon source fix `0f01d80` is **not deployed**; CI only validates source logic. See `docs/IMAGE_BUILD.md` and `docs/AGENT_WORK_QUEUE.md`.
- **Unverified hardware-dependent gates:** real 802.11/radiotap ingress in this Redux image, recovery of the current native driver, supported kernel/Nexmon compatibility, capture-to-hcx conversion against an operator-owned AP, Pi 5 image build/boot, full TFT and touch, sustained faults/restarts, OTA/UPS, SDR/CSI and optional audit hardware.
- **Product integration remains:** user-visible control/config workflows, multi-subsystem event coherence, storage housekeeping, update/rollback, permissions, status, recovery and sustained-use quality.

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
