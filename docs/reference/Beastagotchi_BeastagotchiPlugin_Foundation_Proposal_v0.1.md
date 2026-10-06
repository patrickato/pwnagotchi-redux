# BeastagotchiPlugin Foundation — Proposal v0.1

**Status:** idea / notes only — nothing here is built yet. This document is a
complete write-up for future decision-making, captured while auditing the
third-party pwnagotchi plugin ecosystem in `patrickato/test-plugins` (see
that repo's `pwnagotchi-plugins/MASTER_PLUGIN_LIST.md` and
`plugin-upgrade-proposals/` for the audit this grew out of). It does not
modify or replace anything else in this repo — it is a new, standalone spot
to park the idea until it's picked up.

## Where this idea came from

Auditing ~175 third-party pwnagotchi plugins turned up the same handful of
bugs, over and over, across unrelated authors:

- Code written against a `BasePlugin` class that **does not exist** in this
  fork (`jayofelony/pwnagotchi`) — only `pwnagotchi.plugins.Plugin` exists,
  registered via `__init_subclass__` the moment the class is *defined*, not
  instantiated. Ten plugins in one cluster alone (all from the same author)
  raised `ImportError` on load because of this, and it had already shown up
  earlier in the audit too (`wd_honey_Pot.py`, `bluetooth_scanner.py`).
- Plugins assuming `on_handshake`/`on_association`/etc. always hand them a
  full AP dict, when in practice this fork sometimes hands a bare MAC
  string instead — code that does `access_point.bssid` (attribute access)
  or `ap["essid"]` (wrong key — this fork uses `hostname`, not `essid`)
  crashes or silently never matches.
- Plugins hardcoding `wlan0`/`wlan0mon` instead of reading the real
  interface from `pwnagotchi.config['main']['iface']`.
- Plugins shelling out to a separate binary (`aireplay-ng`, `iwlist`) for
  something this fork's own `pwnagotchi/agent.py` already does natively,
  in-process, through the already-open bettercap session
  (`agent.run('wifi.deauth <mac>')`, `agent.run('wifi.assoc <mac>')`) —
  cheaper, no new dependency, no two processes fighting over one monitor
  interface.
- Attack-capable plugins (deauth, jamming) with **no targeting scope at
  all** — they fire at whatever AP they're handed, unconditionally. The one
  plugin from this audit judged worth rebuilding for lab use
  (`wifi_jammer.py` → `WifiJammerNG`, now in `patrickato/plugins-wip`) was
  rebuilt specifically around an explicit, empty-by-default
  `authorized_networks` allowlist gating every firing path — the agreed
  design rule going forward for anything in this family: **the safety
  mechanism is an authorized-target allowlist, never an assumption about
  RF range or property lines.**

The idea: instead of re-discovering and re-fixing these same bugs plugin by
plugin, bake the fixes into one shared base class that lives in *this*
repo, so every future Beastagotchi-authored pwnagotchi plugin starts from a
foundation that already gets them right.

## The `BeastagotchiPlugin` base class (concept)

A `pwnagotchi.plugins.Plugin` subclass, living somewhere like
`pwnagotchi_plugin/beastagotchi_plugin.py` (proposed path — not created),
that every future custom plugin subclasses instead of `Plugin` directly.
Proposed components:

1. **AP/handshake argument normalizer.** A helper (`_as_ap_dict(ap)` in the
   sketch below) that accepts either a full AP dict or a bare MAC string
   and always returns a dict with at least `mac` present, so subclasses
   never have to special-case the bare-string shape themselves:

   ```python
   def _as_ap_dict(ap):
       if isinstance(ap, dict):
           return ap
       return {"mac": str(ap), "hostname": ""}
   ```

2. **`.pcap`/`.pcapng`-safe filename handling.** A small helper for
   plugins that touch handshake files, since some tooling in the wider
   plugin ecosystem assumes one extension or the other and breaks on the
   other.

3. **A reusable authorized-target allowlist helper**, generalizing the
   pattern built for `WifiJammerNG`: given `authorized_networks` (a list of
   BSSIDs and/or SSIDs, empty by default) and an AP dict, `self.is_authorized(ap)`
   returns whether that AP's MAC or hostname matches an entry — BSSID
   compared case-insensitively against the regex `^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$`
   form, SSID compared case-insensitively against `hostname`. Any future
   plugin capable of firing something at an AP (deauth, assoc, a targeted
   scan) subclasses this and gets the same non-negotiable gate
   `WifiJammerNG` has, for free, instead of re-implementing (or forgetting
   to implement) it from scratch.

