# Integrated beta readiness and development handoff

This branch is a software beta workbench, not a physically qualified release.
Sprint 04 began at `1b59049`: the complete baseline had 2,842 passing tests,
including the receipt owner's escaped-newline fixes. No Pi connection or mutation
was used for this sprint. Keep all work on `human/redux-workbench`; no PR or main
changes. Preserve concurrent commits and the capture-integrity owner's files.

## Executable acceptance matrix

```sh
python3 scripts/dev-preflight.py
python3 scripts/redux-qualify.py
python3 scripts/redux-qualify.py --beta --beta-report /tmp/redux-beta-new.json
```

The validation WSL session used locally extracted RAUC/mtools and Node tools,
without global installation. If these temporary directories still exist, the
matching command environment is:

```sh
PATH="/tmp/redux-release-tools/root/usr/bin:/tmp/redux-js-tools/node-v22.22.1-linux-x64/bin:$PATH" \
LD_LIBRARY_PATH=/tmp/redux-release-tools/root/usr/lib/x86_64-linux-gnu \
python3 scripts/redux-qualify.py --beta --beta-report /tmp/redux-beta-new.json
```

Temporary tools can disappear across host cleanup. Do not bypass preflight if they
are absent; use the documented safe development setup for the missing tools.
Nothing here installs software globally or changes the Pi.

Use a new report filename each time. No command above uses SSH, sudo, deployment,
RF traffic or a physical device. Consult `REDUX_QUALIFICATION.md` for local tool
installation instructions. Node.js is needed for the actual embedded JavaScript
behavior tests; a skipped UI test leaves the beta UI gate UNKNOWN.

| Gate | Execution evidence | Meaning |
|---|---|---|
| Offline software | Full pytest, compilation, shell syntax, diff checks | Required software checks passed |
| Integration tests | Same full run's JUnit cases for platform lifecycle, live runtime, telemetry | Named scenarios executed, no failure or skip |
| UI behavior | Same JUnit cases for canonical state, browser behavior, native gestures and reconnect | Headless software contracts passed |
| Image contracts | Existing `bash image/ci.sh` | Image/update synthetic contracts passed |
| Release candidate | UNKNOWN in this command | Run `scripts/redux-release.py candidate` against actual signed artifacts |
| Physical RF/display | UNKNOWN | Pi 4/5 reception, framing, conversion/handoff and display/touch acceptance required |
| Actual update/rollback | UNKNOWN | Approved install, failed-boot fallback and recovery acceptance required |

`--beta` reuses the full suite's private temporary JUnit report, so it does not
run a second set of tests. Missing, malformed, skipped or incomplete named evidence
cannot pass. Report schema 1 includes source revision, dirty-worktree flag,
assessment time, per-gate reasons, `offline_qualified` and `beta_ready`.
`beta_ready` remains false: this command cannot certify physical gates. The JSON
contains no captures, keys, SSIDs or MAC addresses.

Normal qualification retains exit 0 for completed software checks. Beta mode exits
3 when software passed but release/physical gates remain UNKNOWN; failures take
precedence and exit 1. Field transport retains exit 2. Do not hide exit 3 in CI:
it means incomplete acceptance, not a failed test or a certified release.
`--preflight-only --beta` leaves unexecuted regression gates UNKNOWN.

## Integrated runtime behavior

`ResourceMonitor` remains the sole hardware sampler shared across Augur and engine
generations. Observer exceptions invalidate freshness and record an error class,
with a two-second retry bound. Old measured values may remain visible as stale;
missing battery/power is never promoted to healthy. Governor thresholds, recovery
hold and existing interval behavior are unchanged. No capture-control reaction was
added for observer failures.

The supervisor publishes complete initial Doctor/telemetry/processing state even
before an engine is available. Checkpoints atomically publish canonical `ui` along
with existing producers. The HTTP handler refreshes presentation ages from the
same pure model without sampling hardware or opening the ledger. Provider/JSON
failure returns sanitized HTTP 503 and the browser's existing stale handling.

