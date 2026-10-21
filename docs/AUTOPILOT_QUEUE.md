# Redux source-only autonomy queue

This file is the machine-readable human/ChatGPT-to-local-Codex handoff.
The enabled local Codex timer fetches the current workbench automatically
and may complete **one** clear source-only item per cycle. Mark completed
items `[x]` with a concise test note; choose remaining work based on
dependency and actual source state. Keep already-completed evidence intact.

## Current highest-priority offline engineering tasks

- [ ] **P0 — Hardware bridge source qualification (OFFLINE ONLY):**
  Test scripts/redux-field-bridge.py and its installer in isolated Linux
  fixtures, run all tests/test_redux_field_bridge.py, check shell syntax,
  audit source revision / SSH identity pinning, consent/renewal, policy
  symlink/race safety, and confirm the existing acceptance collector is not
  weakened. Fix actual failures with regression tests. Do **not** execute
  observe, enroll, an SSH login, or enable the field timer.
  Source-only completion is a prerequisite to requesting recurring
  read-only operator consent, not permission to test the Pi.
## Hardware-automation safety milestone (source-only)

A separate private, time-limited read-only Pi controller and six-hour
opt-in user timer are now in the source tree (scripts/redux-field-bridge.py,
tests/test_redux_field_bridge.py). Its code/revision pins, strict-host-key
existing collector, maximum session count, no-retry behavior, local-only
0600 reports, and separate WSL approval are not themselves real Pi results.
The owner approved read-only monitoring on October 21 at an accelerated
**hourly** cadence with no more than **40 total attempts** and **14 days**
of validity, whichever ends first. Source-only Codex must not expand this
approval to any state change or additional RF/capture operation.
Neither the controller nor its timer has been activated by the owner. Do not
extend the source-only Codex timer to run SSH or add credentials. Future
work: dedicated unprivileged OS account with Pi forced-command boundary,
safe redacted evidence-return channel, CI/hardware contract tests, and
separately approved physical experiment manifests and rollback procedures.

- [ ] **P0 — Prepared artifact-integrity readiness:** Review the existing
  `scripts/capture-artifact-audit.py` and `redux.crack.integrity_receipt`.
  Improve offline tests and read-only permission/race handling for the
  *single ready* ledger row observed on the Pi 4. A bounded no-I/O `--plan`
  with synthetic tests was committed to `capture-artifact-audit.py` on
  October 21; do not duplicate that completed subtask. Verify remaining
  ledger, file replacement, and concurrency edge cases source-only.
  **Do not read the real Pi ledger, open captures or run SSH.**
  A physical audit requires its own owner-approved read-only execution.
- [ ] **P0 — Native monitor-VIF NetworkManager evidence:** Investigate source-only
  alternatives to NM's missing netdev enumeration, without assuming
  `not enumerated = unmanaged`. Build only parsers, independent evidence
  checks and fail-closed fixture tests. No interface creation, activation,
  receive socket or NetworkManager change on any hardware.
- [ ] **P1 — USB-radio passive qualification plan:** Adapt existing Alfa
  readiness/safety contracts to the observed state: USB ARPHRD 803,
  NM managed/state20, potential capture process ownership. Prepare
  bounded approved-only process/socket/NM preflight and rollback
  specification and synthetic regressions. Do not collect RF samples.
- [ ] **P1 — 480x320 TFT source diagnosis:** Improve read-only display
  overlay/SPI/input metadata selectors, blocker explanations and
  fixture tests for the observed 1920x1080 framebuffer with absent
  ILI9486 and touch candidates. Do not apply a boot overlay.
- [ ] **P1 — Plugin pack lifecycle:** Expand lifecycle/failure-path and
  published status regressions in the post-`4f907ff` code; leave native
  plugins disabled by default and do not claim on-Pi deployment.
- [ ] **P2 — Release-candidate comparability:** Improve offline evidence
  for the gap between old Pi image `4f907ff` and current branch, including
  source and artifact provenance. No flashing or SD modification.

## Required completion format

Make the code and tests in the *same* cycle, run focused tests, and mark the
chosen item complete only if its bounded acceptance tests truly pass. Include
follow-on tasks in this queue when needed. The **wrapper** independently runs
the full Python suite and `image/ci.sh` before it pushes. Its tests can still
reject the change; a failed cycle is not an accepted implementation.

Never post private Pi telemetry, capture content, credentials, LAN identifiers,
or unredacted logs here. Source-only tests do not close hardware gates.
