"""WPA3 transition / downgrade classifier.

WPA3 is strong — unless the AP runs WPA2/WPA3 *transition mode* to stay compatible
with old clients. Then the WPA2-PSK path is still there, so the network a sticker
calls "WPA3" is crackable the moment a client can be nudged onto the legacy AKM.
Likewise OWE (Enhanced Open) has a transition mode. This spots those: given an
AP's AKM/RSN hints, it names the mode and says whether the strong network has a
weak path hiding behind it.

Reuses crackability's tolerant AKM parsing; pure and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass

from .crackability import _akm_tokens, _low


@dataclass(frozen=True)
class DowngradeFinding:
    mode: str              # wpa3-transition | wpa2 | wpa3-only | owe-transition | open | enterprise | unknown
    downgradable: bool     # is there a weaker PSK path behind a strong one?
    reason: str

    def to_dict(self) -> dict:
        return {"mode": self.mode, "downgradable": self.downgradable, "reason": self.reason}


def assess_downgrade(network: dict) -> DowngradeFinding:
    """Name the AP's security mode and flag a transition-mode downgrade path."""
    tokens = _akm_tokens(network)
    blob = " ".join(sorted(tokens)) + " " + _low(network.get("encryption", "")) + " " + _low(network.get("security", ""))
    has_sae = "sae" in tokens or "wpa3" in blob
    has_psk = any(t in tokens for t in ("psk", "wpa-psk", "wpa2-psk")) or "wpa2" in blob or "wpa-psk" in blob
    has_owe = "owe" in tokens or "owe" in blob
    has_enterprise = any(t in tokens for t in ("eap", "802.1x", "wpa-eap", "wpa2-eap", "mgt"))
    is_open = (not tokens) and ("open" in blob or "none" in blob or not blob.strip())

    if has_sae and has_psk:
        return DowngradeFinding("wpa3-transition", True,
                                "WPA2/WPA3 transition — the WPA2-PSK path is still present and crackable")
    if has_owe and is_open is False and ("open" in blob or has_psk is False):
        # OWE + an open BSS advertised together is an OWE transition
        if "open" in blob:
            return DowngradeFinding("owe-transition", False,
                                    "Enhanced Open transition — no PSK, but the open BSS carries no protection")
    if has_sae:
        return DowngradeFinding("wpa3-only", False, "WPA3-SAE only — no legacy PSK path to downgrade to")
    if has_enterprise:
        return DowngradeFinding("enterprise", False, "802.1X/EAP — needs credentials, not a PSK downgrade")
    if has_psk:
        return DowngradeFinding("wpa2", True, "WPA2-PSK — directly crackable (no downgrade needed)")
    if has_owe:
        return DowngradeFinding("owe", False, "Enhanced Open — no PSK")
    if is_open:
        return DowngradeFinding("open", False, "open network — no key")
    return DowngradeFinding("unknown", False, "could not determine the security mode")
