"""Capability manifest — the one honest catalog of everything Augur can do.

The capability *graph* (redux/core/capabilities.py) answers "given what's present
right now, what's available and why." This manifest is the complementary *catalog*:
every feature the project ships, what each one needs (binaries, Python imports,
hardware), and a readiness state on a truthful ladder — so an operator can see the
**whole option space at a glance** and know exactly how ready each item is, instead
of discovering a capability exists (or doesn't really work yet) by accident.

It deliberately encodes the honest status of each feature, including the ones that
are still gated plans rather than wired runners (sub-GHz TX, IR blast, enterprise
EAP). Nothing here is marked ready because a module imports — readiness is computed
live from real probes (binary on PATH, import resolvable, hardware attached), on top
of a static `implemented` flag that says whether redux has a real code path at all.

The readiness ladder (what the device can tell you without a human attesting a
bench-test):

    PLANNED        — redux has no real runner yet; it's a plan/stub. Installing
                     tools will NOT make it work. (e.g. enterprise EAP)
    TOOL_MISSING   — implemented, but a required binary/import is absent.
                     One command fixes it: `redux toolbox install <x>`.
    NEEDS_HARDWARE — implemented and the software side is ready; attach the device.
    READY          — implemented, deps present, and either needs no hardware or the
                     hardware is confirmed attached.

A fifth rung — *hardware-qualified* (proven on a real bench) — is deliberately NOT
auto-set here: only an operator (or benchpass) can attest it. The manifest surfaces
the software truth and stops there rather than claiming a green bench it can't see.

Links, not duplication: each feature points at its toolbox tool (for the exact
install command) and its guidance topic (for the how-to), so this stays a spine over
the catalogs that already exist, never a third copy of them.

Pure/testable: the catalog is data; readiness takes injected `which` / import /
hardware probes, so it is verified with no binaries and no radio.
"""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

CATEGORIES = ("wifi", "net", "sdr", "rf", "bt", "gps", "power", "sense", "mesh", "blue", "stealth", "storage")


class Readiness(str, Enum):
    READY = "ready"
    NEEDS_HARDWARE = "needs-hardware"
    TOOL_MISSING = "tool-missing"
    PLANNED = "planned"


# glyphs used by the CLI (and anywhere else that renders a manifest row)
GLYPH = {
    Readiness.READY: "✓",
    Readiness.NEEDS_HARDWARE: "◐",
    Readiness.TOOL_MISSING: "○",
    Readiness.PLANNED: "·",
}


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    summary: str
    category: str
    implemented: bool                 # redux has a REAL code path (not a stub/plan)
    needs_hardware: str = ""          # device it needs ("" = none); gates READY
    power_tier: bool = False          # LAB-only, per-action confirm (RF TX / IR / HID)
    cap: str = ""                     # linked capabilities.py Cap id, if any
    binaries: Tuple[str, ...] = ()    # binaries that must be on PATH
    imports: Tuple[str, ...] = ()     # Python modules it imports
    toolbox: str = ""                 # linked toolbox tool name (install command)
    guidance: str = ""                # linked guidance HelpTopic id (how-to)
    note: str = ""                    # honest maturity caveat
    related: Tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "summary": self.summary,
                "category": self.category, "implemented": self.implemented,
                "needs_hardware": self.needs_hardware, "power_tier": self.power_tier,
                "cap": self.cap, "binaries": list(self.binaries), "imports": list(self.imports),
                "toolbox": self.toolbox, "guidance": self.guidance, "note": self.note,
                "related": list(self.related)}


