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
