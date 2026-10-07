# Augur — identity, voice, and face

> The platform's name and soul. This is the north star every surface points at: the
> on-device TFT, the web face, the narration, the CLI copy, and the naming lexicon.
> If a new piece of UI or copy doesn't fit what's here, fix the piece.

## What Augur is

**Augur** is a small, patient, glass-box familiar that reads the invisible RF world and
tells you *true*. An augur is a diviner who reads signs from birds; here the birds are the
frames and signals in the air, and the reading is honest by construction — every decision
the device makes carries a real, human-readable reason, and the creature never claims a
sense or a capability it doesn't have.

That honesty *is* the brand. Pwnagotchi is a pet that pwns; Augur is an instrument with a
pulse — a corvid-cold, clever watcher that remembers faces, hoards what it finds, and reads
the air for you. It is calm and precise, a little dry, never cute-for-cute's-sake and never
dramatic. The corvid underpins the whole lexicon below: crows remember individual faces
(re-identification), cache what they find and recall every spot (the sighting store), and
work a flock that shares what each bird sees (the mesh).

## Voice

Terse, concrete, honest. Augur reports *reasons*, not vibes, and it would rather admit a
blind spot than fake a reading.

- **Do:** `on the hunt — ch6, 3 clients on a WPA2 AP, 1 PMKID elicited`
- **Do (honest):** `blind here — no monitor radio; recon only`
- **Do (dry):** `skipping that one — WPA3-SAE, nothing to crack`
- **Don't:** `OMG shiny new network!!` — no drama, no exclamation spam.
- **Don't:** invent a count, a position, or a verdict it can't trace to a real reason.

The Narrator already turns every subsystem's machine reason into these lines; Augur's voice
is just that, rendered with a mood. Never invent, always traceable.

## The face

Augur's face is **not decoration** — every expression maps to a real machine state, so a
glance tells you what the device is doing and why. The `‹ ›` beak-frame is the signature; the
eyes carry the state. One engine (`redux/face/`) drives both the TFT and the web face, so the
creature has one identity whichever way you look at it.

| State | eyes (nice) | eyes (mono) | Shows when… | Reason line |
|---|---|---|---|---|
| `WATCH`  | `‹·_·›` | `<._.>` | idle / quiet — the default patient watch | watching — quiet |
| `READ`   | `‹o_·›` | `<o_.>` | re-deciding radios / assessing a network (thinking) | reading the air |
| `HUNT`   | `‹►_◄›` | `<=_=>` | arranged to capture (hunting) | on the hunt |
| `MARK`   | `‹!_·›` | `<!_.>` | a genuinely new device/face just seen (transient) | new face |
| `CACHE`  | `‹^_^›` | `<^_^>` | a capture just landed and was stored (transient) | cached it |
| `RUFFLE` | `‹✺_✺›` | `<*_*>` | a detector fired — feathers up (alert) | *the alert's own reason* |
| `BLIND`  | `‹-_-›` | `<-_->` | no way to perceive (no capture engine **and** no CSI) | blind here — no monitor radio |
| `ROOST`  | `‹u_u›` | `<u_u>` | settled with mesh peers present | roosting with the murder |

`BLIND` is the one to notice: it's the honesty state. If Augur can't see, its face says so
rather than wearing a busy expression over nothing. Precedence at a glance: a live **critical
alert** wins, then **blindness**, then a fresh capture/new-face, then what it's actively
doing, then roosting, else the watch.

## Animation policy (house rule)

**Motion only ever marks a real event. Nothing animates on a timer.**

- **On-device TFT:** a *static* face, redrawn only when the state changes. A 3.5" SPI panel
  redraws over a slow bus on Pi CPU, so a loop there is literal heat and battery. Event-driven
  redraw costs nothing when nothing's happening — which is most of the time.
- **Web face:** client-rendered (free for the Pi), so it may do a one-shot *pop* when the face
  changes and a colour shift for `RUFFLE` (red) / `BLIND` (dim). Still only on a real
  transition — no idle loop.

No pwnagotchi-style constant blinking, no Bjorn-style set-pieces. The payoff is that any
motion you see *means something* — a pop is a real state change, a red face is a real alert.

## The lexicon (retiring "beast this, beast that")

Augur's vocabulary, so names stop being `beast-*` and start fitting the creature. Code names
stay clear and descriptive (good engineering); the themed names are what the operator sees.

| Concept | Augur name | Status |
|---|---|---|
| The platform / core object (was `Beastcore`) | **Augur** (`redux.core.Augur`) | ✅ done |
| The sighting store (was `BeastSpatialDB`) | **the Cache** (`SpatialDB` in code) | ✅ renamed in code |
| Capability bundles (was "Beast Packs") | **Packs** | ✅ done |
| Glass-box diagnosis (the `Doctor`) | **the Auspex** (user-facing label) | lexicon; code class stays `Doctor` |
| The mesh swarm of devices | **the Murder** | user-facing; `redux.mesh` in code |
| Mesh sync messages | **Calls** | user-facing |
| RSSI direction-finding (`redux.hunt`) | **the Hunt** | ✅ fits already |
| Cross-MAC re-identification (`redux.dex`) | *"Augur never forgets a face"* | tagline |

Code module names (`sense`, `dex`, `crack`, `mesh`, `report`, `captap`, …) stay as they are —
they're already clear and un-beasted. A handful of internal design-doc terms (`BeastPlot`, the
"Beast Recon Suite") are legacy vocabulary migrating as those docs are touched; references to
the **sibling `beastagotchi` repo** are a different real project and keep their name.

## Where it renders

Two surfaces, split by where the pixels cost something:

- **TFT (lean):** `redux/tft/screen.py` — bordered, box-drawing, monochrome-safe, the face +
  one name line + glass-box gauges, static. Niceties without the heat.
- **Web (rich):** `redux/web/status_page.py` — the big face, the live moving map (real GPS
  fixes only), the activity sparkline, plain/rich skin toggle. Client-rendered, zero Pi cost.
