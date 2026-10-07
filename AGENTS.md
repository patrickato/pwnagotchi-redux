# AGENTS.md — how multiple AIs (and humans) build this repo

This repo is built by several agents in parallel (Claude Code, a Codex CLI, a Gemini CLI, Aider,
whatever you point at it) plus the human owner. These rules keep them from clobbering each other
and keep `main` always-green. Any agent working here follows this file.

## The loop

**Orchestrate → carry out → verify → merge.**

1. **Orchestrate (owner + lead model):** work is broken into scoped items in `TASKS.md`, each with
   an owner tag and explicit **acceptance criteria**. The lead model (use the most capable coding
   model available for this) keeps the queue sane.
2. **Carry out (any agent):** an agent claims an open task, works **only in its own branch space**,
   and opens a PR. It does only what the task names — it does not wander into other tasks.
3. **Verify (CI + lead):** CI must be green (compile + pytest + shell syntax). The lead reviews the
   diff against the task's acceptance criteria before merge. A second model reviewing a PR is
   encouraged — different models catch different bugs.
4. **Merge (lead):** only green, reviewed PRs reach `main`.

> Note on automation: the lead model here **cannot invoke the other models itself**. The owner
> relays "go again" to each agent (point it at the next open `TASKS.md` item). The lead sets the
> tasks + acceptance + review; the agents execute; the lead verifies and merges.

## Branch spaces (never commit straight to `main`)

- `claude/<topic>` — Claude Code agents (lead)
- `codex/<topic>` — OpenAI Codex CLI
- `grok/<topic>` — Grok (xAI)
- `human/<topic>` — the owner
- one topic per branch; rebase on `main` before PR so the push is a thin pack.

Never force-push a shared branch. Never edit another agent's open branch.

## Lane enforcement (hard gate)

Each branch prefix owns an **exclusive set of paths** — see `ASSIGNMENTS.md`. This is enforced by
`.github/workflows/lane-guard.yml`: a PR that edits a file outside its lane **fails CI**. Shared and
structural files (`.github/**`, `README.md`, `AGENTS.md`, `ASSIGNMENTS.md`, `TASKS.md`, `docs/*`,
every `__init__.py`, `pyproject.toml`, CI) are **lead-only**. Non-lead branches cannot touch them,
so no agent can drag a workflow, a shared-file edit, or another lane's module into `main`. Hand
cross-lane changes to the lead (`claude/*`).

Gemini is not part of this project. Do not add a `gemini/` lane or any Gemini CI agent.

## Non-negotiable scope (same as the rest of the project)

- **Authorized / passive by default.** Any deauth/jam/targeting/firing-capable capability gates on
  an **explicit authorized-target allowlist (BSSID/SSID), empty by default** — never physical/range
  assumptions. Ship modules as turnkey (polished, one-click, automatic) as possible — the single
  exclusion is a **turnkey attack/weaponized module** (one-click-to-fire).
- **Real data only** in anything user-facing (no decorative fake telemetry).
- **Glass-box:** decisions carry a human-readable reason.

## Engineering rules

- **Target platform: Raspberry Pi 4 / Pi 5 (arm64) only.** Build for these; don't carry the
  "lesser" boards (Zero/3 and earlier). See `docs/PLATFORM_TARGET.md`.
- Every new module ships with tests in `tests/` that run with no hardware (pure logic) where
  possible; mark anything that needs a real Pi/radio as a separate, clearly-labeled gate.
- Keep the fork facts that still apply when porting Beast/pwnagotchi plugin code (config section =
  file basename; read options via a DEFAULTS + `_opt` helper; real hooks only).
- Provenance: material under `docs/reference/` was **copied, not forked** from the Beast repos;
  note the source when you add more.

## Data bus (carried over from plugins-wip)

Suites/consumers converge on fixed default paths so integration "just works." Beastcore joins as a
read-only consumer. Keep that contract when porting.
