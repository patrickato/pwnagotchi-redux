# Redux Autopilot — remove manual code/test/terminal relay

The owner wants local Codex and GitHub to carry routine source engineering
without the owner relaying commands between ChatGPT and Ubuntu WSL.

## What is implemented

`scripts/redux-autopilot.py` executes **one** source-only Codex engineering
cycle, using an ephemeral clone of **`human/redux-workbench`**. It reads the
read-only/source-only work specification in `docs/AUTOPILOT_AGENT.md` and
invokes the already-installed local Codex CLI with the **workspace-write**
sandbox and noninteractive approvals. It does not reuse, alter, or clean the
owner's `~/redux-workbench-src` checkout.

Only after a successful Codex exit, unchanged Git HEAD, allowlisted file
changes, and independent passing full Python tests, image-contract checks,
compilation and whitespace checks does the host wrapper commit and attempt a
**non-forced, fast-forward-only** push to the workbench. A race with another
agent's push rejects safely. Existing GitHub CI independently runs on pushes.
No main-branch PR or merge is created.

The agent may improve source and tests. **It has no approval to contact the
Pi**, SSH, read capture files, change Wi-Fi state, stop services, flash an SD
card, run the artifact-integrity audit on the Pi, or perform RF experiments.
The CLI wrapper never does those things and does not authorize Codex to.
No credentials, remote logs, or raw captures are published as artifacts.

The noninteractive agent is powerful: Codex itself is an AI running in the
owner's WSL context, and agent instructions alone are not a security
boundary. The wrapper limits publishable paths and performs independent
tests but does not prove the generated code bug-free or eliminate the risks
of a compromised local environment. Keep Codex sandbox restrictions active,
avoid granting full-host access, and review GitHub history/CI periodically.
This is an **opt-in** autonomy mechanism, not a guarantee of secure code.

## Check the setup without running an agent

From Ubuntu WSL:

```sh
cd ~/redux-workbench-src
python3 scripts/redux-autopilot.py plan
bash scripts/install-redux-autopilot.sh --plan
python3 -m pytest -q tests/test_redux_autopilot.py
```

No network or Pi connection is attempted by plan/tests.

## Enable once (explicit owner action)

First ensure the Ubuntu distro has the Codex CLI installed and signed in,
Git HTTPS push access, Python test dependencies, and working `systemctl --user`.
The intended checkout is exactly `~/redux-workbench-src`. In Ubuntu WSL:

```sh
cd ~/redux-workbench-src
git pull --ff-only
bash scripts/install-redux-autopilot.sh --install
```

This opt-in creates two files under `~/.config/systemd/user`, enables the
**user** timer, and runs source-only Codex cycles near 01:00, 07:00, 13:00 and
19:00 local time with up to five minutes of jitter. It requests **no sudo**,
does not install an OS package, does not touch the Pi, and does not start a
Codex run directly during installation. A catch-up timer firing is possible
after WSL restarts. The local systemd user manager and WSL distro must be
running; this does **not** wake a stopped Windows laptop or WSL instance.
If Codex login or test tools are missing, a cycle fails closed.

A one-off source cycle can also be invoked explicitly:

```sh
python3 scripts/redux-autopilot.py cycle --enable-agent
```

Progress is represented by local sanitized machine-readable status:

```sh
bash scripts/install-redux-autopilot.sh --status
cat ~/.local/state/redux-autopilot/last.json
```

Every status reports a high-level result: `published`, `idle`, or
`blocked`, with a fixed reason code. It does not contain private paths,
captures, wireless identities or raw Codex output. Failed ephemeral clones
are removed: there is no automatic deployment or recovery from test failure.

To turn off unattended source work:

```sh
bash scripts/install-redux-autopilot.sh --disable
```

The timer installer refuses to overwrite existing unit files. An installation
or GitHub credential problem should be resolved from the user's terminal;
do not suppress authorization prompts or broaden the sandbox.

## Coordination with the chat-side development lead

ChatGPT can independently develop and commit **source** changes to the shared
workbench through the connected GitHub application. Scheduled local Codex
cycles fetch the current head at the beginning of each run and push only if
the branch has not advanced. An accepted commit is immediately visible to
the other agent through GitHub. This removes the **manual terminal relay for
source engineering**, but not the approval for deploying onto an actual Pi.

The user can send a direction to this chat and the chat-side lead can update
the GitHub workbench and task documentation. The next local Codex source
cycle can read those GitHub changes on its own, provided the timer is enabled
and the workstation is available. There is no direct ChatGPT-to-WSL control
connection and no claim that the chat itself remotely started Codex.

## Hardware automation is a separate gated phase

The October 20 Pi 4 read-only acceptance session succeeded with
`scripts/redux-acceptance.py`; it observed five capture ledger records
(one ready, four invalid), zero one-second RX/TX deltas, onboard ARPHRD 1,
USB ARPHRD 803, and missing 480×320/ILI9486/touch candidates. The original
one-time hardware collection approval is **not** an unlimited license to
repeat it or run artifact hashing. Do **not** schedule or add Pi operations
to the above source timer.

Later, a separately reviewed opt-in **fixed-scope read-only field job**
could audit prepared artifact integrity, refresh passive inventory, and
publish private redacted aggregates from the verified Pi. It must pin the
host key and IPv4 identity, bound rate/deadline/data volume, avoid remote
writes and credentials in GitHub, and require its own operator approval.
State-changing hardware experiments remain separate approved operations.
