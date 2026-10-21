# Redux hardware automation — real Pi, minimal operator relay

Status: **Source-only implementation and offline tests added; NOT installed
or authorized on the physical Pi.** This document does not grant SSH, sudo,
service, driver, wireless, firmware, imaging or capture-content privileges.

## Yes, an AI can reach real hardware

The owner's existing Ubuntu WSL machine can already authenticate to the
Pi 4 over strict known-host SSH using a private field identity. A locally
running Codex (or another agent with terminal access) can run real tests
because it has a local execution environment and network routing. This
ChatGPT conversation does **not** itself gain LAN or private-key access.
A separate local worker can execute bounded hardware jobs and pass back
verified, redacted result summaries through an approved coordination channel.

The best architecture is deliberately **not** unrestricted AI SSH:

    ChatGPT (specifies reviewed test)       Codex (develops/tests source)
                   \                         /
                    GitHub workbench & CI
                              |
                  approved, fixed test profile
                              |
                 local policy-gated controller
                    (owned by trusted user)
                              |
                strict SSH + known host identity
                              |
                   disposable Redux Pi 4
                              |
             host-private bounded evidence store
                              |
           redacted status -> human + AI coordinator

There are two actors: the AI proposes which reviewed test to run;
the controller independently checks a local allowlisted policy and performs
only the operations enabled by that policy. The AI should not be able to
request arbitrary shell commands, alter the target, use sudo or read a private
credential through an untrusted task prompt.

## Stage 1: repeatable passive measurements

Run offline:

    python3 scripts/redux-field-bridge.py plan

The plan describes the already-reviewed collector. The underlying
scripts/redux-acceptance.py session-collect uses one strict SSH session,
a 45-second collector deadline, no sudo, no packet sockets, no interface
or service changes, and no Pi files. It samples existing radio, mount,
display, runtime and processing metadata. Results stay unsigned.

The new bridge separates two decisions:

1. **Enrollment (explicit, finite approval):** pin one private IPv4 target,
   one existing owner-only SSH key path, the exact clean Git revision,
   SHA-256 of the launcher and memory bundle, rate, expiry and session limit. The approval stays on WSL only at
   ~/.local/state/redux-field-bridge/recurring-approval.json, file mode 0600
   inside a 0700 directory.
2. **Scheduled execution:** only an enabled, unexpired approval permits one
   read-only collection. Minimum interval: 1 hour. Maximum approval: 14
   days and 40 total attempts. The controller consumes each attempt before
   SSH, uses a one-instance lock and saves any full report privately. A
   changed collector-source fingerprint blocks further SSH. The output
   status never contains a private IP, SSH identity, raw capture, or SSID.

The source includes scripts/redux-field-bridge.py,
tests/test_redux_field_bridge.py, and
scripts/install-redux-field-bridge.sh. The independent user-systemd timer
fires hourly near minute 20 local time while WSL is running, and
its rate policy can further restrict it. The existing source-only Codex
autopilot timer is separate. Nothing has been enabled on WSL or the Pi by
merely committing this software.

### Independent review before activation

On WSL, after pulling the trusted source, run offline:

    python3 -m pytest -q tests/test_redux_field_bridge.py
    bash scripts/install-redux-field-bridge.sh --plan

Only after confirming the current physical Pi/card and host identity may
the operator approve recurrent read-only observations. Command **syntax**
(not existing consent):

    python3 scripts/redux-field-bridge.py enroll \
      --host VERIFIED_PRIVATE_PI_IPV4 \
      --key ~/.ssh/redux_field_ed25519 \
      --interval-hours 1 --days 14 --max-sessions 40 \
      --approve-recurring-read-only

Then separately install the schedule:

    bash scripts/install-redux-field-bridge.sh --install

Enrollment alone does not connect to the Pi. It creates only private host
approval data. The installer modifies only the user-owned WSL timer. Revoke:

    bash scripts/install-redux-field-bridge.sh --disable

### Operator-approved October 21 cadence

The operator explicitly approved recurring read-only measurements and asked
for a shorter interval than six hours. The controller and separate local timer
now support **one collection per hour**. Keep the original maximum of **40
total attempted sessions** and the **14-day expiration**, whichever comes
first. Under continuous one-hour scheduling this reaches the attempt cap
after roughly 40 hours. Failed SSH sessions consume an attempt; no automatic
retry or scope extension is allowed.

Faster 15–30-minute monitoring is technically possible after a separately
reviewed rate adjustment, but it will not resolve 802.11 decode or touchscreen
acceptance on its own. Keep the source-only Codex runner's schedule separate
from the hardware observer.

After expiry, source revision/bundle change, or session exhaustion, the
controller stops rather than auto-renewing permission. The source-only Codex
worker uses a separate disposable clone and does not alter the approved WSL
checkout, so unrelated GitHub commits cannot silently change the approved
field collector. To renew after expiry or explicit revocation, repeat the
reviewed enrollment options with the 'renew' subcommand in place of 'enroll';
the explicit approval flag is still mandatory. Renewing an active approval
is refused. A new, reviewed scope is required.
Do not report real automation success until a session actually runs.

