"""Guidance registry — the one place in-product help lives.

A new user learning how Augur actually works needs more than `--help` flags and
docs/ files. This is the single, structured source of **info-bubbles** (one-line
hints a UI shows next to a control), **"when do I use this"**, and
**step-by-step how-tos** — written for a beginner. Every surface reads from here,
so the CLI (`redux help`), the TFT (a help tab / long-press bubble), and the web
dashboard (hover bubbles, a Help page) all show the *same* guidance.

**This is where tutorials and info-bubbles get added**: append a `HelpTopic`
(or extend one) and it appears everywhere at once. Content is plain, honest, and
tied to the real commands — it never promises a capability the device doesn't
have.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class HelpTopic:
    id: str                       # slug, e.g. "lab-vs-field"
    title: str                    # human title
    bubble: str                   # ONE line, for an info-bubble (<= ~140 chars)
    when: str                     # when a user would reach for this
    how: str                      # step-by-step, multi-line
    command: str = ""             # the primary CLI command, if any
    related: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "bubble": self.bubble,
                "when": self.when, "how": self.how, "command": self.command,
                "related": list(self.related)}


TOPICS: Tuple[HelpTopic, ...] = (
    HelpTopic(
        id="getting-started", title="Getting started",
        bubble="New here? Flash the image, boot, and you're in FIELD mode (safe/passive) by default.",
        when="The very first time you power the device on.",
        how=("1. Flash the image to the SD card and boot the Pi.\n"
             "2. You start in FIELD mode: passive, authorize-required, nothing transmits.\n"
             "3. Run `redux doctor` to see what the box has and what it's missing.\n"
             "4. For your own lab gear, switch to LAB (one press on the TFT, or `redux mode lab`).\n"
             "5. Explore capabilities with `redux help` — every feature has an entry here."),
        command="redux doctor", related=("lab-vs-field", "doctor", "scope")),
    HelpTopic(
        id="lab-vs-field", title="LAB vs FIELD mode",
        bubble="FIELD = passive & gated (the safe default). LAB = unlocked & automatic, for your OWN gear.",
        when="Deciding how much the device is allowed to do on its own.",
        how=("FIELD (shipped default): passive. It observes, logs, and asks for authorization\n"
             "before anything active. The lock-down for anywhere public.\n\n"
             "LAB: the full chain runs itself — recon → deauth-capture → crack — on targets your\n"
             "Scope approves on sight (your own lab). Turn it on only where you own/authorized\n"
             "everything in range (e.g. a Faraday setup).\n\n"
             "Flip it: one press on the TFT mode switch, or `redux mode lab` / `redux mode field`."),
        command="redux mode status", related=("scope", "getting-started", "safety")),
    HelpTopic(
        id="scope", title="Scope — what the device is allowed to aim at",
        bubble="Scope is the one list of targets you own/are authorized to assess. Empty by default so nothing fires unasked.",
        when="Before any active/firing action (deauth, WPS, karma, EAP).",
        how=("Scope decides WHERE the device may aim, never WHAT it can do.\n"
             "- Add a target: `redux scope add <bssid|ssid|cidr> --job <name>`\n"
             "- Import a list, or arm a whole lab at once.\n"
             "- LAB mode approves targets on sight, so you don't pre-arm your own gear.\n"
             "In FIELD, an off-scope target is refused with a glass-box reason."),
        command="redux scope", related=("lab-vs-field", "safety")),
    HelpTopic(
        id="doctor", title="Doctor — what works and what's missing",
        bubble="`redux doctor` is a glass-box self-check: every finding says what's wrong and how to fix it.",
        when="When something isn't working, or to see the device's capabilities.",
        how=("Run `redux doctor`. Each line is OK / ATTENTION / ACTION / UNKNOWN with:\n"
             "  why:  the reason (e.g. 'no monitor radio')\n"
             "  do:   the remediation (e.g. 'plug in an RTL-SDR…')\n"
             "It never auto-fixes — it tells you, you decide. It also reports which\n"
             "peripherals (SDR/BT/GPS/UPS) are actually plugged in."),
        command="redux doctor", related=("peripherals", "getting-started")),
    HelpTopic(
        id="peripherals", title="Peripherals (SDR / Bluetooth / GPS / UPS)",
        bubble="Plug a supported USB SDR, BT dongle, GPS, or UPS HAT and the device detects it — check `redux doctor`.",
        when="After plugging or unplugging hardware.",
        how=("The device probes what's present and the Doctor reports it. If something you\n"
             "plugged isn't showing: check `redux doctor` for the reason. GPS needs REDUX_GPS\n"
             "set; the UPS HAT is read over I2C. Nothing is assumed present — only what's detected."),
        command="redux doctor", related=("doctor", "sdr")),
    HelpTopic(
        id="sdr", title="SDR — listen beyond Wi-Fi",
        bubble="Pick what to listen to (pagers, aircraft, tire sensors, a band scan) and Augur builds the tuned command.",
        when="You have an RTL-SDR and want ISM/ADS-B/POCSAG/ACARS or to scan a band.",
        how=("1. `redux sdr presets` — see choices in plain language.\n"
             "2. `redux sdr tune <preset>` — get the exact tuned command (Augur sets the frequency).\n"
             "   Frequencies are region-specific for some (pagers, TPMS); the preset says so and\n"
             "   you can override with --freq.\n"
             "3. Run it, then `redux sdr ingest <file> --<fmt>` (or `redux sdr spectrum` for a scan).\n"
             "Not sure where to look? Start with the `scan` preset to see active peaks.\n"
             "Note: a live waterfall display is not built yet — spectrum is reported as peaks/data."),
        command="redux sdr presets", related=("peripherals",)),
    HelpTopic(
        id="crack", title="Cracking hashes (not just Wi-Fi)",
        bubble="`redux crack id <file>` tells you the hash type and the best way to crack it. Gated in FIELD, turnkey in LAB.",
        when="You hold a hash you're authorized to crack (WPA, NTLM, shadow, etc.).",
        how=("1. `redux crack id <file>` — identifies the hash and suggests mode + rules.\n"
             "2. `redux crack plan <file> --type <slug>` — shows the exact hashcat command.\n"
             "3. `redux crack run <file> --type <slug>` — runs it (needs --authorized in FIELD).\n"
             "Pick wordlists with `redux wordlist`, and spread a big job with `redux crack-distribute`."),
        command="redux crack modes", related=("wordlists", "wps")),
    HelpTopic(
        id="wordlists", title="Wordlists",
        bubble="Register wordlists once and pick one; the crack uses your pick until you change it.",
        when="Before cracking, to choose how hard to try.",
        how=("- Add: `redux wordlist add /path/to/list.txt --tier fast|standard|thorough`\n"
             "- Pull from a mounted NAS share, or register local files.\n"
             "- Pick: `redux wordlist select --name <name>` — it sticks for every crack.\n"
             "In LAB, cracking is automatic and uses your selected list."),
        command="redux wordlist list", related=("crack",)),
    HelpTopic(
        id="wps", title="WPS / Pixie-Dust",
        bubble="Find WPS APs from a wash scan; attack a vulnerable one for the WPA key in seconds. Scope-gated.",
        when="A target AP has WPS enabled and you're authorized to test it.",
        how=("1. `redux wps scan <wash-output>` — list WPS APs (locked ones are flagged).\n"
             "2. `redux wps plan --bssid <b> --channel <n>` — see the exact reaver/bully command.\n"
             "3. `redux wps run --bssid <b> --channel <n>` — needs --authorized in FIELD; LAB is turnkey.\n"
             "Needs reaver or bully installed; `plan` tells you if it's missing."),
        command="redux wps scan", related=("scope", "crack", "lab-vs-field")),
    HelpTopic(
        id="karma", title="Karma / probe harvesting",
        bubble="See what networks nearby devices are looking for; advertise one — but only to IN-SCOPE clients, never bystanders.",
        when="Assessing clients you're authorized to test.",
        how=("- `redux karma clients` / `redux karma ssids` — harvested preferred-network lists (passive).\n"
             "- `redux karma plan` — the AIMED advertise plan: only SSIDs an in-scope client asked for;\n"
             "  SSIDs wanted only by strangers are refused ('not aiming at bystanders').\n"
             "Harvesting is always allowed; advertising is gated (LAB, or in-scope + armed)."),
        command="redux karma clients", related=("scope", "lab-vs-field", "safety")),
    HelpTopic(
        id="tracker", title="Unwanted-tracker check (is it following me?)",
        bubble="Flags a device (AirTag-style) that has moved WITH you across time and distance — not a one-off or a fixed beacon.",
        when="You suspect something is following you, or as a routine safety check.",
        how=("Run `redux tracker`. It analyzes BLE observation history and flags any device seen\n"
             "persistently over time AND across multiple places spanning real distance.\n"
             "`--all` shows every device with its reason. A device with no location history is\n"
             "reported as 'can't tell', never guessed."),
        command="redux tracker", related=("oui",)),
    HelpTopic(
        id="oui", title="Vendor lookup & device classing",
        bubble="Turn a MAC into a vendor + a rough device type, and flag randomized/private MACs (phones).",
        when="Making sense of who/what is around you.",
        how=("- `redux oui lookup <mac>` — vendor name (or nothing, never guessed).\n"
             "- `redux oui classify <mac>` — vendor + device-class guess + randomized flag.\n"
             "- `redux oui import <ieee-oui.txt>` — merge the full vendor DB for complete coverage.\n"
             "Vendors also enrich the Field Dex automatically."),
        command="redux oui classify", related=("tracker",)),
    HelpTopic(
        id="learn", title="Experience / learning layer",
        bubble="Honest memory of what worked: shows capture rates per channel/hour with sample sizes, and explores untried options.",
        when="Deciding where/when to spend effort (LAB).",
        how=("- `redux learn summary` — overall success rate and outcomes.\n"
             "- `redux learn rank --dim channel --candidates 1,6,11` — ranked, with sample sizes;\n"
             "  an untried option is lifted (explore), never a fabricated number.\n"
             "It only learns in LAB (FIELD fires nothing to learn from), and it advises — it\n"
             "never silently overrides the proven behavior."),
        command="redux learn summary", related=("lab-vs-field",)),
    HelpTopic(
        id="push", title="Push-to-phone alerts",
        bubble="Send capture/tracker/alert notifications to your phone via ntfy, Pushover, or a webhook.",
        when="You want to be told about events while away from the device.",
        how=("1. Pick a service and target: `redux push --service ntfy --target https://ntfy.sh/<topic> test`\n"
             "2. For Pushover add --token; for a webhook use its URL.\n"
             "Only a short summary is sent — never captured content. Secrets aren't stored."),
        command="redux push test", related=()),
    HelpTopic(
        id="mesh", title="Mesh / swarm",
        bubble="Several redux nodes can share scope and split work — including splitting one crack job across devices.",
        when="You run more than one device together.",
        how=("- Scope can sync across signed nodes.\n"
             "- `redux crack-distribute` splits one hashcat job into disjoint per-node slices.\n"
             "Every node re-checks its own local Scope before acting — placement never grants authorization."),
        command="redux crack-distribute", related=("crack", "scope")),
    HelpTopic(
        id="safety", title="The rules (how Augur stays authorized)",
        bubble="Scoped, authorized, gated by default. Full power inside your Scope; the handful that hit bystanders stay off.",
        when="Always — this is the contract.",
        how=("- Inside your Scope, nothing is held back. Scope decides WHERE, never WHAT.\n"
             "- FIELD is passive/gated by default; LAB unlocks the automatic chain for your own gear.\n"
             "- The only detection-only items are those that can't be aimed and hit bystanders:\n"
             "  indiscriminate beacon/BLE spam and RF jamming (also illegal to transmit).\n"
             "- Every decision carries a human-readable reason. Real data only."),
        command="", related=("scope", "lab-vs-field")),
)

_BY_ID = {t.id: t for t in TOPICS}


def list_topics() -> List[HelpTopic]:
    return list(TOPICS)


def get(topic_id: str) -> Optional[HelpTopic]:
    return _BY_ID.get((topic_id or "").strip().lower())


def bubble(topic_id: str) -> str:
    """The info-bubble one-liner for a topic (''/empty if unknown)."""
    t = get(topic_id)
    return t.bubble if t else ""


def search(query: str) -> List[HelpTopic]:
    """Topics whose id/title/bubble/when match the query (substring, case-insensitive)."""
    q = (query or "").strip().lower()
    if not q:
        return list(TOPICS)
    out = []
    for t in TOPICS:
        hay = f"{t.id} {t.title} {t.bubble} {t.when} {t.command}".lower()
        if q in hay:
            out.append(t)
    return out
