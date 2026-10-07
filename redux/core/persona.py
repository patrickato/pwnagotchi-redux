"""Personas — one box, pick your hat.

A persona is a *declarative* capability/posture set. Because redux's features are
already expressed as capabilities (the capability graph) and config, a persona is
just a saved selection over them: which intent to run, which detectors to watch,
whether firing-capable offense is available at all, and how exposed the web/servers
bind by default. `redux persona apply blue` reconfigures the whole box in one
gesture — same hardware, a different product.

Two things stay true no matter the persona, by design:
  - **Scope still decides WHERE.** A persona never authorizes a target; it only
    shapes WHAT the box is set up to do. Offense is still gated at the aiming layer
    by the central Scope.
  - **Posture is an *extra* gate, never a looser one.** A `detection-only` persona
    (blue) disables firing regardless of Scope — belt and suspenders — it can only
    make the box *more* conservative than Scope, never less. So switching personas
    can tighten, never widen, what can fire.

This is the "unfilter, not unaim" line in code: personas remove *friction*
(one gesture to set up for a job) without touching the aiming requirement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Posture(str, Enum):
    DETECTION_ONLY = "detection-only"   # nothing fires, period (blue)
    PASSIVE = "passive"                 # recon + capture, no active frames
    ACTIVE = "active"                   # full firing-capable offense — still Scope-gated (red/purple)


@dataclass(frozen=True)
class Persona:
    name: str
    summary: str
    intent: str                         # a redux.radio.Intent value
    posture: Posture
    detectors: str = "all"              # "all" | "none" | comma-separated categories/names
    bind_scope: str = "localhost"       # default exposure for any web/servers
    frameworks: tuple = ()              # informational: ATT&CK/D3FEND/PTES relevance
    reason: str = ""                    # glass-box: why this persona is shaped this way

    @property
    def offense_available(self) -> bool:
        """Whether firing-capable offense is even on the table for this persona.
        Scope still has the final say on any specific target."""
        return self.posture is Posture.ACTIVE


# --- the built-in hats ------------------------------------------------------- #

BUILTIN: Dict[str, Persona] = {
    "recon": Persona(
        "recon", "Passive recon & mapping — the default hunt.",
        intent="recon", posture=Posture.PASSIVE, detectors="all", bind_scope="localhost",
        frameworks=("ATT&CK:Reconnaissance",),
        reason="watch everything, touch nothing — build the Dex and the map"),
    "red": Persona(
        "red", "Offensive engagement — full capability, Scope-aimed.",
        intent="hunt", posture=Posture.ACTIVE, detectors="all", bind_scope="localhost",
        frameworks=("ATT&CK:CredentialAccess", "ATT&CK:InitialAccess", "PTES"),
        reason="capture→crack + the network kill-chain, aimed by the central Scope"),
    "blue": Persona(
        "blue", "Defensive sentinel — a wireless IDS, nothing fires.",
        intent="survey", posture=Posture.DETECTION_ONLY, detectors="all", bind_scope="lan",
        frameworks=("D3FEND", "NIST-800-115"),
        reason="deploy and watch for deauth/rogue-AP/evil-twin/karma/spam/jamming/trackers — offense hard-off"),
    "purple": Persona(
        "purple", "Attack *and* grade your own detectors in one box.",
        intent="hunt", posture=Posture.ACTIVE, detectors="all", bind_scope="localhost",
        frameworks=("ATT&CK", "D3FEND"),
        reason="run a Scope-aimed attack and watch which detections fire — ATT&CK vs D3FEND side by side"),
    "mesh": Persona(
        "mesh", "Off-grid comms & swarm coordination (LoRa/Meshtastic/MeshCore).",
        intent="recon", posture=Posture.PASSIVE, detectors="none", bind_scope="tailscale",
        frameworks=(),
        reason="be a node: heartbeat, scope-sync, and telemetry over the mesh lane"),
    "sigint": Persona(
        "sigint", "Listen to everything and map it (SDR + RF).",
        intent="survey", posture=Posture.PASSIVE, detectors="all", bind_scope="lan",
        frameworks=("ATT&CK:Reconnaissance",),
        reason="passive wide-band collection — ADS-B/AIS/ISM/BLE into the sighting store"),
}

DEFAULT_PERSONA = "recon"


def names() -> List[str]:
    return list(BUILTIN.keys())


def get(name: str) -> Persona:
    key = (name or "").strip().lower()
    if key not in BUILTIN:
        raise KeyError(f"unknown persona '{name}'; known: {', '.join(BUILTIN)}")
    return BUILTIN[key]


def summarize() -> List[dict]:
    return [
        {"name": p.name, "summary": p.summary, "intent": p.intent,
         "posture": p.posture.value, "offense_available": p.offense_available,
         "detectors": p.detectors, "bind_scope": p.bind_scope,
         "frameworks": list(p.frameworks), "reason": p.reason}
        for p in BUILTIN.values()
    ]