Visual failures retry at `status_sample_seconds`; processing summaries, including
unavailable results, use that same bound. Fixed-key `runtime.observer_errors`
records unavailable visual, processing, Doctor, resources and radio-inventory
producers. Telemetry and Governor snapshots come directly from the shared monitor,
so cached visuals or failed Augur presentation adapters cannot overwrite them. Doctor reports their coverage as UNKNOWN. Diagnostic errors
do not become engine restart reasons. Radio-inventory thread startup failure is
isolated and retries at the existing ten-second bound.

`runtime.persistence_error` is separate from engine `last_error`. A failed
checkpoint appears immediately in live Doctor/UI state, clears after a successful
write and does not overwrite the engine diagnosis. `live.json` adds a small
`observability` summary (sensor timestamp/freshness, visual freshness and ledger
availability), not full UI data or capture contents. Persistence remains at most
once per five seconds except real state transitions. Engine logs failing to close
cannot prevent owner-lock release. Shutdown is idempotent and gives observer/web
threads bounded joins; a custom blocking provider must still bound its own I/O.

Doctor and native capture rendering share `core/processing_evidence.py` heartbeat
validation. Per-pass outcomes must match scanned counts; low-storage pause uses
its documented zero-file record. Each consumer retains its existing stale-age
threshold. Contradictory counters cannot look like a completed healthy pass.
Lease-blocked and termination-pending states display ACTION consistently; the
underlying safety gates and explicit USB re-arm requirement are unchanged.

## Touch recovery boundaries

Only the operator-selected `/dev/input/eventN` is retried, every five seconds,
after a successful initial open. Kernel input ID and physical path must match.
Different identity candidates are closed. Recovered calibrated input requires
an observed release and a new contact, so partial old gestures cannot navigate.
No device discovery, calibration changes, grabbing or input injection occurs.

If initial identity cannot be read, automatic reconnect is disabled. If input is
absent at startup, the display continues, but the operator must verify the node
and restart the UI after connection. Kernel ID plus port is not a unique serial
identity; an identical replacement controller cannot be distinguished by these
fields. Physical acceptance must establish mapping and reliability.

## Continue independently

1. Verify clean status and `git pull --ff-only`; inspect concurrent commits.
2. Run the three offline commands above. Review the exact failures and JSON
   matrix; do not assume a previously reported defect still exists.
3. Focus regression work on `tests/test_platform_lifecycle.py`,
   `tests/test_touch_reconnect.py`, `tests/test_beta_readiness.py`, existing runtime
   tests and `tests/test_web_refresh.py`. New scenarios should cross producer,
   Doctor, canonical UI and durable-state boundaries.
4. Preserve capture-integrity ownership: `integrity_receipt.py`, its tests,
   the capture audit script/tests and integrity documentation belong to the other
   engineer. Consume documented interfaces instead of creating another receipt
   system. No new receipt collection was added here.
5. For release work, follow `RELEASE_QUALIFICATION.md` using actual artifact inputs.
   Offline image contracts alone do not bind or authenticate a release candidate.
6. Physical testing requires separately scoped operator authorization. Read
   `FIELD_ACCEPTANCE.md`, `LOCAL_AGENT_HANDOFF.md` and `UI_OBSERVABILITY.md`; queue
   remaining RF/display and install/rollback gates without executing them.
7. Commit tested changes directly to the shared workbench and push without force.

Hardware measurements, packet reception, handshakes, physical display readability,
controller operation and real update/rollback remain unverified by this sprint.

## Release factory continuation

Sprint 05 adds `scripts/redux-release.py factory preflight|build|verify|inspect`.
Use [RELEASE_FACTORY.md](RELEASE_FACTORY.md) for exact source/profile/signing,
private-workspace, provenance, artifact verification and recovery contracts.
The factory never elevates privileges or deploys. A successful source test or
synthetic fixture remains separate from a complete real image and distributable
signed release; actual Pi boot/rollback gates stay UNKNOWN.

## First-boot and hardware evidence continuation

Sprint 06 adds the offline `scripts/redux-acceptance.py` workflow. See
[HARDWARE_ACCEPTANCE_KIT.md](HARDWARE_ACCEPTANCE_KIT.md) for preflight, bounded
evidence, replay, independent gate evaluation/comparison, external attestation
and source/candidate integration. Its explicit local collector is read-only,
stdout-only and is not deployed or authorized to run on the Pi by this handoff.
Unsigned/synthetic reports and missing physical observations remain UNKNOWN.
