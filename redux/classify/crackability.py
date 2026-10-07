"""WPA3/PMF-aware crackability classifier (atlas X-8 — the honesty layer).

Most tools capture indiscriminately and leave you with a pile of pcaps, half of
which can never be cracked offline. This module labels each target truthfully
*before* we spend capture/crack cycles on it, and says why in one line (glass-box).

The ground truth it encodes (2024-2026):
  - **WPA2-PSK** — crackable offline: grab PMKID or an EAPOL M1-M4 handshake,
    convert to hashcat mode **22000**, dictionary/rules attack. The live path.
  - **WPA3-SAE (pure)** — NOT offline-crackable: SAE derives the PMK via Dragonfly,
    so there is no PSK-derived PMKID, and PMF (802.11w) is mandatory in WPA3 so
    deauth-forced reassociation dies too. Capturing it is wasted effort.
  - **WPA3 transition mode** — crackable *via its WPA2 path*: a transition-mode AP
    still answers WPA2-PSK, so the classic 22000 attack applies to that path. This
    is the nuance other tools miss.
  - **OWE / OPEN** — no PSK, nothing to crack. **WEP** — legacy, a different (IV)
    attack, flagged separately. **Enterprise (802.1X/EAP)** — no PSK dictionary
    target; needs credentials, out of scope for offline PSK cracking.

Pure logic, no hardware. This informs capture priority; it never fires anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SecType(str, Enum):
    OPEN = "open"
    WEP = "wep"
    WPA2_PSK = "wpa2-psk"
    WPA3_SAE = "wpa3-sae"
    WPA3_TRANSITION = "wpa3-transition"   # WPA2-PSK + WPA3-SAE mixed
    OWE = "owe"                            # Enhanced Open
    ENTERPRISE = "enterprise"              # 802.1X / EAP (WPA2 or WPA3)
    UNKNOWN = "unknown"


class PMF(str, Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


# hashcat mode for the offline WPA path (PMKID + EAPOL unified since 2019)
HASHCAT_WPA = "22000"


@dataclass(frozen=True)
class Assessment:
    sec_type: SecType
    offline_crackable: bool
    method: str          # how, concretely — or why not
    pmf: PMF
    reason: str          # one-line glass-box summary
    capture_worth_it: bool
    notes: tuple = field(default_factory=tuple)


def _low(x) -> str:
    return str(x or "").strip().lower()


def _akm_tokens(network: dict) -> set:
    """Pull every authentication/AKM hint into one lowercased token set, tolerant
    of the several shapes bettercap / iw / nl80211 report (string or list)."""
    tokens: set = set()
    for key in ("authentication", "akm", "akms", "key_mgmt", "auth"):
        v = network.get(key)
        if isinstance(v, (list, tuple, set)):
            tokens |= {_low(x) for x in v}
        elif v is not None:
            tokens |= {t for t in _low(v).replace("/", " ").replace(",", " ").split()}
    return {t for t in tokens if t}


def classify(network: dict) -> Assessment:
    """Classify one AP dict into a crackability Assessment.

    `network` is tolerant: it reads `encryption`/`cipher` (e.g. "WPA2", "WPA3",
    "WEP", "" / "OPEN"), any authentication/akm hint (PSK, SAE, OWE, 802.1X/EAP,
    MGT), and optional booleans `pmf`/`mfp` and `wps`.
    """
    enc = _low(network.get("encryption"))
    akms = _akm_tokens(network)
    has_sae = any("sae" in t for t in akms) or "wpa3" in enc
    has_psk = any("psk" in t for t in akms)
    has_ent = any(t in ("802.1x", "8021x", "eap", "mgt", "enterprise", "wpa-eap", "wpa2-eap")
                  for t in akms) or "enterprise" in enc or "802.1x" in enc
    has_owe = any("owe" in t for t in akms) or "owe" in enc
    wps = bool(network.get("wps"))

    # --- PMF (802.11w): required under pure WPA3, optional in transition ------
    pmf = _pmf(network, has_sae, has_psk)

    # --- decide the security type --------------------------------------------
    if has_ent:
        sec = SecType.ENTERPRISE
    elif has_sae and has_psk:
        sec = SecType.WPA3_TRANSITION
    elif has_sae:
        sec = SecType.WPA3_SAE
    elif has_owe:
        sec = SecType.OWE
    elif "wpa2" in enc or (has_psk and "wpa3" not in enc):
        sec = SecType.WPA2_PSK
    elif "wep" in enc:
        sec = SecType.WEP
    elif enc in ("", "open", "none") and not akms:
        sec = SecType.OPEN
    elif has_psk:
        sec = SecType.WPA2_PSK  # generic WPA-PSK, treat as the 22000 path
    else:
        sec = SecType.UNKNOWN

    return _assess(sec, pmf, wps)


def _pmf(network: dict, has_sae: bool, has_psk: bool) -> PMF:
    raw = network.get("pmf", network.get("mfp"))
    if raw is not None:
        s = _low(raw)
        if s in ("required", "mandatory", "2", "true"):
            return PMF.REQUIRED
        if s in ("optional", "capable", "1"):
            return PMF.OPTIONAL
        if s in ("none", "disabled", "0", "false"):
            return PMF.NONE
    # infer from the AKM when not stated: pure SAE => required; transition => optional
    if has_sae and has_psk:
        return PMF.OPTIONAL
    if has_sae:
        return PMF.REQUIRED
    return PMF.NONE


def _assess(sec: SecType, pmf: PMF, wps: bool) -> Assessment:
    notes = ("WPS enabled — a separate PIN attack surface",) if wps else ()

    if sec == SecType.WPA2_PSK:
        return Assessment(sec, True, f"capture PMKID/EAPOL → hashcat {HASHCAT_WPA}", pmf,
                          "WPA2-PSK: offline-crackable via PMKID/EAPOL", True, notes)

    if sec == SecType.WPA3_TRANSITION:
        return Assessment(sec, True, f"attack the WPA2 path → hashcat {HASHCAT_WPA}", pmf,
                          "WPA3 transition mode: WPA2 path is still exposed and crackable", True,
                          notes + ("mixed WPA2/WPA3 — target the WPA2 association",))

    if sec == SecType.WPA3_SAE:
        return Assessment(sec, False, "none — SAE (Dragonfly), no PSK-derived PMKID; PMF blocks deauth",
                          pmf, "WPA3-SAE + PMF: no offline path, don't waste capture", False, notes)

    if sec == SecType.ENTERPRISE:
        return Assessment(sec, False, "none for PSK cracking — 802.1X/EAP needs credentials", pmf,
                          "Enterprise (802.1X): not a PSK dictionary target", False, notes)

    if sec == SecType.OWE:
        return Assessment(sec, False, "none — Enhanced Open has no PSK", PMF.REQUIRED,
                          "OWE (Enhanced Open): nothing to crack", False, notes)

    if sec == SecType.OPEN:
        return Assessment(sec, False, "none — open network, no key", PMF.NONE,
                          "Open network: no key to crack", False, notes)

    if sec == SecType.WEP:
        return Assessment(sec, True, "WEP IV attack (not the WPA 22000 path)", PMF.NONE,
                          "WEP: legacy, crackable by a different (IV) method", True,
                          notes + ("legacy cipher — flag, don't route through the WPA pipeline",))

    return Assessment(sec, False, "unknown — classify by hand before capturing", pmf,
                      "unknown security — not enough info to decide", False, notes)


def triage(networks: list) -> list:
    """Rank a list of AP dicts crackable-first, so capture effort goes where it
    can pay off. Returns [(network, Assessment), ...]. Stable within a tier."""
    assessed = [(n, classify(n)) for n in networks]
    # crackable first; WPA2-PSK ahead of WEP (modern pipeline), SAE/enterprise last
    order = {
        SecType.WPA2_PSK: 0, SecType.WPA3_TRANSITION: 1, SecType.WEP: 2,
        SecType.UNKNOWN: 3, SecType.WPA3_SAE: 4, SecType.ENTERPRISE: 5,
        SecType.OWE: 6, SecType.OPEN: 7,
    }
    return sorted(assessed, key=lambda pair: (not pair[1].offline_crackable,
                                              order.get(pair[1].sec_type, 9)))
