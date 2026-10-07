"""redux.eap — WPA-Enterprise (802.1X) EAP credential capture.

The eaphammer/hostapd-mana playbook: a Scope-aimed, posture-gated rogue enterprise
AP that captures MSCHAPv2 challenge/response (crackable with hashcat -m 5500 / John
NETNTLM) or a GTC-downgrade cleartext credential. Firing-capable → gated exactly
like the rest of offense (SSID must be in Scope, offense posture active,
authorized=True). Pure core (plan + crackable-line conversion); the AP stand-up is
hardware with honest tool-absence.
"""
from .eap import (
    MschapV2Credential, GtcCredential, parse_hostapd_wpe,
    EapConfig, EapPlan, EapHarvester, authorize_eap,
)

__all__ = [
    "MschapV2Credential", "GtcCredential", "parse_hostapd_wpe",
    "EapConfig", "EapPlan", "EapHarvester", "authorize_eap",
]
