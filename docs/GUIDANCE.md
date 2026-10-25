# In-product guidance: info-bubbles, when-to-use, how-to (`redux help`)

A new user shouldn't have to read source or hunt through `docs/` to learn how
Augur actually works. `redux/guidance.py` is the **single, structured source of
in-product help** — and it's the answer to *"where do the tutorials and
info-bubbles get added?"*: **here, in one place, and they appear on every surface
at once.**

Each topic is a `HelpTopic` with:

- **`bubble`** — a one-line info-bubble a UI shows next to a control (hover on
  web, long-press on the TFT),
- **`when`** — when a user would reach for this,
- **`how`** — a beginner step-by-step,
- **`command`** — the CLI command it maps to,
- **`related`** — links to sibling topics.

## Using it

```
redux help                     # list every topic
redux help lab-vs-field        # the full how-to for one topic
redux help --search pager      # find topics by keyword
```

Topics today: getting-started, lab-vs-field, scope, doctor, peripherals, sdr,
crack, wordlists, wps, karma, tracker, oui, learn, push, mesh, safety.

## Adding a tutorial or info-bubble

Append (or extend) a `HelpTopic` in `redux/guidance.py`:

```python
HelpTopic(
    id="my-feature", title="My feature",
    bubble="One line a UI shows as a hint.",
    when="When a user would use this.",
    how="1. step\n2. step\n3. step",
    command="redux my-feature",
    related=("scope",),
)
```

That's it — `redux help`, and any TFT/web surface that reads the registry, pick
it up immediately. (A test enforces that every `bubble` is short and every
`related` id resolves, so the help stays consistent.)

## Every surface reads the same source

- **CLI**: `redux help` (and `redux help <topic>` / `--search`).
- **TFT**: a help tab or a long-press bubble reads `guidance.bubble(id)` /
  `guidance.get(id)`.
- **Web / phone**: hover bubbles and a Help page read the same registry via the
  status API.

Because the content lives once, the three surfaces can never drift out of sync —
and the Doctor's remediation hints (`redux doctor`) complement this with
*live* "do: …" guidance about the specific device's state.

## Honest by construction

The help never promises a capability the device doesn't have — e.g. the `sdr`
topic explicitly says a live waterfall display isn't built yet, and `safety`
states the real contract (scoped/gated; the handful that hit bystanders stay
off). A test pins these so the guidance can't quietly start over-promising.
