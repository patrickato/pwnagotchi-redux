# CLAUDE.md — pwnagotchi-redux

Pointer file so Claude Code agents auto-load the project contract.

**Read `AGENTS.md` first** — it is the governing contract for every agent (and human) in this
repo: the build loop, branch spaces, CI gate, and the **non-negotiable scope**.

Fast facts:

- **Successor to pwnagotchi, not a fork.** Keep only bettercap (engine) + nexmon (firmware);
  everything above is ours. See `docs/CORDCUT_ARCHITECTURE.md`.
- **Work only in your branch space** (`claude/<topic>`), one topic per branch. Open a PR; never
  commit to `main`. Nothing reaches `main` red or unreviewed.
- **Scope, not a cage.** redux ships the full offensive toolkit at full power (capture→crack,
  PMKID/assoc, deauth, the network-layer kill-chain, evil-portal). Firing-capable functions
  operate within the **central Scope** (`redux/core/scope.py`) — the one list of targets you own
  or are authorized to assess: your own networks and devices, your whole lab, gear you bought to
  test, engagements you're contracted for, ranges, CTFs, and consenting peers. It starts empty
  only so the device never fires at something you didn't choose; arming is one gesture (`redux
  scope add` / `import` / `arm-lab`, with per-job groups and optional expiry). **Scope decides
  WHERE it's aimed, never WHAT it can do — inside scope, nothing is held back, and ship it as
  turnkey/one-click as you can.** The only things that stay detection-only are the handful that
  can't be aimed and hit bystanders: indiscriminate BLE/beacon spam and RF jamming (also illegal
  to transmit). `AGENTS.md` is the governing contract.
- **Glass-box:** every decision carries a human-readable reason. Real data only.
- **Fork facts when porting plugin code:** config section name = plugin file basename; read
  options via a module-level `DEFAULTS` dict + `_opt()` helpers (the loader ignores
  `__defaults__`); real hooks only.
- **Tests:** pure-logic tests in `tests/` run with no hardware; mark anything needing a real
  Pi/radio as a separate labeled gate. Run `pytest` before you push.

The current queue is in `TASKS.md`.
