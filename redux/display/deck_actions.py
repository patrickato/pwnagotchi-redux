"""The control-deck action catalog — what each tappable action needs, and where
its pick-list choices come from.

The TFT is a small resistive screen: typing is painful, so the deck fills a
command's blanks from *pick-lists* pre-populated with the most-likely candidates
(scope targets, wordlists, device types, interfaces…), sourced from real device
state. The operator taps a choice; the machine assembles the exact `redux`
command. A genuine free-text field (rare) is the only thing that falls back to
the on-screen keyboard.

This module is pure data + a candidate resolver. It knows nothing about pixels or
touch; deck_model drives it and deck_render draws it. Kept honest by tests that
assert every action id is a real arsenal command and every param maps to a real
candidate source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# --- candidate sources ------------------------------------------------------- #
# A param names a SOURCE; the resolver turns that source + live DeckState into a
# concrete list the operator picks from. "static:a|b|c" is an inline fixed list.

@dataclass
class DeckState:
    """Live choices the deck offers, injected from real device state (scope store,
    wordlist store, recon, radios). Defaults are empty so the deck is honest on a
    fresh box — a param with no candidates shows 'none yet', never a fake value."""
    scope_targets: Tuple[str, ...] = ()      # armed/known BSSIDs + SSIDs you own
    hosts: Tuple[str, ...] = ()               # discovered LAN hosts (IPs)
    subnets: Tuple[str, ...] = ()             # armed CIDRs
    wordlists: Tuple[str, ...] = ()           # registered wordlist names
    captures: Tuple[str, ...] = ()            # capture files ready to crack
    hash_modes: Tuple[str, ...] = ()          # hashcat -m recipes ("22000=WPA-PBKDF2", ...)
    rules: Tuple[str, ...] = ()               # rule files ("best64", "dive", ...)
    device_categories: Tuple[str, ...] = ()   # router/camera/doorbell/...
    vendors: Tuple[str, ...] = ()             # tp-link/hikvision/...
    interfaces: Tuple[str, ...] = ()          # wlan0/wlan1
    personas: Tuple[str, ...] = ()
    mode_lab: bool = False


def choice_value(choice: str) -> str:
    """A candidate may be 'value=label' (CLI value + friendly label). The command
    uses the value; the screen shows the label."""
    return choice.split("=", 1)[0] if "=" in choice else choice


def choice_label(choice: str) -> str:
    return choice.split("=", 1)[1] if "=" in choice else choice


_STATIC_PREFIX = "static:"


def candidates_for(source: str, state: DeckState) -> List[str]:
    """Resolve a param's candidate list from its source key + live state."""
    if source.startswith(_STATIC_PREFIX):
        return [s for s in source[len(_STATIC_PREFIX):].split("|") if s]
    values = getattr(state, source, ()) or ()
    # de-dupe, keep order, bound the list so a popup never runs off the screen
    seen: Dict[str, None] = {}
    for v in values:
        if isinstance(v, str) and v and v not in seen:
            seen[v] = None
    return list(seen)[:64]


# --- parameter + action specs ------------------------------------------------ #

@dataclass(frozen=True)
class Param:
    name: str                 # internal key
    label: str                # shown on the deck ("Target", "Wordlist")
    source: str               # candidate source key, or "static:a|b|c", or "free_text"
    flag: str = ""            # CLI flag it fills ("" = positional argument)
    required: bool = True
    default: str = ""         # pre-selected choice when present (the auto pick)
    free_text: bool = False   # no pick-list → on-screen keyboard fallback
    multi: bool = False       # choose all that apply (e.g. which capture files)
    auto: str = ""            # what the auto-pipeline would use, shown when unset

    @property
    def is_free_text(self) -> bool:
        return self.free_text or self.source == "free_text"


@dataclass(frozen=True)
class Action:
    id: str                   # the arsenal command, e.g. "crack run"
    title: str                # short label for the row
    group: str                # arsenal group it belongs to
    params: Tuple[Param, ...] = ()
    power_tier: bool = False   # transmits / fires / writes → per-action confirm
    lab_only: bool = False     # refuses outside LAB (confirm explains)
    note: str = ""

    def ready(self, chosen: Dict[str, str]) -> bool:
        """True when every required param has a value chosen."""
        return all((not p.required) or chosen.get(p.name) for p in self.params)


