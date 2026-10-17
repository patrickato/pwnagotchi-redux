# Redux: local hardware agent handoff (ASUS Ubuntu WSL)

This is an **optional** way to accelerate physical development. A local
Codex CLI agent runs inside the owner's Ubuntu WSL workspace, can inspect
the actual Pi over SSH, edit the checked-out Redux repository, run tests,
and share code progress through the existing GitHub workbench branch.
A cloud ChatGPT conversation cannot directly access the owner's
`192.168.x.x` network or the owner's private SSH key.

## Trusted baseline

- GitHub repository: `patrickato/pwnagotchi-redux`.
- Sole development branch: `human/redux-workbench`; **never** merge to or
  push to `main`. Use direct tested commits to the workbench.
- ASUS/Ubuntu checkout: `~/redux-workbench-src`.
- Disposable physical target: separate **Redux Pi 4 8 GB** SD card. Do not
  touch or reflash the separate working Jayofelony Pwnagotchi card.
- Previously observed Pi LAN address: `192.168.12.115` (DHCP can change).
- Owner's WSL-only SSH identity: `~/.ssh/redux_field_ed25519`. Never
  display, copy, upload, log or transmit its private contents.
- 2026-10-12 field image built from `redux-field-20261012-125118`.
  The image booted on a real Pi 4 with a local console login and
  `sudo redux-report --full`.
- Two field snapshots roughly 15 minutes apart: `redux.service`,
  `redux-live.service`, and `redux-capture-ingest.timer` active;
  `live_state: running`; `wlan0` monitor on channel 11; global country
  US; Wi-Fi unblocked; dedicated REDUXCAP ext4 mounted; Bettercap REST
  loopback unauthenticated status 401 (as expected); `failure_streak: 0`;
  `events_seen: 6 -> 12`; `engine_attempts: 2 -> 4`;
  `handed_off: 0`. These observations do NOT prove real handshake capture.
- Expected default rotation `rotation_seconds=300`; each rotation may
  increment engine_attempts. The handoff code refuses a missing/zero-byte
  .pcap, so zero handoffs alone is not evidence of an error.
- Hardware observations recorded in `docs/IMAGE_BUILD.md`. Read
  `AGENTS.md`, `docs/WORKBENCH.md`, `docs/FIELD_ACCEPTANCE.md`,
  `docs/LIVE_RUNTIME.md`, and the most recent workbench commits before
  changing code.

## Current native-radio gate — 2026-10-12 (supersedes bootstrap notes)

Codex's local Pi session has **already obtained SSH access** and performed
an explicitly approved, temporary native monitor-interface experiment.
Do not repeat the initial host-key verification process unless SSH now
indicates a *new* identity mismatch. Check current state; do not assume
the older bootstrap warning remains active.

The temporary native monitor interface was deleted and firmware mode restored,
but the Pi's `wlan0` could not return to monitor mode: brcmfmac reported
`Device or resource busy (-16)`. Live runtime was **degraded** at that
checkpoint. The Pi's Ethernet SSH transport was not interrupted.

Source commit `0f01d8066c5e625341cd698e536a0cd6b93ea817` patches the
*Nexmon build-stage source* to release its cfg80211 monitor virtual interface
on netdev teardown. Local unit suite reported 1,413 passed and 3 skipped,
with GitHub CI green. **The new driver has NOT been built or deployed to
the Pi**; the source change cannot repair the already loaded driver until
a separately approved rebuild/reflash.

A **separate operator approval** is required to stop `redux-live`, gracefully
reload the **already installed** brcmfmac module and restart/check the
service. If the operator grants it in the active local Codex session, verify
Ethernet SSH and snapshot bounded/sanitized before-state, refuse forced
unload, and do not perform a new monitor-interface experiment or driver
deployment without fresh approval. If recovery is blocked, continue
with independent safe source review/tests instead of idling.

On this workbench, the owner's current instruction is to maximize agent
autonomy within safe bounds; `AGENTS.md` now spells out the operating
contract. The chat-side model can read/write GitHub, but cannot directly
reach the owner's LAN. Codex CLI can execute remotely from Ubuntu WSL,
but no direct chat-to-Codex bridge exists. Do not claim otherwise.

