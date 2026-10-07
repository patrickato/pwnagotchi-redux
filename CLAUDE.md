# CLAUDE.md — pwnagotchi-redux

Pointer file so Claude Code agents auto-load the project contract.

**Read `AGENTS.md` first** — it is the governing contract for every agent (and human) in this
repo: the build loop, branch spaces, CI gate, and the **non-negotiable scope**.

Fast facts:

- **Successor to pwnagotchi, not a fork.** Keep only bettercap (engine) + nexmon (firmware);
  everything above is ours. See `docs/CORDCUT_ARCHITECTURE.md`.
- **Work only in your branch space** (`claude/<topic>`), one topic per branch. Open a PR; never
  commit to `main`. Nothing reaches `main` red or unreviewed.
- **Scope:** authorized / passive by default; any firing-capable capability gates on an explicit
  authorized-target allowlist (BSSID/SSID), empty by default. **Ship turnkey (one-click,
  automatic) wherever possible** — the single exclusion is a **turnkey attack/weaponized module**
  (one-click-to-fire). `AGENTS.md` is the governing contract.
- **Glass-box:** every decision carries a human-readable reason. Real data only.
- **Fork facts when porting plugin code:** config section name = plugin file basename; read
  options via a module-level `DEFAULTS` dict + `_opt()` helpers (the loader ignores
  `__defaults__`); real hooks only.
- **Tests:** pure-logic tests in `tests/` run with no hardware; mark anything needing a real
  Pi/radio as a separate labeled gate. Run `pytest` before you push.

The current queue is in `TASKS.md`.
