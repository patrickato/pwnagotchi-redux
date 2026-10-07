# PLATFORM TARGET — Raspberry Pi 4 / Pi 5 (arm64) only

_Decision, locked by the owner (2026-10-06). This is a hard target, not a preference._

## The decision

redux targets **Raspberry Pi 4 and Pi 5, arm64, only.** We do **not** carry the "lesser" boards —
Pi Zero / Zero 2 W, Pi 3 and earlier, or 32-bit (armhf) userlands. pwnagotchi and Bjorn can keep
them; chasing a 512 MB single-core Zero is what kept that whole generation of tooling slow, flaky,
and compromised on features. We build for the hardware that can actually run what the atlas
describes.

## Why this is the right call

Dropping the low end isn't about exclusion — it's what unlocks the product:

- **arm64 everywhere.** One architecture, 64-bit. No armhf build matrix, no "works on Pi 4 but not
  Zero" driver/firmware splits, simpler nexmon + kernel story.
- **RAM headroom.** 2–8 GB (Pi 4) / 4–16 GB (Pi 5) vs the Zero's 512 MB. The on-device pieces the
  atlas wants — SpatialDB, a live offline PMTiles map on the TFT, the glass-box brain, event
  correlation, a local wordlist tier — need real memory. They're impossible on the low end.
- **USB 3 (Pi 4/5).** The Alfa / MT7612U-class adapters and an RTL-SDR want USB 3 bandwidth and
  clean power; this is exactly the brownout failure mode the Radio Orchestrator already warns about.
  USB 3 is the difference between "works" and "browns out on a shared hub."
- **Compute for the brain + fusion.** Quad-core A72/A76 handles multi-radio ingest, trilateration
  math, and SDR decoding that a Zero cannot.
- **Fast boot + modern I/O.** Pi 5's PCIe (NVMe) and faster I/O make the read-only-rootfs,
  fast-boot, and A/B-OTA goals (atlas X-2/X-6/X-7) genuinely achievable.

## What this means in practice

- **Image:** the pi-gen image is **arm64**, built and tested for Pi 4 and Pi 5. No armhf artifact.
- **Reference hardware:** Pi 4 **and** Pi 5, each with the 3.5" MPI3501 TFT (480×320), on the
  jayofelony 64-bit bettercap/nexmon base. On-screen elements stay monochrome-safe for the small
  TFT regardless of board.
- **Acceptance criteria** that named "a Pi 4" now read **"Pi 4 / Pi 5"** — a real-hardware pass
  should cover both where they differ (power, boot, PCIe on the 5).
- **Dependencies/features may assume** arm64, ≥2 GB RAM, and USB 3. Don't add a workaround whose
  only purpose is to keep a Zero/Pi 3 alive.

## Non-goals

- Pi Zero / Zero 2 W, Pi 3B/3B+/3A+ and earlier, and any 32-bit (armhf) target. Not supported, not
  tested, not a bug if it doesn't run there.
- "It might also work on an old board" — we make no such claim and spend no effort on it.

_See `docs/CORDCUT_ARCHITECTURE.md` for the full stack and `docs/IDEA_ATLAS.md` for the capabilities
this headroom is meant to carry._
