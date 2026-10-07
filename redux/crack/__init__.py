"""Crack pipeline — the offline side of capture→crack.

Capturing a handshake/PMKID is the firing step (scope-gated at the radio). This
package is what pwnagotchi never did on-device: take a capture you legitimately
hold and recover the PSK against a wordlist, with a glass-box result. The actual
cracker is injected (aircrack-ng / hashcat on hardware; a replay cracker in
tests), so the pipeline logic is testable with no tools installed.
"""
from .pipeline import (
    CrackResult,
    Cracker,
    ReplayCracker,
    AircrackCracker,
    CrackRefused,
    crack,
)
from .capture import (
    CapturePlan, CaptureProvider, AngryOxideConfig, AngryOxideProvider,
    BettercapProvider, select_capture_provider, register_capture_providers,
)

__all__ = [
    "CrackResult",
    "Cracker",
    "ReplayCracker",
    "AircrackCracker",
    "CrackRefused",
    "crack",
    "CapturePlan", "CaptureProvider", "AngryOxideConfig", "AngryOxideProvider",
    "BettercapProvider", "select_capture_provider", "register_capture_providers",
]
