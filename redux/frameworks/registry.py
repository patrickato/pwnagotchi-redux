"""Framework registry — redux capabilities mapped to MITRE ATT&CK and D3FEND.

This is the connective tissue that makes redux framework-aware: every offensive
action is tagged with the ATT&CK technique(s) it performs and the PTES phase it
belongs to, and every action is paired with the redux detector(s) that *should*
catch it plus the D3FEND countermeasure class. That buys three things at once:
engagement reports that speak ATT&CK/D3FEND, a teaching aid (the box names the
technique it's running), and the spine of purple Range mode.

Honesty about the mapping:
  - **ATT&CK IDs are real Enterprise technique IDs**, curated best-fit for a
    Wi-Fi/RF context. Review them against your engagement's threat model.
  - **D3FEND's wireless coverage is coarse**, so the defensive side maps to
    `D3-NTA` (Network Traffic Analysis — genuinely what the RF anomaly detectors
    do) plus a plain-language countermeasure. It's directional, and labelled so.
  - Detector names here are **references** to the detector suite, not duplicates —
    this layer never edits detectors; it maps to them by name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class TechniqueMap:
    action: str                 # redux offensive action key
    label: str                  # human name
    attack_ids: Tuple[str, ...]  # MITRE ATT&CK Enterprise technique IDs (best-fit)
    ptes_phase: str             # Penetration Testing Execution Standard phase
    detects: Tuple[str, ...]    # detector names that SHOULD catch this (suite references)
    defend: Tuple[str, ...]     # D3FEND technique IDs / countermeasure labels (best-fit)
    note: str = ""

    def to_dict(self) -> dict:
        return {"action": self.action, "label": self.label, "attack": list(self.attack_ids),
                "ptes_phase": self.ptes_phase, "detects": list(self.detects),
                "defend": list(self.defend), "note": self.note}


# Curated mapping. ATT&CK IDs are real; D3-NTA is the honest best-fit for RF
# detection analytics. `detects=()` means "nothing in the wireless suite should
# catch this" (e.g. a passive technique) — stated plainly, not hidden.
REGISTRY: Dict[str, TechniqueMap] = {
    "wifi_recon": TechniqueMap(
        "wifi_recon", "Passive Wi-Fi recon",
        ("T1595", "T1590"), "Intelligence Gathering",
        detects=(), defend=("D3-NTA",),
        note="passive discovery — little to detect by design"),
    "handshake_capture": TechniqueMap(
        "handshake_capture", "WPA handshake capture",
        ("T1040",), "Exploitation",
        detects=("handshake",), defend=("D3-NTA",),
        note="EAPOL 4-way capture; the handshake detector sees the exchange"),
    "pmkid_capture": TechniqueMap(
        "pmkid_capture", "Clientless PMKID capture",
        ("T1040",), "Exploitation",
        detects=(), defend=("D3-NTA",),
        note="clientless/assoc PMKID — low observable signal"),
    "offline_crack": TechniqueMap(
        "offline_crack", "Offline PSK cracking",
        ("T1110.002",), "Exploitation",
        detects=(), defend=("D3-SPP",),
        note="off-device; D3-SPP = Strong Password Policy is the real countermeasure"),
    "deauth": TechniqueMap(
        "deauth", "Deauthentication",
        ("T1498",), "Exploitation",
        detects=("deauth-flood", "surveillance-sweep"), defend=("D3-NTA",),
        note="forces reconnection; MFP/802.11w is the real mitigation"),
    "evil_twin": TechniqueMap(
        "evil_twin", "Rogue AP / evil twin",
        ("T1557",), "Exploitation",
        detects=("rogue-AP", "pineapple", "karma"), defend=("D3-NTA",),
        note="adversary-in-the-middle via a look-alike AP"),
    "captive_portal": TechniqueMap(
        "captive_portal", "Captive-portal credential capture",
        ("T1557", "T1056.003"), "Exploitation",
        detects=("rogue-AP", "pineapple"), defend=("D3-NTA",),
        note="portal rides on a rogue AP; authorized testing only"),
    "net_scan": TechniqueMap(
        "net_scan", "Network service discovery",
        ("T1046",), "Exploitation",
        detects=(), defend=("D3-NTA",),
        note="network-layer (post-pivot) — outside the wireless detector suite"),
    "cred_test": TechniqueMap(
        "cred_test", "Credential testing / remote login",
        ("T1110", "T1021"), "Exploitation",
        detects=(), defend=("D3-SPP",),
        note="network-layer brute/login — host IDS territory"),
    "loot": TechniqueMap(
        "loot", "Unsecured-credential collection",
        ("T1552",), "Post-Exploitation",
        detects=(), defend=("D3-FEMC",),
        note="network-layer loot; D3-FEMC = File Encryption is the real mitigation"),
}


def actions() -> List[str]:
    return list(REGISTRY.keys())


def get(action: str) -> TechniqueMap:
    key = (action or "").strip().lower()
    if key not in REGISTRY:
        raise KeyError(f"unknown action '{action}'; known: {', '.join(REGISTRY)}")
    return REGISTRY[key]


def summarize() -> List[dict]:
    return [m.to_dict() for m in REGISTRY.values()]


def attack_defend_pairs() -> List[dict]:
    """Flat ATT&CK→D3FEND pairing for every action — the teaching/report view."""
    return [
        {"action": m.action, "label": m.label, "attack": list(m.attack_ids),
         "defend": list(m.defend), "detects": list(m.detects), "phase": m.ptes_phase}
        for m in REGISTRY.values()
    ]
