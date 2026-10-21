# Redux source-only autonomy queue

This file is the machine-readable human/ChatGPT-to-local-Codex handoff.
The enabled local Codex timer fetches the current workbench automatically
and may complete **one** clear source-only item per cycle. Mark completed
items `[x]` with a concise test note; choose remaining work based on
dependency and actual source state. Keep already-completed evidence intact.

## Current highest-priority offline engineering tasks

- [ ] **P0 — Verified private hardware-to-Codex feedback:** A fixed-category
  no-SSH converter is implemented in `redux/field_feedback.py` and
  `scripts/redux-field-feedback.py`. A separate complete nested-schema
  validator is now enforced at the Codex-ingestion boundary, with regressions
  for injected private fields and malformed JSON. This needs clean new CI
  evidence before counting it accepted. The earlier 16 isolated feedback
  fixture tests passed, but they are not physical Pi proof. The source-only Codex runner contains an *opt-in* filtered
  feedback channel, but its existing installed timer does not enable
  transmission. Qualify the full test suite, validate that at least one
  real private observer report is available and fresh enough to guide work,
  then arrange a deliberate WSL upgrade that does not silently invalidate
  the currently pinned collector authorization. The **frozen-collector
  migrator** is now implemented in `scripts/freeze-redux-field-bridge.py`,
  with synthetic no-SSH systemd/revision tests. It reconstructs the pinned
  approved revision as a private detached worktree and rebinds only the local
  observer service; it does not reset attempts, alter expiry or collect data.
  The owner ran the migration in WSL on October 21: **8 tests passed**
  and the tool returned `result=frozen`, `observer_service_updated=true`,
  unchanged approval, timer and consumed-session budget. The timer showed a
  prior 11:24 EDT trigger with no result. A subsequent **local offline
  doctor report supplied by the owner on October 21** verified:
  `last_recorded_collection.status=observed`,
  `private_report_saved=true`, `sessions_used=4`,
  `sessions_remaining=36`, systemd `exit_code=0`,
  `last_result=success`, and expected frozen service/timer identity.
  Thus an actual automated read-only metadata snapshot succeeded (but
  **not** RF decoding, handshake authentication, TFT validation or
  hardware release acceptance). The source-only feedback filter rejects
  reports older than two hours. A consent-gated, user-service-only
  feedback toggle now exists in `scripts/configure-redux-codex-feedback.py`.
  **The user enabled it on WSL October 21**: 26 offline tests passed,
  the consent controller returned `sanitized_model_feedback=on`, and
  a subsequent status check returned `on`. Consent was granted for
  fixed-category cloud model ingestion independently of metadata
  observation. Confirm at least one *subsequent* Codex source cycle
  actually consumes a fresh saved category summary; an enabled timer alone
  does not prove the data handoff or an automatic fix.
  Never share raw private reports, SSH credentials, identifying network
  data or Pi commands.
- [x] **P0 — Hardware bridge initial offline qualification:** The owner
  reported **29 passing** field bridge tests and successful explicit
  recurring read-only enrollment plus separate user-timer installation on
  October 21. The collector was not modified; tests do not attest actual
  hourly SSH success. Real reports stay private in the WSL environment.
  No extra RF, Pi-write, reboot or capture-audit permissions were granted.
- [ ] **P0 — Codex source-cycle reliability:** One real unattended
  WSL Codex attempt on October 21 reported `status=blocked`,
  `reason=python_tests_failed`, `changed_count=3`, with no pushed
  changes. CI remained green on the accepted workbench. A bounded
  one-time sandboxed Codex repair, independent retest and no-push on
  repeated failure is now implemented in `scripts/redux-autopilot.py`.
  Do not claim this self-repair is proven until its CI passes and a
  subsequent WSL cycle publishes successfully. Codex never receives raw
  pytest logs from the trusted wrapper.

## Hardware-automation safety milestone (source-only)

A separate private, time-limited read-only Pi controller and **hourly**
opt-in user timer are now in the source tree (scripts/redux-field-bridge.py,
tests/test_redux_field_bridge.py). Its code/revision pins, strict-host-key
existing collector, maximum session count, no-retry behavior, local-only
0600 reports, and separate WSL approval are not themselves real Pi results.
The owner approved read-only monitoring on October 21 at an accelerated
**hourly** cadence with no more than **40 total attempts** and **14 days**
of validity, whichever ends first. Source-only Codex must not expand this
approval to any state change or additional RF/capture operation.
The owner **activated the local recurring approval and hourly WSL timer**
after all 29 setup tests passed. Its first successful unattended Pi collection
has not yet been independently established from connected GitHub evidence.
Do not extend the source-only Codex timer to run SSH or add credentials. Future
work: dedicated unprivileged OS account with Pi forced-command boundary,
safe redacted evidence-return channel, CI/hardware contract tests, and
separately approved physical experiment manifests and rollback procedures.

- [x] **P0 — Prepared artifact audit source hardening:** The existing
  `scripts/capture-artifact-audit.py` rechecks the SQLite ledger inode
  immediately after DB open, after ready-row selection and after hashing,
  with synthetic swap tests. An operator can select `--max-ready-rows 1`
  and refuse a larger ledger **before hashing** any prepared output; the
  offline `--plan` states that limit. GitHub Python and image CI have
  passed these additions. This is **software-ready, not field-verified**.
  The owner separately approved **one** non-root read-only integrity
  audit of at most one prepared `.hc22000` file on October 21. A one-shot
  strict-SSH launcher, in-memory remote audit, REDUXCAP mount identity
  checks and consumed-before-contact local marker are implemented in
  `scripts/redux-one-artifact-field-audit.py` with offline synthetic
  tests. After the final CI run passes, execute exactly once from WSL,
  inspect only sanitized aggregate results, and stop on UNKNOWN/ATTENTION.
  No PI-side run has occurred yet; SHA file-integrity evidence will NOT
  prove handshake authenticity, received 802.11 frames, or release PASS.
- [ ] **P0 — Physical prepared-artifact audit blocked by ownership:**
  The user performed the separately approved, one-shot non-root audit on
  October 21. It returned `status=attention`, `checked=0`,
  `ready_rows=0`, `reason=artifact_integrity_problem`, with no verified
  bytes. This is an **early audit failure**, not proof of an empty ledger
  or corrupted file. The worker service is `User=root`, with
  `/captures/ready` mode 0700 and `jobs.db` 0600, so the `pi` SSH audit
  identity likely lacks access. A source-only diagnosis fix now classifies
  read denial as UNKNOWN before hashing, with synthetic tests. The old
  one-shot approval was consumed: **do not retry, delete the local marker,
  chmod/chown Pi artifacts, or grant sudo**. Develop and independently
  validate a narrowly constrained, separately approved privileged read-only
  option, or wait for a controlled disposable-image upgrade. Keep raw
  captures and prepared authentication bytes private.

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