4. **Safe subprocess conventions.** If a future plugin ever does need to
   shell out, a helper that only accepts real argument lists (never
   `shell=True`), as a reminder/guardrail against the `aireplay-ng`-with-a-
   hardcoded-path style bugs found repeatedly in the audited ecosystem.

5. **A shared webhook-page HTML template.** `WifiJammerNG`'s `on_webhook`
   builds a small manual-control page (list authorized targets, "fire now"
   links, refuse anything unauthorized). Worth factoring the page shell
   (nav, styling, the authorized/refused messaging pattern) out into a
   shared template method so every future plugin's manual-control page
   looks and behaves consistently instead of each one reinventing it.

6. **Real interface/config lookup.** A `self.iface` property (or similar)
   that reads `pwnagotchi.config['main']['iface']` once and caches it,
   instead of every plugin re-deriving or hardcoding it — the exact fix
   identified for `beacons.py` (hardcodes `wlan0mon`) versus its sibling
   `beaconify.py` (reads it from config correctly).

None of this is implemented yet. It's scoped narrowly on purpose: each
piece above maps to a specific, already-diagnosed bug class from the audit,
not a speculative feature.

## Three feature ideas that could sit on top of it

These are independent of each other and of the base class above — any of
them could be built without the others, though all three would benefit
from `BeastagotchiPlugin` existing first.

### 1. GPS-tagged coverage map

Use the location data `GPSTaggerNG` (in `patrickato/plugins-wip`) already
attaches to handshakes to build an actual map of where networks were seen,
themed to match Beastagotchi's UI. Rough shape: a small adapter plugin that
reads GPS-tagged handshake records and feeds them into a Beast module/app
(per the three extension classes already defined in
`Beastagotchi_Plugin_Extension_Architecture_v0.1.md`) that renders a
coverage map — density of captures by location, maybe a simple "conquest"
history over time. Sits naturally alongside the existing rare/achievement
systems referenced elsewhere in `docs/`.

### 2. Unified lab console

Generalize `WifiJammerNG`'s single-plugin "fire now" webhook page into one
shared control panel: a single page listing every authorized-target-gated
tool currently installed (not just wifi jamming — anything future built on
the `BeastagotchiPlugin` allowlist helper), with one shared target list
maintained in one place instead of duplicated per-plugin config. Would
plug into the plugin lifecycle states (`AVAILABLE -> STAGED -> INSTALLED ->
ENABLED`) already sketched in the Plugin Extension Architecture doc.

### 3. `beast_bridge.py` telemetry expansion

`pwnagotchi_plugin/beast_bridge.py` already exists in this repo. Idea: richer
live stats flowing through it — deauth/assoc/handshake counts (sourced from
whatever `BeastagotchiPlugin`-based plugins are active), GPS-tagged
"conquest" history (feeding from idea #1 above) — surfaced through Beast
Core's existing telemetry/state plumbing (`beastcore/telemetry.py`,
`beastcore/state.py`) rather than a new, separate reporting path.

## Design rule carried over from the audit (non-negotiable)

Any future plugin in this family that can *fire* something at a target
(deauth, association, anything intrusive) must gate every firing path
behind an authorized-target allowlist that is **empty by default**. Every
other aspect of usability (auto-fire on multiple hooks, no manual trigger
required, relaxed defaults) can be made as convenient as wanted — this one
gate is the exception. This was the explicit agreement reached while
building `WifiJammerNG`, and it's the rule `BeastagotchiPlugin`'s allowlist
helper above is meant to make automatic instead of something each new
plugin has to remember on its own.

## Open questions (not yet decided)

- Exact file path/module name for `BeastagotchiPlugin` within this repo.
- Whether it lives purely in `pwnagotchi_plugin/` (pwnagotchi-side only) or
  needs a Beast Core–side counterpart for the lifecycle/telemetry pieces.
- Which of the three feature ideas (if any) to build first.
- Whether the shared webhook template should be a Jinja template file or a
  plain Python string-builder method, to match whatever convention the
  rest of this repo already uses for HTML output.

## Source material

Full bug-by-bug detail behind the patterns summarized above lives in
`patrickato/test-plugins`:

- `plugin-upgrade-proposals/cluster-33-network-security/NOTES.md` — the
  `BasePlugin`-doesn't-exist defect and the `wifi_jammer.py` → `WifiJammerNG`
  rebuild rationale.
- `patrickato/plugins-wip/wifi-jammer-suite/NOTES.md` and
  `wifi_jammer_ng.py` — the actual allowlist/cooldown/webhook implementation
  this proposal generalizes from.
