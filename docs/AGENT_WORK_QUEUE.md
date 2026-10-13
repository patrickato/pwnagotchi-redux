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

## P1 — engineer a less manual field workflow

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

## P2 — independent verification and release preparation

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
