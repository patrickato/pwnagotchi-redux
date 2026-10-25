"""Purple-team "explain & defend" — for every technique Augur can run, how a
defender detects it and how they stop it.

The best red-teamers know exactly how they get caught. This is the blue-team
mirror of the toolkit: each offensive technique mapped to what it is, the
artifacts/signatures a defender sees, and the concrete mitigations that beat it.
It also powers the remediation section of the engagement report, so findings
come with fixes, not just problems.

Static, factual, mode-agnostic. The forbidden emitters (beacon/BLE flood, RF
jamming) are documented here for defensive understanding even though Augur never
transmits them — knowing their signatures is part of the learning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Technique:
    key: str
    name: str
    what: str
    detection: Tuple[str, ...]
    mitigation: Tuple[str, ...]
    standards: Tuple[str, ...] = ()      # the defenses that actually close it


TECHNIQUES: Dict[str, Technique] = {
    "deauth": Technique(
        "deauth", "Deauthentication / disassociation",
        "Spoofed 802.11 management frames that kick a client off its AP, usually to "
        "force a reassociation so the 4-way handshake can be captured.",
        ("A burst of deauth/disassoc frames, commonly with a spoofed AP BSSID and "
         "reason code 7 ('class-3 frame from nonassociated STA').",
         "A WIDS/WIPS flags the frame rate and the source; clients log repeated drops."),
        ("Enable 802.11w Protected Management Frames (PMF) — it cryptographically "
         "authenticates deauth/disassoc so spoofed ones are ignored.",
         "WPA3 mandates PMF; enabling WPA3 (or WPA2 with PMF required) closes this."),
        ("802.11w (PMF)", "WPA3")),
    "pmkid": Technique(
        "pmkid", "PMKID (clientless) capture",
        "Request the RSN PMKID from an AP's first EAPOL message during (attempted) "
        "association — no client needed — then crack it offline.",
        ("A single association/EAPOL-M1 exchange from an unfamiliar client; very low "
         "signal and often indistinguishable from a normal roam.",),
        ("Use WPA3-SAE, which is not vulnerable to the offline PMKID dictionary attack.",
         "Use a long, high-entropy PSK so an offline crack fails even if the PMKID leaks.",
         "On APs that allow it, disable PMKID/opportunistic-key caching."),
        ("WPA3-SAE",)),
    "handshake": Technique(
        "handshake", "WPA 4-way handshake capture",
        "Passively capture the 4-way handshake (often after a deauth) and crack the "
        "PSK offline with a wordlist/mask.",
        ("The capture itself is passive and essentially undetectable; the paired "
         "deauth used to force it is the visible part.",),
        ("A long, random passphrase defeats the offline crack regardless of capture.",
         "WPA3-SAE resists offline dictionary attacks entirely.",
         "PMF removes the deauth lever used to force the reassociation."),
        ("WPA3-SAE", "802.11w (PMF)")),
    "evil_twin": Technique(
        "evil_twin", "Evil twin / rogue AP",
        "Stand up an AP cloning a target SSID so clients associate to you instead of "
        "the real network.",
        ("A duplicate SSID advertised from a new/unexpected BSSID; signal-strength and "
         "channel anomalies; WIDS rogue-AP alerts; clients may warn about a changed network.",),
        ("WPA2/3-Enterprise (802.1X/EAP) with server-certificate validation — the "
         "client authenticates the network, so a twin without the cert fails.",
         "Disable auto-join to open/again-seen networks; pin the expected BSSID where possible."),
        ("802.1X / WPA2-Enterprise", "server-cert validation")),
    "karma": Technique(
        "karma", "KARMA / probe-response",
        "Answer clients' directed probe requests for any SSID in their preferred-network "
        "list, luring them to auto-connect.",
        ("A single AP responding as many different SSIDs; a device auto-joining a network "
         "that isn't really present; WIDS flags the multi-SSID responder.",),
        ("Turn off auto-join for open networks and forget stale saved networks (prune the PNL).",
         "Use MAC randomization and send no directed probes where the OS allows it."),
        ("PNL hygiene", "MAC randomization")),
    "wps": Technique(
        "wps", "WPS / Pixie-Dust",
        "Attack the WPS registrar (online PIN brute force or the offline Pixie-Dust flaw) "
        "to recover the PSK.",
        ("Repeated WPS registrar/EAP-WSC attempts in the AP logs; a burst of association "
         "attempts.",),
        ("Disable WPS entirely — there is no safe way to leave it on.",),
        ("WPS disabled",)),
    "evil_portal": Technique(
        "evil_portal", "Captive / evil portal",
        "Serve a fake captive portal (often behind an evil twin) to harvest credentials "
        "the user types in.",
        ("A captive portal appearing on a network that normally has none; TLS certificate "
         "warnings; a portal asking for credentials it shouldn't need.",),
        ("Never enter real credentials into a captive portal; validate the URL/cert.",
         "Enterprise auth (802.1X) and a VPN remove the portal's leverage; HSTS blocks "
         "TLS downgrade."),
        ("802.1X", "VPN", "HSTS")),
    "netrecon": Technique(
        "netrecon", "Network recon (port/service scan)",
        "Once on the network, enumerate live hosts, open ports and service versions "
        "(nmap -sV) to find a foothold.",
        ("Port-scan signatures — many SYNs across ports/hosts; IDS/IPS alerts; connection "
         "logs on scanned services.",),
        ("Segment the network (VLANs) and firewall between segments; IDS/IPS with rate "
         "limiting; expose only needed services.",),
        ("segmentation", "IDS/IPS")),
    "cred_spray": Technique(
        "cred_spray", "Credential spraying / reuse",
        "Try a recovered or common credential against services (router admin, SSH, etc.), "
        "exploiting password reuse.",
        ("Many failed authentications from one source; account-lockout and auth-failure "
         "logs; fail2ban triggers.",),
        ("Multi-factor authentication; unique high-entropy credentials per service; "
         "lockout/rate-limiting; fail2ban; change default admin passwords.",),
        ("MFA", "unique credentials", "lockout")),
    "beacon_flood": Technique(
        "beacon_flood", "Beacon/BLE flood (NOT emitted by Augur)",
        "Flood the air with fake beacons/advertisements. Indiscriminate — it hits "
        "bystanders — so Augur has no transmit path for it; documented for defense only.",
        ("A flood of SSIDs/BLE devices appearing at once; WIDS flags the beacon rate; "
         "a spectrum view shows the surge.",),
        ("WIDS alerting on beacon rate; client-side, ignore the noise (it's a nuisance, "
         "not a compromise).",),
        ()),
    "jamming": Technique(
        "jamming", "RF jamming (NOT emitted by Augur — and illegal)",
        "Transmit noise to deny the channel. Indiscriminate and illegal to transmit; "
        "Augur has no jamming path. Documented so you can recognise it.",
        ("Noise floor spike on a spectrum analyzer; sudden total loss of a band with no "
         "management frames; directional source-finding.",),
        ("Spectrum monitoring to detect and locate the source; wired fallback; report "
         "illegal jamming to the regulator — it cannot be 'mitigated' on the client.",),
        ()),
}

# Finding-kind -> technique key, for the engagement report's remediation section.
_FINDING_TO_TECHNIQUE = {
    "pmkid": "pmkid",
    "handshake": "handshake",
    "wpa2-psk": "handshake",
    "wpa": "handshake",
    "deauth": "deauth",
    "evil_twin": "evil_twin",
    "evil_portal": "evil_portal",
    "karma": "karma",
    "wps": "wps",
    "netrecon": "netrecon",
    "cred_spray": "cred_spray",
}


def explain(key: str) -> Optional[Technique]:
    """Look up one technique by key (case/hyphen-insensitive)."""
    if not key:
        return None
    norm = key.strip().lower().replace("-", "_").replace(" ", "_")
    if norm in TECHNIQUES:
        return TECHNIQUES[norm]
    mapped = _FINDING_TO_TECHNIQUE.get(norm) or _FINDING_TO_TECHNIQUE.get(key.strip().lower())
    return TECHNIQUES.get(mapped) if mapped else None


def list_techniques() -> List[Tuple[str, str]]:
    return [(t.key, t.name) for t in TECHNIQUES.values()]


def remediation_for(finding_kind: str) -> Tuple[str, ...]:
    """Mitigations for a finding kind (e.g. 'handshake', 'pmkid'), for the report.
    Empty tuple when nothing maps — never a fabricated fix."""
    t = explain(finding_kind)
    return t.mitigation if t else ()
