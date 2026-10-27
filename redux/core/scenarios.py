"""Scenario library — the wave, tied together into runnable playbooks.

Every capability Augur ships is a tool; a scenario is the recipe that chains them
toward an outcome. These are three things at once:

  - tutorials  — read a scenario to learn the flow and the exact commands
  - a map      — each step names the capability (a manifest feature id) it uses,
                 so "what fits with what" is explicit, not folklore
  - a readiness check — cross-referenced against the capability manifest, a
                 scenario reports whether it's RUNNABLE on THIS box right now, or
                 which step needs a radio / a tool install first

That last part is the A+B=C: A (the scenarios) + B (the manifest readiness ladder)
⇒ C (an executable-aware playbook that tells you, before you start, exactly what's
blocking it). Pure/deterministic; validates against the real manifest, no hardware.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import manifest as M


@dataclass(frozen=True)
class Step:
    title: str
    capability: str          # a manifest feature id this step exercises
    command: str             # the concrete redux CLI to run it
    why: str                 # what this step contributes to the chain


@dataclass(frozen=True)
class Scenario:
    name: str
    goal: str
    teaches: str
    posture: str             # "silent" | "quiet" | "loud" — the emissions profile
    steps: Tuple[Step, ...]

    @property
    def capabilities(self) -> Tuple[str, ...]:
        return tuple(s.capability for s in self.steps)


# The playbooks. Each step's `capability` is a real manifest feature id, so the
# validator can prove the chain is wired and tell you what's ready.
SCENARIOS: Tuple[Scenario, ...] = (
    Scenario(
        name="silent-loot",
        goal="recover a WPA2 key without transmitting a single frame",
        teaches="how passive harvest + environment-grown wordlists beat a generic list",
        posture="silent",
        steps=(
            Step("Harvest PMKIDs passively", "wifi.pmkid",
                 "redux pmkid sweep --from-22000 /captures/<file>.22000",
                 "APs volunteer crackable hashes; nothing is sent, nothing is logged by a WIDS"),
            Step("Grow a wordlist from the environment", "wifi.crack",
                 "redux forge --environment --loot-dir /captures/loot/files",
                 "seeds from SSIDs/PNLs/looted strings — tuned to THIS place, run before rockyou"),
            Step("Crack the harvested hash", "wifi.crack",
                 "redux crack run",
                 "the targeted list cracks the PMKID the sweep found — all zero-transmit"),
        ),
    ),
    Scenario(
        name="lan-pivot",
        goal="turn one cracked Wi-Fi key into LAN loot and more credentials",
        teaches="the full post-auth kill-chain: join → harvest → loot → triage",
        posture="loud",
        steps=(
            Step("Capture + crack the handshake", "wifi.capture",
                 "redux capture plan ; redux crack run", "recover the PSK for the target AP"),
            Step("Join the LAN", "net.recon", "redux campaign unleashed",
                 "the recovered PSK gets us an IP + the subnet to work"),
            Step("Harvest NetNTLM hashes", "net.responder",
                 "redux responder <iface> harvest --lab",
                 "poison name resolution so hosts authenticate to us → more crackable hashes"),
            Step("Pull interesting files", "net.recon",
                 "redux loot files <host> --user <u> --pw <p> --triage",
                 "SMB shares off credited hosts → forensic triage for secrets"),
        ),
    ),
    Scenario(
        name="ghost-recon",
        goal="map everything in earshot while staying effectively invisible",
        teaches="stacking the stealth layer: quiet posture + paced emissions + passive id",
        posture="quiet",
        steps=(
            Step("Go dark", "stealth.quiet", "redux stealth quiet on",
                 "passive-first, frame budget, MAC rotation — one toggle"),
            Step("Pace anything that must transmit", "stealth.lowslow",
                 "redux stealth pace --count 5 --quiet",
                 "spread emissions under a footprint cap, jittered so the cadence isn't a signature"),
            Step("Re-identify devices past MAC rotation", "wifi.dna",
                 "redux dex --dna", "link a device by HOW it probes, no active prompting"),
            Step("Rewind the airwaves", "rf.dvr", "redux airwave timeline",
                 "scrub back through everything that was on the air, after the fact"),
        ),
    ),
    Scenario(
        name="counter-surveillance",
        goal="find out if a device is following you",
        teaches="the same re-id tech, pointed defensively",
        posture="silent",
        steps=(
            Step("Watch for a BLE tag trailing you", "blue.tracker", "redux ...tracker check",
                 "a tracker that reappears across locations is the tell"),
            Step("Link a Wi-Fi device across its MAC rotation", "wifi.dna", "redux dex --dna",
                 "a follower that rotates MAC still probes the same way"),
        ),
    ),
    Scenario(
        name="clean-exit",
        goal="leave with the loot gone but the lessons kept",
        teaches="operator OPSEC — surgical scrub vs full burn",
        posture="silent",
        steps=(
            Step("Check your exposure", "stealth.scrub", "redux scrub status",
                 "see exactly what sensitive data is on the box"),
            Step("Surgical scrub", "stealth.scrub", "redux scrub run --apply",
                 "wipe raw loot/captures/creds; KEEP the brain so the box stays smarter"),
        ),
    ),
)

_BY_NAME = {s.name: s for s in SCENARIOS}


def list_scenarios() -> List[Scenario]:
    return list(SCENARIOS)


def get(name: str) -> Optional[Scenario]:
    return _BY_NAME.get((name or "").strip().lower())


def validate(scenario: Scenario, **probes) -> dict:
    """Cross-reference every step against the manifest: is the capability real, and
    is it runnable on this box right now? Returns a glass-box readiness report."""
    rows = []
    unknown, ready, blocked = 0, 0, 0
    for step in scenario.steps:
        feat = M.get(step.capability)
        if feat is None:
            unknown += 1
            rows.append({"step": step.title, "capability": step.capability,
                         "state": "unknown", "reason": "no such capability in the manifest"})
            continue
        state, reason = M.readiness(feat, **probes)
        rows.append({"step": step.title, "capability": step.capability,
                     "state": state.value, "reason": reason})
        if state is M.Readiness.READY:
            ready += 1
        else:
            blocked += 1
    runnable = unknown == 0 and blocked == 0
    return {
        "scenario": scenario.name, "goal": scenario.goal, "posture": scenario.posture,
        "steps": rows, "ready": ready, "blocked": blocked, "unknown": unknown,
        "runnable": runnable,
        "note": ("fully runnable on this box right now" if runnable
                 else f"{blocked} step(s) need hardware/tools"
                      + (f", {unknown} unknown capability" if unknown else "")),
    }
