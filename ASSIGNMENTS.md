# ASSIGNMENTS — lanes (enforced by CI)

Three agents build this repo in parallel. **Lanes are enforced** by
`.github/workflows/lane-guard.yml`: a PR that edits a file outside its branch's lane **fails CI**
and cannot be merged green. This is not a polite request — it is a gate. One topic per branch;
open a PR; the owner merges.

_Gemini has been removed from this project. Do not re-add a `gemini/` lane or any Gemini CI agent._

## Lanes (exclusive — the guard enforces these exact paths)

| Agent | Branch | May edit ONLY | Everything else |
|---|---|---|---|
| **Codex** (GPT) | `codex/<topic>` | `image/**`, `pi-gen/**`, `boot/**`, `build.sh`, `docs/IMAGE_BUILD.md`, `redux/core/boot.py`, `tests/test_{image_build,nexmon_build,boot}.py` | blocked by CI |
| **Grok** (xAI) | `grok/<topic>` | `redux/detect/**`, `tests/test_detect_*.py` | blocked by CI |
| **Claude** (lead) | `claude/<topic>` | everything (owns `redux/radio`, `redux/engine`, `redux/core` except `boot.py`, `redux/classify`, `etc/`, `scripts/`, all docs, all shared files, `.github/`) | — |

**Shared / structural files are LEAD-ONLY** and the guard blocks every non-lead branch from them:
`.github/**`, `README.md`, `AGENTS.md`, `ASSIGNMENTS.md`, `TASKS.md`, `docs/*` (except Codex's
`docs/IMAGE_BUILD.md`), every `__init__.py`, `pyproject.toml`, `.github/workflows/ci.yml`. If your
task seems to need a shared-file change, it doesn't — hand that to the lead (`claude/*`), who
consolidates docs/exports/CI. This is the rule that keeps overlaps, clobbered merges, and stray
workflows from ever reaching `main`.

## Current assignments

### Codex → `codex/*` — the image/OS build
Lean **arm64** pi-gen image for **Pi 4 / Pi 5**, bettercap + nexmon baked, `redux.service` →
`python -m redux.core.boot`. Read-only overlay rootfs (yank-safe), sub-15s boot, hardware watchdog.
Acceptance: `./build.sh` produces an arm64 image; boot/overlay/watchdog documented; on-Pi flash is
the labeled hardware gate. **Do not** touch README/TASKS/AGENTS — the lead folds any needed
doc/task updates; put image docs in `docs/IMAGE_BUILD.md` only.

### Grok → `grok/*` — the defensive detector pack (`redux/detect/`)
deauth-flood, rogue-AP/evil-twin, beacon-spam, surveillance-sweep detectors + the `DetectEngine`
fan-in, emitting glass-box alerts. Standalone `Frame` input — do **not** import `redux.engine`
(the lead wires it to the real `Event` at integration). Pure defense: raises alerts, transmits
nothing. Acceptance: hardware-free tests under `tests/test_detect_*.py`. **Stay entirely in
`redux/detect/`** — nothing else, ever.

### Claude (lead) → `claude/*` — core + integration + review
Owns `redux/radio` (orchestrator/manager/probe/hotplug), `redux/engine` (bettercap driver),
`redux/core` (supervisor), `redux/classify`, `etc/` + `scripts/` (udev/hotplug), all docs, all
shared files, `.github/`. Sets tasks + acceptance, reviews every PR against the guard + its
acceptance, reconciles exports/docs, keeps the safety gates. **Merges are the owner's.**

## Scope (non-negotiable, all lanes)
Authorized/passive by default; any firing-capable capability gates on an empty-by-default
BSSID/SSID allowlist; **no turnkey-attack modules**; Pi 4/Pi 5 arm64 only; glass-box; real data only.

## If the guard fails your PR
It printed exactly which file is out of lane and which paths your lane allows. Remove the stray file
from your branch (it belongs to another lane), or ask the lead to make the shared-file change. Do
not widen your lane to make it pass.