**Latest recovery checkpoint:** One explicitly approved graceful reload of
the existing driver completed, but the runtime initially stayed degraded
because the global country had returned to `00` while the image was
configured for US. A separately approved restart of `redux-regdom.service`
restored US; the existing live supervisor's automatic retry then reached
`running` on `wlan0` with no current error or failure streak. The temporary
interface is absent. Firmware monitor remains 0 and packet link type
remains Ethernet 1, so this is service/regulatory recovery, not native RF
capture. Do not repeat a driver reload or interface experiment based on
the older checkpoint above. Neither driver deployment nor an ARM64 driver
build has been approved or performed. Details are in `docs/IMAGE_BUILD.md`.

**2026-10-15 reconnect:** The local checkout was clean and synchronized to
`c0cb265`; strict SSH at the prior address still matched the trusted key.
Read-only summaries again observed services running, global/configured US,
unblocked Wi-Fi and Bettercap HTTP 401. The selected interface remained
Ethernet link type 1 despite its monitor label; a separate read-only
Nexutil query returned firmware monitor 0. Capture inventories and the
ingestion ledger were empty, with a fresh worker heartbeat and no handoffs.
No driver, service, country, capture or other Pi configuration changes were
performed in this reconnect session. A fresh local image build is authorized
by the operator, but local sudo requires interactive authentication; no
qualified new artifact exists until that build and qualification finish.

**2026-10-15 local build update:** The operator started the isolated
`0403c39` build. Actual ARM64 modules linked for the Pi 4 and Pi 5
`6.12.109` kernels and passed the installer's vermagic gates. However,
the build log confirms a missing Debian archive keyring/signature warning.
The synchronized `240c51c` workbench adds mandatory signed-bootstrap gates;
the old build is preserved and marked **DO NOT FLASH**, regardless of its
older artifact helper's result. A fresh signed-build launcher is prepared;
host-package installation approval and terminal sudo authentication are
pending. Local tests at `240c51c`: 1,519 passed, 2 skipped. This supersedes
the earlier statement that no ARM64 module compilation had occurred.
No driver was deployed and no Pi state was changed.

**Latest signed-image checkpoint — 2026-10-15:** Approved WSL host
package installation and a fresh build have completed. Source `4f907ff`
is independently artifact-qualified, including signed bootstrap, exact
compressed-image checksum and actual embedded Pi 4/5 module verification.
See [FIELD_RUN_20261015.md](FIELD_RUN_20261015.md) for the exact SHA256,
Windows-accessible artifact directory and staged acceptance procedure.
The image includes measured live ARPHRD/Doctor/TFT status. It has not been
flashed or booted. Physical changes still need separate approval; retain
the existing comparison card and unsigned diagnostic build. This checkpoint
supersedes earlier pending host-authentication/build statements above.

**Post-flash resume (operator report):** The owner reports that the signed
`4f907ff` image was flashed and booted on the disposable Pi 4 8 GB and that
local login works. On resume, the checkout was clean at `3717cf5`.
Strict SSH detected a changed ED25519 host identity and refused login.
The local agent requested the fingerprint from the physical Pi console;
no known-host entry was replaced and no post-flash SSH hardware measurements
are claimed while that independent verification is pending. Keep the old
comparison card and Jayofelony card untouched. The new software-only
link-probe cleanup shares the bounded ARPHRD reader between reports and
runtime, refusing dot path components, linked type files and oversized input.
It is not installed on the Pi by this source edit.

**Verified post-flash diagnostics — 2026-10-16:** Console verification and
single-host known_hosts replacement are complete; strict Ethernet SSH works.
The Pi's baked `4f907ff`, Pi 4 `6.12.109` kernel, installed module digest,
loaded srcversion and firmware digest agree with the signed image. Services,
US, storage, Bettercap and worker scans pass, but native RF remains blocked:
only wlan0, ARPHRD 1, firmware monitor 0, no captures/handoffs. Doctor correctly
reports degraded RF health. No 480x320 framebuffer is enumerated. Exact
PASS/FAIL/UNKNOWN evidence and a separately gated next-test proposal:
[FIELD_DIAGNOSTICS_20261016.md](FIELD_DIAGNOSTICS_20261016.md).
No Pi mutations or new build occurred. This supersedes the pending SSH
verification statement above. Do not repeat prior recovery approvals.

**Native-VIF approval preflight — 2026-10-16:** The newly approved bounded
test stopped before any mutation: NetworkManager still explicitly claims
wlan0 managed=yes/state20, with manager and supplicant daemons active.
Exclusive ownership was not established. redux-live was not stopped and no
VIF was created; no cleanup was needed. Final read-only summary reconfirmed
healthy services/US/storage but the original RF limitation. A new specific
ownership-isolation decision is required before hardware experimentation.
See the final section of [FIELD_DIAGNOSTICS_20261016.md](FIELD_DIAGNOSTICS_20261016.md).
Source summaries now expose sanitized NM ownership observations, without
changing manager/radio state or claiming exclusive ownership.

