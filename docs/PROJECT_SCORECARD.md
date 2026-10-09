# Redux completion scorecard — 2026-10-09

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
- **Recorded limited physical evidence:** the repository README cites a Pi 4 passive capture test (2026-10-08) that processed 703 real frames, observing 2 APs and 3 device identities. This is *not* an end-to-end standalone Redux image boot or a Bettercap handshake qualification.
- **Unverified hardware-dependent gates:** successful full pi-gen arm64 image creation and boot on both Pi 4 and Pi 5; native kernel/Nexmon driver behavior; startup radio transition and connected uplink preservation; live Bettercap-to-hcx conversion of a controlled owned AP sample; TFT and touch; long-term power and storage faults; OTA/UPS, SDR/CSI, optional audit hardware.
- **Product integration remains:** user-visible control/config workflows, multi-subsystem event coherence, storage housekeeping, update/rollback, permissions, status, recovery and sustained-use quality.

## Milestones remaining (sequence, not a percentage promise)

1. **Reliable passive standalone image:** complete ARM64 build, first boot, mount checks, live passive capture, ingestion and health/status with no radio competition.
2. **Field-stable device:** physical unplug/hotplug tests, recovery after reboots and near-full SD, first-class storage management, 24–72-hour soak testing and actionable diagnostics.
3. **Great user interface:** live dashboard and actual Pi TFT touch interaction, runtime controls, feature discovery and settings that show real telemetry and states.
4. **Full-capability integration:** qualify remaining sensors, blue/purple features, packs, sync/OTA and all permission boundaries on real supported hardware.
5. **Release candidate:** documented flash/upgrade/restore process, privacy checks, checksummed image, hardening review, owner acceptance and rollback.

## Current development boundary

Owner-driven PR [#144](https://github.com/patrickato/pwnagotchi-redux/pull/144)
uses branch `human/redux-capture-pipeline-integration`; `main` remains
protected and no development image is being represented as flash/field qualified.

A conservative, manual-only raw capture retention command now exists:

```bash
redux pipeline --config /etc/redux/pipeline.toml prune --older-than-days 30
redux pipeline --config /etc/redux/pipeline.toml prune --older-than-days 30 --apply
```

It requires both source and prepared artifact hashes to match and leaves
converted records intact. Deletion is **never automatic** on image boot.
