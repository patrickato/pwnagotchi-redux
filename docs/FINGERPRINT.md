# Device fingerprinting — identity across MAC randomization

## Why

A MAC-keyed Dex loses a phone the instant it re-randomizes its MAC — which modern
phones do constantly. But a device carries MAC-*independent* signatures: the set
of networks it probes for (its **Preferred Network List**) and the exact template
of its probe-request **information elements**. Two different MACs that share those
are almost certainly the same device. Linking on them turns the Dex from "MACs
I've seen" into **"devices I've seen"** — a logbook into a recon brain, and a
direct answer to the cross-device recon you care about (a device fingerprinted at
node A is recognized at node B even though its MAC changed).

## What's built (sandbox-verified)

Pure identity math in `redux/dex/fingerprint.py`:

- `is_randomized(mac)` — the locally-administered bit.
- `fingerprint(obs)` → a `Fingerprint` with a MAC-independent `key` when linkable,
  a `strength` (0–1 distinctiveness), `linkable`, the `features` that formed it,
  and a reason.
- `DeviceLinker` — clusters observations into `DeviceIdentity`s; MACs sharing a
  fingerprint collapse into one device (`reidentified` when >1 MAC folds in).
- `observations_from_store(store)` / `link_store(store)` — a best-effort adapter
  over recorded sightings.
- CLI: `redux dex --identities` shows devices re-identified across MAC
  randomization (and how many MACs folded into each).

## The honesty line (the failure mode here is a *false link*)

Claiming two different devices are one is worse than missing a link, so the guards
are strict:

- **OUI is never a cross-MAC signal.** A randomized MAC's vendor prefix is noise;
  it's used only for a *non-randomized* MAC, and even then it only labels — it
  never links two MACs.
- **Linking requires a MAC-independent signal** (PNL or IE template). With
  neither, a device is **not linkable**: each randomized MAC stays its own
  ephemeral identity. "Couldn't link it" is never "it's new."
- **Strength is reported.** A link off a one-network PNL is weak and labelled
  weak; a rich PNL or an IE template is strong. Every identity shows its evidence.

## What needs the raw-frame tap (needs-hardware / future)

The linker is complete; what makes it *sharp* is rich input. Today's event stream
doesn't separate a client's directed probes from an AP's beacon and captures
neither full PNL nor IE templates, so the store adapter is weak (often empty PNL →
unlinkable). The moment the probe-request / raw-frame tap (the same gap the
raw-frame detectors have) feeds real PNL and IE hashes into `DeviceObservation`,
re-identification gets strong — **the logic does not change**, only the input
quality. That separation is deliberate: the part that could over-claim is the math,
and the math is done and guarded; the part that's missing is honest data capture.

## Scope / privacy note

Cross-MAC re-identification is genuinely surveillance-capable. It fits "scope, not
a cage" — passive to compute, and meaningful only against a space or target you're
authorized to assess — and, like CSI, the glass-box evidence on every link is
deliberately loud here.