**Important:** This initial controller is not yet a hardened OS-level trust
boundary: an AI process running as the same WSL user may have independent
access to the user's existing SSH key. Before AI-directed state-changing
hardware operations, move credentials to a separate unprivileged controller
identity and enforce a server-side forced-command/allowlist. A model prompt
or local policy alone is not a complete security boundary.

## Stage 2: separately authorized observation profiles

Recurring metadata does NOT authorize these operations:

- Prepared artifact integrity: one bounded, read-only SQLite/SHA-256
  examination of the historical ready ledger row and prepared bytes, with
  no raw PCAP export. The existing capture-artifact-audit.py --plan is
  source-only until separately approved for the live Pi.
- Physical TFT: metadata inventory followed by observed pixels/touch
  evidence. No automatic overlay or calibration change.
- Driver/NM forensic evidence: identity-pinned, bounded, read-only
  evidence with fail-closed UNKNOWN handling.

## Stage 3: gated hardware experiments

The user can approve one **complete** reviewed experiment, including
preflight, preparation, bounded action, measurements, restoration and
verification. That approval covers all specifically named internal steps:
no prompt per command is necessary. Failed identity, ownership, control
or rollback gates stop without retries or expanded recovery.

Examples include a dedicated test AP's passive RF reception, separately
isolated USB radio, controlled reboot endurance, or a replacement image on
a physically verified disposable SD card. Flash, firmware write, Wi-Fi
configuration, service/driver changes and active transmissions must never
be silently included in read-only recurring approval.

## Remote reachability and host choice

The existing local LAN works with WSL -> Pi SSH. There is no reason to
open SSH to the internet. For future offsite testing, Tailscale or a
similarly restricted private VPN can provide reachability and ACLs.
For reliable 24/7 scheduling, an always-on dedicated Linux mini PC or
second Pi is preferable to WSL on a sleeping Windows laptop.

Do not put Pi credentials on a general GitHub self-hosted runner exposed
to untrusted public PR/workflow code. Such workflows can compromise the
runner environment. Prefer a locally initiated outbound evidence
controller with narrow privileges.

## Current Pi 4 acceptance priorities

The Oct 20 unsigned session measured onboard Wi-Fi monitor-labeled ARPHRD
1 (not native reception evidence), USB ARPHRD 803 without verified
decoding or owner, a processing ledger of 1 ready and 4 invalid, and
missing 480x320 ILI9486/touch candidates. Future hardware automation
must close these gates with actual observations rather than synthetic
software test counts.

## Stable collector while source development advances

The approved hourly observer is pinned to the exact WSL checkout revision
recorded when the owner enrolled. This is desirable: updating a collector's
code must **not** automatically expand recurring physical authorization.
However, frequent source pulls into that same checkout would cause subsequent
observations to stop with `source_changed`.

The new **source-only** migration utility
`scripts/freeze-redux-field-bridge.py` reconstructs the exact *previously
approved* Git revision as a separate, locally private detached worktree and
verifies its original collector launcher/bundle SHA-256 and private approval
record. It then changes only the user-level systemd field service's
`ExecStart` to this detached snapshot. It does **not** reset, extend, or
rewrite the approval; change the timer; open SSH; restart the Pi; or grant
Codex credentials. If a read-only observation is currently active it refuses
to migrate. Failed systemd reload restores the existing service path.

The source-only plan is:

```sh
python3 scripts/freeze-redux-field-bridge.py --plan
```

After a successful **full** CI run and operator review, the one-time WSL
sequence is (with no Pi command):

```sh
cd ~/redux-workbench-src &&
git pull --ff-only &&
python3 -m pytest -q tests/test_freeze_field_bridge.py &&
python3 scripts/freeze-redux-field-bridge.py --migrate
```

The first `git pull` can briefly cause a scheduled collection to return
`source_changed`; it should not consume a session attempt. The migrator
pins and recovers the original approved revision from the existing
owner-private approval, rather than treating the newly pulled HEAD as
authorization. If the old commit is unavailable or any hashes disagree,
migration halts **without** approving a replacement. A successful migration
preserves the observer's consumed count, hourly schedule and existing
expiry. Failed/missing local systemd or dirty/altered frozen sources are
fail-closed blockers, never permission to proceed against the Pi.

Once migrated, normal `git pull --ff-only` is allowed in the mutable
source checkout without changing the frozen observer. Codex's ephemeral
source clones continue independently. A future collector upgrade or
approval renewal requires a separately reviewed migration/rotation;
**do not silently reset the 40-attempt cap or auto-renew consent.**

This migration has no power to repair the network interface, display,
capture integrity or acceptance gaps. Its sole role is reliable, auditable
unattended read-only metadata collection while software changes rapidly.
