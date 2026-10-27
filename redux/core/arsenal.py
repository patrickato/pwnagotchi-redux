"""The arsenal — one place that lists everything the box can do + how to run it.

The capability manifest answers "what exists and how ready is it?"; the arsenal
answers "what do I actually type to use it?". It's the operator's index: every
capability grouped, with the exact command, so nothing the project offers is
buried or forgotten — see it, find it, choose it, run it. One catalog feeds the
CLI, the TFT menu page and the web dashboard, so all three show the same toolkit.

Kept honest by a test: every command's verb is a real `redux` subcommand, so the
arsenal can't drift into listing things that don't exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Entry:
    command: str        # the exact CLI, e.g. "pmkid sweep"
    title: str          # what it does, one line
    group: str

    @property
    def verb(self) -> str:
        return self.command.split()[0]

    def to_dict(self) -> dict:
        return {"command": "redux " + self.command, "title": self.title, "group": self.group}


# The toolkit, grouped the way an operator reaches for it. Each command's verb is
# validated against the real CLI by the test suite.
_RAW: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...] = (
    ("STATUS & HEALTH", (
        ("status", "live operating snapshot — intent, capture, sightings, face"),
        ("capabilities qualified", "readiness of every capability (one truth)"),
        ("doctor", "measured device health — no green it can't prove"),
        ("web", "the dashboard for a phone/laptop browser"),
        ("simulate", "boot the whole live stack with no Pi, no radios"),
    )),
    ("RECON & IDENTITY", (
        ("survey", "passive airspace survey — APs/clients in earshot"),
        ("identify", "what IS this device (Roomba/Alexa/phone...) + what you can do to it"),
        ("discover", "LAN service discovery → labeled inventory of what every host is"),
        ("gatt", "BLE GATT loot + control — read device-info; map/write control points"),
        ("dossier", "everything known about one AP + the recommended move"),
        ("dex --dna", "devices re-identified by behavior past MAC rotation"),
        ("airwave timeline", "scrub back through RF history"),
        ("captap live", "raw-frame tap + cross-MAC re-identification"),
        ("graph", "entity graph — devices, travels-with, sightings"),
        ("hunt", "RSSI fox-hunt — walk warmer/colder toward a target"),
        ("tracker", "is a BLE tag following me? (counter-surveillance)"),
        ("gatt", "BLE GATT looting — read a device's info/battery/vendor services"),
    )),
    ("CAPTURE & CRACK", (
        ("targets", "rank APs by how worth attacking (crackability x value x signal)"),
        ("capture plan", "engine selection, Scope-aimed"),
        ("pmkid sweep", "zero-transmit PMKID loot — harvest + rank"),
        ("crack run", "convert captures and crack them (hashcat)"),
        ("forge --environment", "wordlist grown from everything the box saw"),
        ("wordlist", "wordlist management + QNAP pull"),
        ("wps", "WPS / Pixie-Dust against an armed AP"),
    )),
    ("LOOT & PIVOT", (
        ("campaign unleashed", "autonomous kill-chain over armed targets"),
        ("netrecon", "scan a Scope-authorized subnet"),
        ("loot pivot", "recon + credential reuse into a subnet"),
        ("loot reuse", "credential-reuse graph — one password's blast radius"),
        ("loot defaults", "well-known default creds for a device type (feeds pivot)"),
        ("loot files", "pull interesting files off a host (SMB) -> triage"),
        ("responder harvest", "NetNTLM hashes on the joined LAN -> crack queue"),
        ("loot triage", "forensic triage of pulled files"),
        ("loot secrets", "typed, redacted secrets scan (keys/tokens/JWT/DB-URI)"),
        ("loot report", "one rollup across creds / files / NetNTLM / secrets"),
        ("scenario list", "runnable playbooks that chain the toolkit"),
    )),
    ("STEALTH & OPSEC", (
        ("stealth quiet on", "go dark — passive-first, frame budget, MAC rotate"),
        ("stealth mac", "randomize the radio's own MAC"),
        ("stealth pace", "low-and-slow emission scheduler (jittered)"),
        ("scrub status", "secure-wipe exposure — own data, brain kept"),
        ("shadow", "what would a blue team see from this action?"),
    )),
    ("SWARM & CASE", (
        ("mesh correlate", "swarm time-correlation — corroborate + track movement"),
        ("leases demo", "radio ownership arbitration (no clobbering)"),
        ("engagement open", "the durable case file — scope/authlog/loot/report"),
    )),
    ("SDR / RF  (TX = LAB)", (
        ("sdr spectrum", "spectrum / waterfall from an RTL-SDR"),
        ("subghz", "sub-GHz capture / replay (TX is LAB-only)"),
        ("ir", "IR capture / blast (LAB-only)"),
    )),
    ("CONFIG & SYSTEM", (
        ("scope arm-lab", "authorize your whole lab in one gesture"),
        ("mode", "FIELD <-> LAB posture switch"),
        ("phone plan", "serve the dashboard to a phone over Bluetooth PAN"),
        ("toolbox list", "the curated tool catalog + install commands"),
        ("notebook", "lab session journal"),
        ("persona", "operating persona (offense posture)"),
    )),
)

ARSENAL: Tuple[Entry, ...] = tuple(
    Entry(command=cmd, title=title, group=group)
    for group, entries in _RAW for cmd, title in entries
)


def groups() -> "Dict[str, List[Entry]]":
    out: Dict[str, List[Entry]] = {}
    for e in ARSENAL:
        out.setdefault(e.group, []).append(e)
    return out


def all_entries() -> List[Entry]:
    return list(ARSENAL)


def verbs() -> set:
    """Every distinct CLI verb the arsenal references (for validation)."""
    return {e.verb for e in ARSENAL}


def search(term: str) -> List[Entry]:
    """Entries whose command or title matches a term (case-insensitive)."""
    t = (term or "").strip().lower()
    if not t:
        return list(ARSENAL)
    return [e for e in ARSENAL if t in e.command.lower() or t in e.title.lower()
            or t in e.group.lower()]


def menu_groups(max_groups: int = 8) -> List[Tuple[str, str, str]]:
    """Compact (name, line1, line2) rows for the TFT menu grid — the commands in
    each group, split across two lines. Always current with the catalog."""
    rows: List[Tuple[str, str, str]] = []
    for name, entries in list(groups().items())[:max_groups]:
        verbs_here = []
        seen = set()
        for e in entries:
            v = e.command.split()[0] if e.command.split()[0] not in ("dex",) else e.command
            # show the distinctive word (verb, or verb+sub for a couple)
            token = e.command.split()[0]
            if token in seen:
                token = " ".join(e.command.split()[:2])
            seen.add(token)
            verbs_here.append(token)
        half = (len(verbs_here) + 1) // 2
        rows.append((name, " ".join(verbs_here[:half]), " ".join(verbs_here[half:])))
    return rows
