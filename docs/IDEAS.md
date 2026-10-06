# IDEAS — the backlog of things worth building (nothing here is approved to build)

This is a thinking space. An idea moves out of here only when it becomes a scoped row in
`TASKS.md` with acceptance criteria and the owner's go. All ideas inherit the repo scope
(`AGENTS.md`): authorized/passive by default, glass-box, real data only, no turnkey-attack
modules.

---

## I1 — The creature narrates its own radio decisions (glass-box, made visible)

**The pitch.** The Radio Orchestrator already produces a human-readable `reason` on every role
assignment and every hotplug promote/fallback (see `redux/radio/orchestrator.py` → `Assignment.reasons`
and `.warnings`). Right now those strings are structured data. Idea: pipe them straight into the
creature's voice so the device *tells you what it just did and why*, on the TFT and in the log.

- Plug the Alfa mid-hunt → the creature says *"nice — mt76 does 5 GHz and injects, promoting it to
  capture, onboard goes to uplink."*
- Alfa browns out on a shared hub → *"your Alfa keeps dropping on USB 2.0 — give it its own USB 3
  port."* (This is the `high_draw` + `usb_gen` warning path, surfaced as personality instead of a
  buried log line.)
- No injector present in hunt intent → *"only the onboard radio here, so I'm staying passive —
  recon only."*

**Why it matters / why it's different.** Every other device in this space is a black box with a
cute face. This is the opposite: the face is the explanation. The "mystique" of pwnagotchi was
never real (the RL brain got removed and nobody noticed). redux's bet is the honest inverse —
*legibility as the personality*. The reasons are already computed and tested; this is a thin
presentation layer over data that exists, not new decision logic. Cheap to build, unique to own.

**Scope.** Pure presentation of already-generated reason strings. No new capability, nothing
offensive. Lands as a Supervisor + creature-screen task once the driver (1.4) and supervisor loop
(1.5) exist — depends on them, not on new radio logic.

---

## I2 — Measured per-adapter injection self-test + opt-in community capability DB

**The problem.** Whether a given USB adapter can actually do monitor + injection on a given kernel
is *driver- and firmware-specific* and changes with every kernel/nexmon bump. Today `probe.py`
infers `inject` from a driver allowlist heuristic (`_INJECT_DRIVERS`) — a guess. Guesses are how
you end up on a hunt with a radio that silently can't inject.

**The idea, in two honest halves:**

1. **Measured self-test (local, authorized only).** A one-shot, opt-in `redux radio selftest` that
   runs *against the user's own hardware* — put each adapter in monitor, confirm it reports the
   bands `iw phy` claims, and verify monitor mode actually takes. The injection check is the
   sensitive part: it only ever fires at **the user's own authorized test AP from the allowlist**
   (the same empty-by-default gate the rest of the project uses) — never into the air at large,
   never a scan-and-spray. Result is a measured capability record that replaces the heuristic guess
   for *that* adapter on *that* kernel. If no authorized AP is configured, injection stays "unknown
   (heuristic)" and the orchestrator degrades gracefully, exactly as it does now.

2. **Opt-in community capability DB.** Let a user *choose* to contribute an anonymized record —
   `{chipset, driver, kernel, nexmon rev, bands-confirmed, monitor:ok, inject:confirmed-in-own-lab}`
   — so the next person with the same adapter gets a real answer instead of a guess. Strictly
   opt-in, no telemetry by default (repo rule: real data only, nothing phones home uninvited). The
   DB is just "this hardware+kernel combo is known to work," the single most-asked question in this
   hobby and one nobody has answered well.

**Why it matters.** It turns the weakest part of the probe (an inject *guess*) into either a
measured fact or an honest "unknown," and it builds a moat: a crowd-sourced, trustworthy
"what actually works" table is the kind of thing a community rallies around and remembers.

**Scope / guardrails.** The injection test is a firing-capable action, so it is gated exactly like
any other: empty-by-default authorized-target allowlist, user's own lab hardware only, off unless
explicitly configured. The DB is opt-in and anonymized. Neither half ships until it's a `TASKS.md`
row with the owner's explicit go — and the injection-test half only with sign-off per the scope rule.
