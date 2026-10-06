# Beast Pack SDK — v0.19

Beast Packs are optional capability/content bundles for Beastagotchi. The base
platform stays small and stable; Packs live in managed storage and enter through
the same inspect → verify → stage → install lifecycle.

## Start here

Every Pack archive contains exactly one root `manifest.json`. The archive may
optionally have a single top-level folder; intake normalizes that away.

Supported archives:

- `.zip`
- `.tar.gz`
- `.tgz`

The current intake limit is 128 MiB compressed, 512 MiB unpacked and 2048 archive
members. Symlinks, hard links, devices, absolute paths and path traversal are
rejected.

## Minimal manifest

```json
{
  "id": "my-pack",
  "label": "My Pack",
  "version": "1.0.0",
  "pack_type": "theme",
  "resource_class": "none",
  "thermal_class": "static",
  "source": {
    "type": "github_release",
    "url": "https://github.com/owner/repository/releases",
    "channel": "stable"
  }
}
```

IDs use lowercase letters, numbers, dots, underscores and hyphens. Keep the ID
stable forever; versions change, identity does not.

## Current content-only activation tier

These classes may be enabled without executing Pack code:

`theme, face, animation, audio, layout, board, data, map`

This tier rejects executable/code-like files, executable permission bits,
privileged permission requests, background services and service restart
declarations.

Not every safe class has a consumer yet. **Theme, Face, Board and Layout Packs are
fully connected v0.19 consumers.**

### Theme Pack

```
manifest.json
themes/
  orchard.json
```

Each theme filename must match its internal `id`. Built-in Beast theme IDs win
collisions.

### Face Pack

```
manifest.json
faces/
  orbital.json
```

Face Packs are declarative and never import Pack code. The selected Face Pack
changes how canonical Beast moods are drawn; Beast Core/Face Engine still decides
what the creature is feeling.

Current renderers:

- `glyph` — mood → Unicode/text glyph string;
- `vector` — mood → bounded lists of safe normalized drawing primitives.

Vector coordinates use a 0–1000 logical box and may use lines, ellipses,
rectangles, rounded rectangles, polygons and arcs. Colors refer to semantic
theme roles such as `primary`, `accent`, `danger`, `text` and `dim`.
This keeps community faces theme-aware and cheap to render.

### Animation Pack

v0.19 Animation Packs are intentionally lightweight choreography rather than
video/GIF/frame bundles. The first target is `face`.

```
manifest.json
animations/
  float.json
```

A profile can declare bounded bob, sway and breathing motion plus up to four
orbit markers. Optional per-mood multipliers make alert/excited motion quicker
and sleeping motion calmer. The runtime performs only a few trigonometric
operations and primitive draws, keeping the animation layer reusable across
built-in faces and Face Packs with very low storage and CPU cost.

### Mission Pack / Experience Profile

Mission Packs are the composition layer. They may depend on other Pack IDs and
reference their reusable Theme, Face, Animation, Board/Layout and Context Deck
content.

A Mission becomes an **Experience Profile** when it contains presentation
selections such as `theme`, `face_profile`, `animation_profile`, `board`
or `layout`.

This is intentionally not a new monolithic Experience Pack type. A WOPR,
LCARS, Field Scout or seasonal experience can be a tiny Mission Pack that
assembles independently reusable components.

v0.19 discovers, validates and previews Experience Profiles. Applying a full
profile transactionally to user preferences is deliberately the next gate.

#### Optional Experience DNA

A Mission Experience may now add a declarative `experience_dna` block plus a
trusted renderer reference:

```json
{
  "experience_renderer": "atlas",
  "experience_dna": {
    "visual_family": "expedition",
    "layout_family": "map_first",
    "density": "balanced",
    "motion_profile": "calm_ambient",
    "creature_presence": "supporting",
    "utility_bias": "instrument",
    "playfulness": "low",
    "alert_style": "field",
    "input_model": "touch_first",
    "doctor_visibility": "contextual",
    "mystery_level": "discoverable",
    "hardware_fit": ["reference", "medium", "large", "desktop"],
    "mission_bias": ["field", "journey", "location"],
    "tags": ["field", "gps", "companion"]
  },
  "experience_policy": {
    "requires": ["display.primary"],
    "optional_requirements": ["location.position", "radio.wifi.monitor"],
    "preferred_pages": ["home", "recon", "map"],
    "presentation_engine": "beast_scene",
    "fallback_policy": "identity_preserving"
  }
}
```

Rules:

- Pack DNA is declarative data only; it does not load Pack rendering code.
- `experience_renderer` references an already-registered Beast Experience
  renderer such as `atlas`, `forge`, `observatory`, `habitat` or
  `monolith`.
- Pack identity/DNA remains distinct from the referenced renderer. Studio labels
  both so a Pack Experience does not masquerade as the built-in renderer.
- Core semantic axes such as density, creature presence and utility bias use the
  closed Beast vocabulary so Resource Governor/Doctor can reason about them.
- Presentation vocabularies may extend through namespaced values such as
  `vendor.biomech`; unnamespaced collisions are rejected.
- Core compiles Pack Experiences through the same long-lived
  DependencyCapabilityResolver used by Plugins/Packs.
- Missing Pack/capability requirements make the plan non-previewable; Core does
  not install/select anything on the Pack's behalf.
- Preview never writes preferences and never claims TFT ownership.

### Board Pack

```
manifest.json
boards/
  field.json
```

A Board file is a dashboard destination:

```json
{
  "id": "field",
  "label": "Field Board",
  "widgets": [
    {
      "id": "cpu",
      "key": "system.cpu.total",
      "label": "CPU",
      "style": "radial",
      "x": 0, "y": 0, "w": 4, "h": 3,
      "min": 0, "max": 100
    }
  ]
}
```

Enabled Board Packs appear as **read-only launcher destinations**. Beast scopes
their runtime IDs by Pack so community boards cannot collide with personal
boards. Their telemetry still comes from canonical live Beast state.

### Layout Pack

```
manifest.json
layouts/
  triad.json
```

A Layout uses the same widget geometry as a Board, but it is a **template**.
Beast Studio shows enabled Layout Pack templates and imports one only when the
user explicitly asks. Importing creates a normal editable personal Board; the
Pack template remains untouched.

## Widget contract

The 480×320 reference Dashboard uses a 12×8 logical grid. Current generic widget
styles are:

- `metric`
- `bar`
- `radial`
- `microtrend`

A widget binds a canonical Beast telemetry key. Unknown/malformed fields are
normalized by Beast Studio rather than trusted blindly.

## Distribution

The recommended v0.19 distribution is a GitHub Release containing:

- the Pack archive;
- a SHA-256 sidecar asset.

Example:

```
field-layouts-1.0.0.zip
field-layouts-1.0.0.zip.sha256
```

A repository appearing in a Depot catalog does **not** automatically become a
trusted unattended-update source. Depot discoverability and source trust are
separate boundaries.

See the examples under `pack-sdk/examples/`.
