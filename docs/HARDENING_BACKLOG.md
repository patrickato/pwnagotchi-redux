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

- **Secrets at rest — DONE (the protective half).** `redux/vault/` seals captured
  data (handshakes, cracked PSKs, EAP hashes, the Scope, the swarm key, an exported
  Cache) with Fernet (AES-128-CBC + HMAC-SHA256), key derived from an operator
  passphrase via scrypt; fresh salt per seal in the envelope. Optional `crypto`
  extra, and **honest/fail-closed**: no backend → the Vault refuses rather than
  writing plaintext. CLI: `redux vault seal|open`, `redux cache export --encrypt`
  (sealed in memory — plaintext never hits disk). **Remaining:** seal the live
  on-device stores in place at shutdown / open at boot (lifecycle wiring), and the
  swarm-key item below.
- **Anti-tamper "dead-man" wipe — NOT building (by design).** A destroy-loot-on-tamper
  mechanism is anti-forensics, not data protection, so it's deliberately out of
  scope. At-rest encryption (above) is the protective answer for a lost/seized card.
  A non-destructive "tamper detected → alert over LoRa" could be considered separately
  if wanted, but nothing here wipes data.
- **Swarm-key lifecycle — DONE.** `redux/mesh/keyring.py`: generate a random key,
  **rotate** per job (new current + a bounded grace window of recent keys so
  in-flight deltas still verify — `ScopeSync.verify_keys`), per-key **expiry** (a
  lapsed key stops authorizing), out-of-band **exchange** via an `AKEY1` token
  (QR/LoRa), and a keystore **sealed at rest** through `redux.vault` (honest/
  fail-closed). CLI: `redux mesh key gen|rotate|show|export|import`. **Remaining:**
  auto-wire the active key into the running `ScopeSync` from the keystore at boot
  (config item), rather than passing keys in by hand.

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
- **Unified config — DONE.** `redux/config/`: one typed, validated `config.toml`
  (Cache paths + retention, web bind-scope/port/token, mesh keystore + node_id,
  default persona, the at-rest passphrase *env-var name*) built on the fork's
  `defaults.toml` style with explicit `>>> USER INPUT REQUIRED <<<` markers. Partial
  files merge over defaults; `validate()` returns all problems at once. CLI:
  `redux config init|show|check` (token masked in `show`). Secrets aren't stored —
  only the passphrase env-var *name*. **Remaining:** have each subsystem actually
  read it (wire `redux web` / `cache prune` / `mesh` to resolve from the file),
  which is the incremental adoption step.
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
- **TFT stays lean on purpose** (SPI redraw = Pi CPU → heat/battery). Selectable
  low-cost **face-packs — DONE** (`redux/face/packs.py`: augur/owl/fox, static,
  `--pack` / `[tft] face_pack`), NOT heavy animation. Remaining candidates: a
  channel-waterfall / RSSI meter panel (web), offline map tiles.

## Known, already-coordinated

- **Detector-side ATT&CK/D3FEND tags** — the mapping layer exists (`redux/frameworks`);
  tagging the detectors inline is Grok's lane, coordinated.
- **Governor real collector** — needs the on-Pi `vcgencmd`/battery feed
  (needs-hardware; runbook §2b.9).

## Not building (would be theater on this hardware)

- Wi-Fi 7 / 6GHz / MLO offense (no Pi 7 radio), on-device LLM operator (infeasible +
  unsafe; the deterministic operator is the answer), FTM/802.11mc ranging (not
  Pi-ready), BLE GATT MITM (needs BLE hardware + its own subsystem).