**Ownership procedure ready for approval — 2026-10-16:** Independent D-Bus
checks show NM claims wlan0 but no active connection, and the supplicant
has zero bound interfaces. nmcli is 1.42.4. The prepared
`scripts/native-vif-isolation.py` passes its default read-only preflight;
its explicit execution flag has NOT been used. Exact operations, failure
checks, rollback and console fallback are documented in
[NATIVE_VIF_OWNERSHIP_PLAN.md](NATIVE_VIF_OWNERSHIP_PLAN.md). Current static
prerequisites pass; runtime isolation/reception/restoration remain approval
and hardware gates. No ownership/service/VIF changes or image build occurred.

**Historical initial outcome — 2b540ad attempt stopped, initially unrestored:** The
single newly approved attempt stopped redux-live and set only wlan0 runtime
managed=no. NM also changed primary wlan0 to managed mode/DOWN. The safety
gate stopped before VIF creation; no reception was sampled. Cleanup refused
to restore NM/live because the original radio invariant no longer held.
**Current Pi:** strict Ethernet SSH works, US/storage intact, only wlan0
ifindex3/type1/firmware0 exists, NM unmanaged/state10, redux-live INACTIVE;
other Redux/NM/supplicant services active and supplicant unbound. Do not
repeat the test or infer restoration. Exact evidence and a new-approval-only
primary mode/UP recovery proposal:
[NATIVE_VIF_TRIAL_20261016.md](NATIVE_VIF_TRIAL_20261016.md).
No further Pi changes have occurred. Earlier healthy-service/readiness
checkpoints above are historical, not the current state.

**LATEST — separately approved baseline recovery COMPLETE:** The owner
subsequently approved direct restoration of primary wlan0 monitor/UP, then
its original runtime NM flag and redux-live, each behind fresh safety gates.
All four approved commands succeeded once. The final independent summary
at 2026-10-16T17:05:37Z confirms original wlan0 ifindex3/monitor/UP/type1,
firmware0, NM managed=true/state20, unbound supplicant, Ethernet/US/storage,
all Redux services active and fresh running runtime with zero failures.
Doctor remains degraded for native RF; captures/handoffs remain empty.
No native VIF was ever created; do not repeat the experiment or infer RF
success. The prior stopped/unmanaged checkpoint is historical.
[NATIVE_VIF_TRIAL_20261016.md](NATIVE_VIF_TRIAL_20261016.md) records both
the failed isolation and the separately approved successful recovery.

## Historical bootstrap blocker: SSH host-key rotation

The Pi's currently offered ED25519 SSH host-key fingerprint was reported as
`SHA256:pKI2PuH3kxRjxJd1ys5MssupTWRV0hMnXxM5GduywJo`. WSL has an
**older** ECDSA identity for `192.168.12.115`, so OpenSSH correctly
refuses the connection.

**Do not automatically trust the new fingerprint.** The operator must
first confirm it using the physical Pi console or another independently
trusted channel:

    sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub

If and only if the Pi console fingerprint matches the value reported by
the SSH client, the local agent may replace the obsolete known_hosts entry
for that *single* IP and connect using
`ssh -i ~/.ssh/redux_field_ed25519 -o StrictHostKeyChecking=yes pi@192.168.12.115`.
Never pass `StrictHostKeyChecking=no` or `UserKnownHostsFile=/dev/null`,
never delete the entire `.ssh` directory, and do not invent a verified
fingerprint. If the private identity or hostname is still inaccessible,
explain the blocker to the operator.

## Local agent responsibilities and autonomy boundary

For a bounded, shareable collector, current source supports
`redux-report --summary`: allowlisted JSON with link type/channel, country,
runtime counters and classified errors, capture names/sizes, and the worker
heartbeat. It does not read engine logs or emit free-text ledger errors.
Only generated `bettercap-<16 hex>.pcap` names are printed; other capture
names are withheld because they may contain Wi-Fi identifiers. Inventory
enumeration is capped and explicitly marks truncation. Missing measurements
remain null/unobserved. This source feature is not installed on the current
Pi; it can be exercised read-only by streaming the collector through strict
SSH with `python3 -B` and `PYTHONPATH=/opt/redux`, without `--save` or copying
files onto the device. Legacy `--full` output remains private troubleshooting
material; do not treat arbitrary log text as safe for a public commit.

