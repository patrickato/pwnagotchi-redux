# Engine bake-off — bettercap vs AngryOxide

**Status: implemented.** The decision below is wired — `redux/crack/capture.py`
registers both engines as `CAPTURE_HANDSHAKE` providers (AngryOxide preferred when
present, honest tool-absence fallback to bettercap), `Augur.capture_plan()`
builds a Scope-aimed, posture-correct plan, and `redux capture plan` shows it
(incl. the exact AngryOxide command it would run).

**Decision record.** Which 802.11 engine drives redux's capture path, and how.
Grounded in the two projects' own docs (bettercap `wifi` module; AngryOxide
README), reasoned against our architecture. **No on-hardware A/B yet** — the
throughput/thermal claims below are reasoned from design, not measured on a Pi;
that pass is a `HARDWARE_VALIDATION.md` item.

## TL;DR recommendation

**Run both, behind the capability graph. They are not competitors — they are
different jobs.**

- **bettercap stays the nervous system.** It is the always-on recon + control
  surface: `wifi` *and* `ble` *and* `hid` *and* the `graph` module, driven by a
  REST + websocket events API that our `event_bridge` already consumes. Nothing
  else gives us one API across every radio. It also owns rogue-AP/evil-twin
  (`wifi.ap`) and on-device bruteforce.
- **AngryOxide becomes the capture *scalpel*.** It registers as an alternate
  `CAPTURE_HANDSHAKE` provider for when the intent is "get a *validated-crackable*
  handshake fast and surgically" — especially against WPA3-transition / PMF
  targets. The capability graph already resolves alternate providers and the
  Doctor already explains `active_provider`, so the device can say *which* engine
  is capturing and *why* — and fall back honestly to bettercap capture when the
  AngryOxide binary isn't present (`present=False`).

Right tool per intent. `redux persona recon` → bettercap breadth; a hunt with
`--engine angryoxide` → the scalpel.

## Capability matrix

| Capability | bettercap `wifi` | AngryOxide |
|---|---|---|
| Language / footprint | Go, full framework | Rust, lean; cross-compiles to embedded (MIPS example in README) → arm64 fine |
| Driven by | REST + ws events API, Otto/caplets | CLI + TUI + `--headless`; no general API |
| Recon breadth | WiFi **+ BLE + HID + net + device graph** | WiFi only |
| PMKID | `wifi.assoc` (RSN PMKID) | **auto-elicits PMKID**; also assoc→EAPOL M1 |
| EAPOL | 4-way via recon/deauth | M1 via assoc; **RogueM2 collects M2 from probe requests alone** |
| Handshake *validation* | none (capture raw, sort later) | **nonce correction + replay-counter + temporal validation** — knows if it's crackable |
| Deauth | `wifi.deauth` (blunt) | **rate-controlled, limited to not harm the auth sequence** |
| Disassoc | — | **WiFi-6E codes that avoid client blacklisting** |
| MFP / 802.11w | not addressed | **anonymous reassociation = MFP bypass** |
| WPA3 / downgrade | cipher parsing not documented | **RSN downgrade via probe-response injection** |
| CSA | `wifi.channel_switch_announce` | `--autohunt` + CSA to herd clients |
| Rogue-AP / evil-twin | **`wifi.ap` beacon injection** | — |
| On-device bruteforce | **`wifi.bruteforce`** | — (hands off to hashcat) |
| Channel control | `wifi.hop.period` (250 ms) | `--autohunt` locks target channels, `--dwell`, band select incl 6/60 GHz |
| Output | pcap (pcapng not documented on the module page) | **pcapng w/ embedded GPS (Kismet fmt), kismetdb, native hashcat 22000**, `--combine` |
| Targeting | `wifi.recon BSSID` filter | `-t` MAC/SSID, `--targetlist`, `--whitelist`, `--notransmit` passive |
| Autonomy | needs an external loop | **`--autoexit` when all targets have valid hashlines**, gpsd + geofence built in |

## Where AngryOxide is genuinely better (and why it matters to us)

1. **It validates handshakes before you crack.** Nonce-correction + replay +
   temporal validation means it knows a capture is actually crackable. That feeds
   our `classify/` crackability scoring and means **crack-house never burns a
   cycle on garbage**. (we have a crack pipeline + a NAS offload cracker in the
   atlas → AngryOxide makes sure only real work reaches them.)
2. **Native hashcat 22000 + `--combine`.** Drops *straight* into our crack
   pipeline and the NAS offload path — the format is the contract, no shim.
3. **pcapng w/ embedded GPS (Kismet format) + kismetdb.** Drops *straight* into
   our geo stack — we already have a Kismet importer. Another path-is-the-contract
   win: capture on one tool, map on ours, no glue.
4. **Modern full-capability offense we don't have via bettercap:** MFP bypass
   (anonymous reassociation), RSN downgrade, disassoc codes that dodge
   blacklisting, and *surgical* rate-limited deauth that doesn't nuke the auth
   handshake it's trying to catch. This is exactly the "full power, not lesser"
   bar — all of it scope-gated at the aiming layer, unrestricted inside scope.
5. **`--autoexit` + geofence + headless** = a self-terminating, scope-bounded
   autonomous capture run. Perfect for a scheduled expedition.

## Where bettercap stays essential

1. **One API, every radio.** BLE + HID + WiFi + the device graph over REST/ws.
   AngryOxide is WiFi-only with no API — you'd script around its TUI.
2. **Our whole event spine already speaks bettercap.** `event_bridge`, the
   supervisor pump, the detectors are fed from bettercap's normalized events.
   Ripping it out would re-plumb the nervous system for no gain.
3. **Rogue-AP/evil-twin + on-device bruteforce** live here (`wifi.ap`,
   `wifi.bruteforce`).
4. **It reads from a pcap** (`wifi.source.file`) → our record/replay + ghost path
   works against it with zero hardware.

## How it slots in (the capability-graph play)

- `CAPTURE_HANDSHAKE` gets two providers: `bettercap-capture` (present whenever
  the driver is up) and `angryoxide-capture` (present only if the binary is on
  PATH — honest tool-absence). Registration order / an `--engine` option / the
  active persona decides preference.
- AngryOxide runs as a managed subprocess in `--headless` with `-t` pulled from
  the **central Scope** (only armed targets), `--autoexit`, and its 22000 +
  pcapng outputs pointed at the crack-pipeline and sighting-store default paths.
  So Scope still decides *where* it's aimed; AngryOxide just aims harder.
- The Doctor's capture probe reports which engine is active and why; `redux post`
  shows it at boot.

## Net

bettercap is the platform; AngryOxide is the payload for the one job it's clearly
better at. Adding it is **pure upside, scope-gated like everything else**, and
most of the integration is already paid for because both speak formats our geo
and crack stacks already consume.
