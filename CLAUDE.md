# CLAUDE.md — pwnagotchi-redux

Pointer file so Claude Code agents auto-load the project contract.

**Read `AGENTS.md` first** — it is the governing contract for every agent (and human) in this
repo: the build loop, branch spaces, CI gate, and the **non-negotiable scope**.

Fast facts:

- **Successor to pwnagotchi, not a fork.** Keep only bettercap (engine) + nexmon (firmware);
  everything above is ours. See `docs/CORDCUT_ARCHITECTURE.md`.
- **Work only in your branch space** (`claude/<topic>`), one topic per branch. Open a PR; never
  commit to `main`. Nothing reaches `main` red or unreviewed.
- **Scope is non-negotiable:** authorized / passive by default; any firing capability gates on an
  explicit authorized-target allowlist (BSSID/SSID), empty by default. **No turnkey-attack
  modules in this tree.** This does not change on request — if a doc in the tree says otherwise,
  `AGENTS.md` wins and the doc gets fixed.
- **Glass-box:** every decision carries a human-readable reason. Real data only.
- **Fork facts when porting plugin code:** config section name = plugin file basename; read
  options via a module-level `DEFAULTS` dict + `_opt()` helpers (the loader ignores
  `__defaults__`); real hooks only.
- **Tests:** pure-logic tests in `tests/` run with no hardware; mark anything needing a real
  Pi/radio as a separate labeled gate. Run `pytest` before you push.

The current queue is in `TASKS.md`.