# The catalog. Honest by construction: `implemented` is True only where redux has a
# real runner/gated-plan path; the gated-plan-without-a-wired-backend features are
# marked implemented=False (PLANNED) so no install can make them look ready.
FEATURES: Tuple[Feature, ...] = (
    Feature("wifi.capture", "Wi-Fi handshake capture",
            "bettercap (always) + AngryOxide scalpel (when present), Scope-aimed.",
            "wifi", implemented=True, needs_hardware="a monitor/injection Wi-Fi radio",
            cap="capture.handshake", binaries=("bettercap",),
            toolbox="angryoxide", guidance="capture",
            note="bettercap is the baseline; AngryOxide adds validated 22000 when installed",
            related=("wifi.crack", "wifi.wps")),
    Feature("wifi.crack", "WPA / hash cracking",
            "convert captures and crack them (hashcat engine, aircrack alt).",
            "wifi", implemented=True, binaries=("hashcat", "hcxpcapngtool"),
            toolbox="hashcat", guidance="crack", related=("wifi.capture", "wifi.pmkid", "net.recon")),
    Feature("wifi.pmkid", "Passive PMKID sweep",
            "zero-transmit loot: harvest PMKIDs APs volunteer, correlate to scope + "
            "captured-list + crackability + forge, rank the new loot.",
            "wifi", implemented=True, needs_hardware="a monitor-mode Wi-Fi radio",
            guidance="crack",
            note="live harvest needs a monitor radio; offline correlation of a .22000 "
                 "the engine already wrote needs no hardware (redux pmkid sweep --from-22000)",
            related=("wifi.capture", "wifi.crack")),
    Feature("wifi.wps", "WPS / Pixie-Dust",
            "wash scan → reaver/bully + pixiewps against an armed WPS AP.",
            "wifi", implemented=True, needs_hardware="a monitor/injection Wi-Fi radio",
            binaries=("reaver",), toolbox="reaver", guidance="wps"),
    Feature("wifi.karma", "Karma / probe harvesting",
            "harvest preferred-network lists; advertise only to in-scope clients.",
            "wifi", implemented=True, needs_hardware="a monitor/injection Wi-Fi radio",
            guidance="karma"),
    Feature("rf.dvr", "Airwave DVR",
            "append-only RF timeline — scrub back to any moment, snapshot the air, "
            "replay a window, track one BSSID's presence over time.",
            "wifi", implemented=True,
            note="the engine is pure (verified with no radio); it records whatever the "
                 "tap/engine/sweep feeds it and persists on /captures",
            related=("wifi.pmkid", "wifi.dna")),
    Feature("net.recon", "Network kill-chain",
            "scan → scope-gated cred-test → loot over an authorized network.",
            "net", implemented=True, binaries=("nmap", "hydra"),
            toolbox="nmap", guidance="netrecon",
            note="real nmap + hydra runners; needs access to a network in Scope",
            related=("net.responder", "wifi.crack")),
    Feature("net.responder", "NetNTLM hash harvest",
            "on the joined LAN, poison LLMNR/NBT-NS/mDNS to harvest NetNTLMv1/v2 "
            "hashes → the crack queue (hashcat -m 5500/5600).",
            "net", implemented=True, binaries=("responder",), toolbox="responder",
            note="firing path: FIELD needs the LAN armed + authorization, LAB unleashed; "
                 "the parser is pure (verified with no tool); analyze-only mode is passive",
            related=("net.recon", "wifi.crack")),
    Feature("sdr.rx", "SDR receive / decode",
            "spectrum/waterfall + ISM/ADS-B/POCSAG decoders from an RTL-SDR.",
            "sdr", implemented=True, needs_hardware="an RTL-SDR dongle",
            cap="sdr.rx", binaries=("rtl_power", "rtl_433"),
            toolbox="rtl-sdr", guidance="sdr"),
    Feature("subghz.replay", "Sub-GHz replay (TX)",
            "capture + catalog a sub-GHz signal, then replay it.",
            "rf", implemented=False, needs_hardware="a TX-capable SDR (HackRF) or rpitx",
            power_tier=True, toolbox="hackrf",
            note="capture & catalog work today; TX replay is a gated PLAN — the "
                 "HackRF/rpitx transmit backend is not wired as a runner yet",
            related=("sdr.rx",)),
    Feature("ir.blast", "IR blast / replay",
            "TV-B-Gone-style IR, as a gated plan through LIRC.",
            "rf", implemented=False, needs_hardware="an IR LED on a free GPIO (+ LIRC)",
            power_tier=True, binaries=("irsend",), toolbox="lirc",
            note="builds a gated irsend plan; needs an IR LED wired + a LIRC remote "
                 "config before it can actually transmit"),
    Feature("ble.scan", "Bluetooth / BLE scan",
            "discover and inventory nearby BLE devices via BlueZ.",
            "bt", implemented=True, needs_hardware="a Bluetooth adapter",
            cap="ble.scan", binaries=("bluetoothctl",), toolbox="bluez"),
    Feature("gps.position", "GPS position / geo-tagging",
            "serial-NMEA or gpsd position feeding sighting geo-tags.",
            "gps", implemented=True, needs_hardware="a USB/serial GPS",
            cap="location.position", imports=("serial",), toolbox="gpsd",
            guidance="peripherals"),
    Feature("power.battery", "UPS / battery telemetry",
            "read charge/health from a supported UPS HAT over I²C.",
            "power", implemented=True, needs_hardware="a supported UPS/battery HAT",
            cap="power.battery", imports=("smbus2",),
            note="the geekworm boot power path uses smbus; the richer probes use smbus2"),
    Feature("storage.persist", "Persistent capture storage",
            "the REDUXCAP /captures partition that survives the ephemeral root.",
            "storage", implemented=True, cap="storage.persist",
            note="captures/jobs/results live here; the root filesystem is overlay-ephemeral"),
    Feature("sense.csi", "CSI motion sensing",
            "use nexmon CSI as a presence/motion sensor (the radio as a sensor).",
            "sense", implemented=True, needs_hardware="nexmon CSI firmware on the onboard radio",
            note="the CSI parser + engine are real and tested; the live firmware→data "
                 "path still needs hardware qualification"),
    Feature("mesh.swarm", "Mesh / swarm scope-sync",
            "authenticated scope/task sharing across cooperating nodes.",
            "mesh", implemented=True, needs_hardware="a LoRa radio for off-net transport",
            guidance="mesh",
            note="message signing + scope-sync logic are real; a real LoRa transport "
                 "(Meshtastic) is not wired yet — it currently runs over loopback"),
    Feature("mesh.correlate", "Swarm time-correlation",
            "fuse nodes in TIME: corroborate an event across nodes, track an entity "
            "moving node→node, merge a swarm-wide timeline (clock-skew corrected).",
            "mesh", implemented=True,
            note="engine is pure (verified with no radio); pairs with the spatial "
                 "collector-grid fusion and rides whatever transport the fleet uses",
            related=("mesh.swarm", "wifi.dna", "rf.dvr")),
    Feature("blue.detect", "Wireless detectors (blue)",
            "the always-on detector suite — deauth floods, rogue APs, karma, etc.",
            "blue", implemented=True, needs_hardware="a Wi-Fi radio in monitor mode",
            note="core; findings stay tied to real observations"),
    Feature("blue.tracker", "Unwanted-tracker check",
            "is a BLE tracker following me? — the personal-safety sweep.",
            "blue", implemented=True, needs_hardware="a Bluetooth adapter",
            guidance="tracker"),
    Feature("wifi.dna", "Device DNA (behavioral re-id)",
            "link a device across MAC rotation by HOW it probes (timing cadence, "
            "supported-rate set, channels, IE template) — works even with no PNL.",
            "wifi", implemented=True, needs_hardware="a monitor-mode Wi-Fi radio",
            note="similarity-based re-id that extends the exact-match fingerprint; "
                 "the engine is pure (verified with no radio), live traces need the tap",
            related=("blue.tracker",)),
    Feature("eap.enterprise", "Enterprise EAP assessment",
            "hostapd-mana/-wpe enterprise client-behavior assessment.",
            "wifi", implemented=False, needs_hardware="a monitor/injection Wi-Fi radio",
            note="PLANNED — a plan builder exists, but it is not yet a validated hostapd "
                 "integration (no AP is actually launched/qualified)"),
    Feature("stealth.quiet", "Quiet/Ghost mode",
            "one-toggle stealth posture: passive-first capture, frame budget, "
            "per-action confirm, MAC rotation — never loosens a gate, only tightens.",
            "stealth", implemented=True, guidance="crack",
            note="software posture; the MAC-rotate part needs a Wi-Fi radio",
            related=("stealth.footprint", "stealth.lowslow", "stealth.mac", "wifi.pmkid")),
    Feature("stealth.footprint", "Footprint / noise meter",
            "live 0–100 detectability score from emitted frames (silent→loud).",
            "stealth", implemented=True,
            related=("stealth.quiet", "stealth.lowslow")),
    Feature("stealth.lowslow", "Low-and-slow scheduler",
            "pace offensive emissions under a footprint cap with jittered spacing "
            "so neither the volume nor the cadence is a signature.",
            "stealth", implemented=True, guidance="crack",
            related=("stealth.quiet", "stealth.footprint")),
    Feature("stealth.mac", "Own-radio MAC randomization",
            "rotate the device's own MAC (vendor-plausible or LAA) so it leaves no "
            "stable fingerprint.",
            "stealth", implemented=True, needs_hardware="a Wi-Fi radio",
            related=("stealth.quiet",)),
    Feature("stealth.scrub", "Secure-wipe / panic-scrub",
            "wipe the device's OWN sensitive loot/captures/creds on demand — scrub "
            "keeps the brain (experience/wordlists/graph), panic burns it all but "
            "leaves the box bootable. Dry-run by default; own-device only.",
            "stealth", implemented=True,
            note="best-effort overwrite+unlink; honest that FDE is the real guarantee on flash. "
                 "Never target anti-forensics — refuses any root outside the captures tree",
            related=("stealth.quiet",)),
)