# The catalog. Only actions that take choices need an entry; everything else in
# the arsenal is a zero-param RUN (status/reference commands) and the deck offers
# it with a plain RUN button. Every id below is validated against the arsenal.
_CATALOG: Tuple[Action, ...] = (
    # --- RECON & IDENTITY ---
    Action("survey", "Passive survey", "RECON & IDENTITY",
           note="zero-transmit airspace survey — APs/clients in earshot"),
    Action("identify", "Identify a device", "RECON & IDENTITY",
           params=(Param("target", "Target", "scope_targets", required=False),),
           note="what IS it + what you can do to it"),
    Action("discover", "LAN discovery", "RECON & IDENTITY",
           params=(Param("subnet", "Subnet", "subnets", flag="--subnet", required=False),),
           note="label every host on the subnet"),
    Action("dossier", "AP dossier", "RECON & IDENTITY",
           params=(Param("target", "AP", "scope_targets"),),
           note="everything known about one AP + the move"),
    Action("gatt", "BLE GATT", "RECON & IDENTITY",
           params=(Param("mode", "Mode", "static:read|write-surface", flag="",
                         default="read"),),
           note="read device-info / map writable control points"),
    # --- CAPTURE & CRACK ---
    Action("targets", "Rank targets", "CAPTURE & CRACK",
           note="rank APs by crackability × value × signal"),
    Action("pmkid sweep", "PMKID sweep", "CAPTURE & CRACK",
           note="zero-transmit PMKID loot — harvest + rank"),
    Action("crack run", "Crack captures", "CAPTURE & CRACK",
           params=(
               Param("captures", "Capture files", "captures", flag="--capture",
                     required=False, multi=True, auto="all ready"),
               Param("type", "Hash type (-m)", "hash_modes", flag="--type",
                     required=False, auto="auto-detect"),
               Param("attack", "Attack (-a)",
                     "static:0=0 · wordlist|3=3 · mask|6=6 · wordlist+mask|7=7 · mask+wordlist",
                     flag="--attack", required=False, auto="0 · wordlist"),
               Param("wordlist", "Wordlist", "wordlists", flag="--wordlist",
                     required=False, auto="selected"),
               Param("rules", "Rules", "rules", flag="--rules",
                     required=False, auto="none"),
           ),
           note="hashcat — every field auto-fills like the pipeline; override any"),
    Action("wps", "WPS / Pixie", "CAPTURE & CRACK",
           params=(Param("target", "Target AP", "scope_targets"),),
           power_tier=True, note="active WPS attack against an armed AP"),
    # --- LOOT & PIVOT ---
    Action("loot defaults", "Default creds", "LOOT & PIVOT",
           params=(Param("category", "Device type", "device_categories",
                         flag="--category", required=False),),
           note="well-known default creds for a device type"),
    Action("loot pivot", "Pivot a subnet", "LOOT & PIVOT",
           params=(Param("subnet", "Subnet", "subnets", flag="--subnet", required=False),
                   Param("category", "Device type", "device_categories",
                         flag="--device-category", required=False)),
           power_tier=True, note="recon + credential reuse into a subnet"),
    Action("loot reuse", "Cred reuse", "LOOT & PIVOT",
           params=(Param("subnet", "Subnet", "subnets", flag="--subnet"),),
           power_tier=True, note="one password's blast radius across hosts"),
    Action("loot files", "Pull files (SMB)", "LOOT & PIVOT",
           params=(Param("host", "Host", "hosts"),),
           power_tier=True, note="pull interesting files off a host"),
    Action("campaign unleashed", "Kill-chain", "LOOT & PIVOT",
           params=(Param("target", "Targets", "scope_targets", required=False),),
           power_tier=True, lab_only=True,
           note="autonomous capture→crack→loot over armed targets"),
    # --- STEALTH & OPSEC ---
    Action("stealth quiet on", "Go dark", "STEALTH & OPSEC",
           note="passive-first, frame budget, MAC rotate"),
    Action("stealth mac", "Rotate MAC", "STEALTH & OPSEC",
           note="randomize the radio's own MAC"),
    Action("shadow", "Blue-team view", "STEALTH & OPSEC",
           note="what would a blue team see from this?"),
    # --- CONFIG & SYSTEM ---
    Action("mode", "FIELD / LAB", "CONFIG & SYSTEM",
           params=(Param("state", "Posture", "static:lab|field|status", default="status"),),
           note="switch operating posture"),
    Action("web", "Serve dashboard", "CONFIG & SYSTEM",
           note="serve the web UI to your phone"),
    Action("provision", "First-boot setup", "CONFIG & SYSTEM",
           note="/captures skeleton + starter wordlist (crack-ready)"),
)

ACTIONS: Dict[str, Action] = {a.id: a for a in _CATALOG}


def action_for(command: str) -> Optional[Action]:
    return ACTIONS.get(command)


def power_tier_ids() -> Tuple[str, ...]:
    return tuple(a.id for a in _CATALOG if a.power_tier)
