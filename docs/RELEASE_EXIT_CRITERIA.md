# Redux release exit criteria — product-first engineering

**Status: historical evidence reviewed October 21, 2026.**
This file identifies the minimum independently observable gates for
a finished, trustworthy standalone Redux / Augur release. Closing a
gate requires the prescribed real evidence, not just a passing test,
simulated hardware snapshot, feature count, or UI screenshot.

The owner wants a complete product. Engineering work should choose
high-value implementation+tests from the live queue in `TASKS.md`; keep
independent release gates closed until there is actual supporting evidence.
Maintain fail-closed unknowns. Do not expand scope or operate the Pi
without explicit approval.

| Priority | Release gate | Current evidence | Closure evidence | Status |
| --- | --- | --- | --- | --- |
| P0 | Pi4 802.11 reception | Onboard ARPHRD1; USB ARPHRD803 in monitor-label mode, NM managed; zero delta in one-second sysfs sample | Decoded real 802.11 reception on an owned test AP with pinned interface/ownership, US regulatory and rollback, separately approved passive measurement | **UNKNOWN** |
| P0 | Capture and conversion | Five ledger rows: one ready, four invalid; last five scans duplicates | Closed nonempty capture provenance, unbroken handoff, independent SHA-256/inode audit of prepared output, consistent conversion evidence, no unsafe raw-content publication | **UNKNOWN** |
| P0 | Clean signed deployable release image | Older signed Pi image `4f907ff` booted, current source branch ahead | Fresh reproducible signed image with exact source manifest, offline image verification and expected ARM64 modules, booted from independently identified disposable card | **PARTIAL** |
| P1 | TFT and touch | Only 1920x1080 framebuffer; 480x320, ILI9486 and touch candidates missing | Correct physical 480x320 pixels, navigation, touch corners and mapping, no HDMI fallback, measured performance and recovery | **UNKNOWN** |
| P1 | Native UX and plugin lifecycle | Native source rendering and plugin-pack tests; not installed in Oct 20 field image | Real device status/config/plugin controls, on/off state correctness, failure/restart recovery and telemetry matching producers | **UNKNOWN** |
| P1 | Stability and storage | Verified overlay/lower/boot/captures mounts, ~449 MiB available during one snapshot | At least one 24h stable run followed by 72h candidate soak and approved restart/fault/recovery; bounded storage behavior and no data loss | **UNKNOWN** |
| P1 | Security and scope | Source control gates and privilege tests | Independent security/privacy review, least privilege, authorization scope controls, redaction, safe defaults, no credential leaks | **UNKNOWN** |
| P1 | Update and rollback | Offline RAUC/source contracts, no independent real A/B rollback | Explicitly authorized, measured signed update and failed-update rollback with captured state invariants | **UNKNOWN** |
| P2 | Pi5 and optional hardware | Synthetic Pi5/image contracts but no booted Pi5 sample | Actual Pi5 boot, chosen supported peripherals and tested renderer/radio compatibility | **UNKNOWN** |
| P2 | Distribution and support | Signed artifact history and field docs | Reproducible installation, operator guide, recovery procedure, checksums, troubleshooting and support-ready diagnostics | **PARTIAL** |

## Release policy

- **Pi4 usable beta**: P0 RF, capture and image gates must close with
  hardware proof. TFT may still be separate when explicitly labeled headless.
- **Pi4 full-feature candidate**: Pi4 beta gates plus physical TFT/touch,
  operational UI/plugins, significant soak, security/privacy and recoverability.
- **Full Redux/Augur v1**: All mandatory supported-platform gates,
  signed distribution and real rollback; Pi5 inclusion is conditional on
  physically available hardware and declared support matrix, not assumed.
- **Optional future expansions**: Additional modules, SDR, peripherals
  and integrations should not indefinitely defer a qualified stable core.

No new feature earns a higher project completion estimate unless it closes a
weighted end-product workstream or removes a measured blocker. This does not
discourage imaginative enhancements; it gives them a stable platform to join.

## Velocity and forecast baseline

As of October 21, 2026, the conservative broad-platform score remains
**60–65% complete (approximately ±10 percentage points)**. Source autonomy
is enabled, but no unattended end-to-end WSL Codex cycle has yet been
independently demonstrated from the visible record.

After applying the two-hour timer migration, the local WSL runner has up
to twelve *attempted* source-only cycles per day while the host and user
systemd are awake. ChatGPT's GitHub-side engineering task is separately
scheduled every two hours. Concurrency guards reject competing GitHub
writes; long tests and CI can limit throughput. Measure real accepted commits,
CI green rates, reopened defects, and hardware gates closed before revising
this forecast.

Planning windows (subject to field hardware and driver risks):

- Pi4 usable beta: 4–8 weeks from October 21.
- Pi4 TFT/UI candidate: 6–12 weeks.
- Stable broad Redux v1: 3–6 months, contingent on needed supported hardware.
- Expanded optional modules and Pi5 coverage: 6–12+ months.

These are decision-support scenarios, not guaranteed delivery dates.