_BY_ID: Dict[str, Feature] = {f.id: f for f in FEATURES}


def list_features() -> List[Feature]:
    return list(FEATURES)


def get(feature_id: str) -> Optional[Feature]:
    return _BY_ID.get((feature_id or "").strip().lower())


def _default_has_import(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def missing_deps(feature: Feature, *, which: Callable[[str], Optional[str]] = shutil.which,
                 has_import: Callable[[str], bool] = _default_has_import) -> List[str]:
    """The required binaries/imports that are absent. Empty when the software side
    is fully present."""
    out = [b for b in feature.binaries if which(b) is None]
    out += [f"py:{m}" for m in feature.imports if not has_import(m)]
    return out


def readiness(feature: Feature, *,
              which: Callable[[str], Optional[str]] = shutil.which,
              has_import: Callable[[str], bool] = _default_has_import,
              hardware_present: Callable[[Feature], bool] = lambda f: False
              ) -> Tuple[Readiness, str]:
    """Compute a feature's readiness state + a glass-box reason, from live probes.

    `hardware_present` defaults to False (unconfirmed): the manifest does not guess
    at attached hardware, so a software-ready feature that needs a device reports
    NEEDS_HARDWARE ("attach it") rather than a hardware claim it can't verify. An
    on-device caller can inject a real probe to promote those to READY.
    """
    if not feature.implemented:
        return Readiness.PLANNED, (feature.note or "not implemented yet — a plan/stub")
    missing = missing_deps(feature, which=which, has_import=has_import)
    if missing:
        hint = f"run `redux toolbox install {feature.toolbox}`" if feature.toolbox else "install the missing tool(s)"
        return Readiness.TOOL_MISSING, f"missing {', '.join(missing)} — {hint}"
    if feature.needs_hardware and not hardware_present(feature):
        return Readiness.NEEDS_HARDWARE, f"software ready — attach {feature.needs_hardware}"
    return Readiness.READY, "ready"


def readiness_record(feature: Feature, **probes) -> dict:
    state, reason = readiness(feature, **probes)
    return {**feature.to_dict(), "state": state.value, "reason": reason,
            "missing": missing_deps(feature,
                                    which=probes.get("which", shutil.which),
                                    has_import=probes.get("has_import", _default_has_import))}


def completion(features: Optional[List[Feature]] = None, **probes) -> dict:
    """An evidence-based completion picture — counts by readiness state and by
    category, plus honest tallies. No subjective percent-done: a planner is never
    counted as ready, and software-ready-pending-hardware is its own bucket."""
    feats = features if features is not None else list(FEATURES)
    by_state: Dict[str, int] = {r.value: 0 for r in Readiness}
    by_category: Dict[str, Dict[str, int]] = {}
    for f in feats:
        state, _ = readiness(f, **probes)
        by_state[state.value] += 1
        cat = by_category.setdefault(f.category, {r.value: 0 for r in Readiness})
        cat[state.value] += 1
    implemented = sum(1 for f in feats if f.implemented)
    return {
        "total": len(feats),
        "implemented": implemented,
        "planned": len(feats) - implemented,
        "by_state": by_state,
        "by_category": by_category,
        "summary": (f"{by_state[Readiness.READY.value]} ready, "
                    f"{by_state[Readiness.NEEDS_HARDWARE.value]} need hardware, "
                    f"{by_state[Readiness.TOOL_MISSING.value]} need a tool install, "
                    f"{by_state[Readiness.PLANNED.value]} still planned "
                    f"(of {len(feats)} capabilities)"),
    }