1. Work from Ubuntu WSL (not Windows PowerShell) and confirm the Git
   worktree and active branch before any edit. Never assume the terminal
   prompt is the Pi.
2. After host-key verification, run read-only SSH diagnostics autonomously
   and efficiently. Prefer batching bounded, sanitized output into one
   SSH call, rather than asking the operator to type many Pi commands.
3. First inspect `/captures/active`, `/captures/incoming` **filenames
   and sizes only**, `/captures/live/live.json` with sensitive fields
   redacted, the current rotation configuration, worker heartbeat/status,
   and relevant recent journal lines. Diagnose zero `handed_off` against
   actual files and runtime state before applying any fix.
4. Never upload raw PCAPs, handshake/hc22000 material, API credentials,
   Wi-Fi identifiers, private logs, token files, or SSH keys to GitHub,
   chat, or third-party services. The GitHub project is **public**; publish
   only small, intentionally sanitized pass/fail observations.
5. Edit software in the local workbench and run tests as needed; commit
   tested source and non-sensitive docs to `human/redux-workbench` with
   meaningful messages. Never directly change the installed Pi software
   just to make a test pass.
6. **Require explicit operator approval** before any Pi-changing action:
   remount, service restart/stop, radio or regulatory setting, package
   installation, software deployment, SSH configuration, reflashing,
   deleting/rotating captures, or destructive test. Do not run active RF
   operations or unauthorized network testing.
7. Keep all testing observational by default. If a separate disposable SD
   rebuild is needed, qualify the final compressed artifact first and have
   the owner decide when to flash it.
8. Report work in short milestones: what is verified, what remains unknown,
   exact changed repo files/commit, what (if anything) needs operator action.
   Distinguish local test results, simulated fixtures, CI, and physical
   measurements. Do not claim an action succeeded before its command did.

## Suggested initial local-agent prompt

> Read AGENTS.md, docs/WORKBENCH.md, docs/FIELD_ACCEPTANCE.md,
> docs/LOCAL_AGENT_HANDOFF.md and the latest workbench commits. Work on
> human/redux-workbench only. Take over the Redux Pi 4 field debugging
> using the WSL SSH key, starting read-only. **First pause only for the
> physical Pi host-key fingerprint verification described in the handoff**;
> don't disable SSH checking. Then autonomously gather bounded/sanitized
> Pi diagnostics in one or few SSH calls, trace the capture rotation/handoff
> and processing pipeline, implement and test source fixes locally, and
> push tested commits to the workbench branch. Never reflash, restart
> services, modify Pi state or run active RF tests without my approval.
> Keep progress concise and distinguish device evidence from simulation.

## Quick launch

The local agent needs the owner's explicit local installation and sign-in.
As of October 2026, Codex CLI is available for Linux/WSL. In Ubuntu WSL:

    cd ~/redux-workbench-src
    git status --short --branch
    git pull --ff-only
    command -v codex || echo 'Codex CLI not installed'

If not installed, use the current official Codex CLI installer documented at
https://github.com/openai/codex and sign in with the owner's ChatGPT account.
Launch `codex` **from the repository** and give it the suggested prompt above.
A locally running CLI agent is separate from this ChatGPT chat, but both
can read and update the same GitHub workbench commits.

Avoid GitHub Actions self-hosted runners on this public repository: they
would expose a host with LAN access and SSH credentials to untrusted
workflow code. If a future automated chat-to-LAN job queue is needed,
design it in a separate private, narrowly scoped control plane with
independent security review rather than exposing a general shell.

## Latest supervised f4f83c0 attempt — 2026-10-17

A new explicitly approved single attempt passed fresh preflight and the
measured NM managed/DOWN isolation. It stopped at the post-create temporary
interface gate **before activation or reception sampling**. Planned cleanup
fully verified restoration: original wlan0 monitor/UP/type1/firmware0,
NM yes/state20, fresh active Redux, strict Ethernet/US/storage preserved.
Independent summary at 2026-10-17T13:03:35Z passed service/transport baseline;
native reception remains unproven. The combined temporary gate did not log
its individual measurements, so its precise failed predicate is UNKNOWN.
No retries or extra recovery. This approval is consumed; see
[NATIVE_VIF_TRIAL_20261017.md](NATIVE_VIF_TRIAL_20261017.md).
