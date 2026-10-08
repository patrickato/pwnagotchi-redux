# Hardening backlog — "have we thought of everything?"

The 13 subsystems are feature-complete and green in the sandbox. This is the honest
list of what stands between a green sandbox and a thing you hand to pros and
classmates to run on real sites. These are **tracked, not yet built** (except where
noted). Mostly hardening/productization, not missing core capability. Ordered by
leverage.

## P0 — the one architectural gap — PRODUCER DONE

- **Raw-frame capture tap.** ✅ Built (`redux/captap/`, docs/CAPTAP.md): a pure
  802.11 parser (deauth/disassoc + probe-request IE fingerprint) + a tap that feeds
  the fingerprint Dex real PNL/IE (cross-MAC re-id now works, sandbox-verified) and
  stages normalized deauth events. **Remaining:** (a) the detector-side consumption
  of those deauth events (deauth-flood / surveillance-sweep) lives in the detector
  lane — coordinate with Grok; (b) live monitor capture on the Pi (needs-hardware,
  runbook §2b.1/2b.6).

## P1 — field-device safety (a dropbox can be found/seized)

- **Secrets at rest.** Handshakes, cracked PSKs, EAP hashes, the Scope, and the
  **swarm key** sit in plaintext. Add loot-at-rest encryption (key from a boot
  secret / operator passphrase) so a seized card doesn't spill the engagement.
- **Anti-tamper dead-man.** We have CSI motion + boot-POST; wire "tamper detected →
  wipe loot / alert over LoRa" (the CSI anti-tamper idea). Opt-in, glass-box.
- **Swarm-key lifecycle.** Mesh deltas are HMAC-signed, but there's no key
  generation/rotation/distribution story. Define one (generate on arm-lab, QR/LoRa
  exchange, rotate per job).

## P1 — surface auth

- **Web dashboard auth — DONE.** Fail-closed token gate (`redux/web`): `localhost`
  stays open (single-user loopback), but any off-box bind (`lan`/`tailscale`)
  requires a token — supply one with `--token` or `serve(token=…)`, else one is
  minted and printed to the operator console. Token travels as an
  `Authorization: Bearer` header or an `augur_token` cookie (never in a URL),
  compared in constant time; `/api/status` returns 401 without it and `/` shows a
  self-contained unlock page. **Remaining:** transport is still plain HTTP — pair
  with TLS (or keep it inside the tailnet) for anything crossing an untrusted LAN.

## P2 — productization / onboarding

- **First-run + field-operator guide.** Flash → first boot → arm scope → set swarm
  key → EAP cert. Current quickstart is dev-focused; write the operator path for the
  community.
- **Unified config.** Config is scattered across JSON stores + per-module defaults.
  A single `config.toml` (paths, default persona, bind scopes, keys) with the fork's
  `>>> USER INPUT REQUIRED <<<` markers.
- **Data lifecycle — DONE.** The Cache (`SpatialDB`) can now be bounded:
  `prune(older_than=…, max_rows=…)` (single commit, SD-friendly; opt-in `vacuum()`),
  `export(path, fmt=jsonl|csv)` to pull it off-box, and `stats()` for honest
  count/per-kind/time-span. CLI: `redux cache stats|prune|export`. **Remaining:** a
  default on-device retention *policy* (a scheduled prune) — the mechanism is here,
  the cron/age defaults are an operator/config choice (pairs with the config.toml item).

## P2 — UI polish (optional, by where it renders)

- **Rich web dashboard — DONE** (moving map + track + pins + sparkline + plain/rich
  skin; client-rendered, zero Pi cost). Further: offline map tiles when internet is
  present, a channel waterfall / RSSI meter panel, per-security pin coloring (needs
  an encryption field on sightings).
- **TFT stays lean on purpose** (SPI redraw = Pi CPU → heat/battery). Candidate:
  selectable low-cost "faces" (pwnagotchi-style), NOT heavy animation.

## Known, already-coordinated

- **Detector-side ATT&CK/D3FEND tags** — the mapping layer exists (`redux/frameworks`);
  tagging the detectors inline is Grok's lane, coordinated.
- **Governor real collector** — needs the on-Pi `vcgencmd`/battery feed
  (needs-hardware; runbook §2b.9).

## Not building (would be theater on this hardware)

- Wi-Fi 7 / 6GHz / MLO offense (no Pi 7 radio), on-device LLM operator (infeasible +
  unsafe; the deterministic operator is the answer), FTM/802.11mc ranging (not
  Pi-ready), BLE GATT MITM (needs BLE hardware + its own subsystem).
