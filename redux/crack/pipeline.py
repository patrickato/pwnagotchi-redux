"""capture→crack pipeline.

`crack()` takes a capture file you already hold and a wordlist, optionally checks
the target against the central Scope (defense in depth — you should only be
cracking captures of authorized targets), and runs an injected cracker. It never
fabricates a result: if the cracker tool isn't installed it says so plainly
rather than pretending, and every outcome carries a human-readable reason.

The cracker is a small protocol so the pipeline is pure/testable:
  - ReplayCracker   — canned results, no tools (tests / dry runs)
  - AircrackCracker — shells out to aircrack-ng on the device; honest tool-absent
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol


class CrackRefused(Exception):
    """Raised when a crack is refused because the target isn't in scope."""


@dataclass(frozen=True)
class CrackResult:
    cracked: bool
    target: str                       # bssid or ssid identifying the capture
    reason: str                       # glass-box: why cracked / not / refused / unavailable
    psk: Optional[str] = None         # recovered key, if cracked
    tool: str = ""                    # which cracker ran (or "" if none could)
    available: bool = True            # False = the tool wasn't present (not a real "no")
    detail: Dict = field(default_factory=dict)


class Cracker(Protocol):
    def run(self, capture_path: str, wordlist_path: str, *, bssid: Optional[str] = None,
            ssid: Optional[str] = None) -> CrackResult: ...


class ReplayCracker:
    """Offline cracker for tests / dry runs. Returns canned outcomes keyed by the
    target; no external tools, no sockets."""

    def __init__(self, found: Optional[Dict[str, str]] = None):
        # map of target (bssid or ssid) -> psk that this fixture "recovers"
        self._found = {(k or "").lower(): v for k, v in (found or {}).items()}

    def run(self, capture_path, wordlist_path, *, bssid=None, ssid=None) -> CrackResult:
        target = (bssid or ssid or "").lower()
        psk = self._found.get(target)
        if psk is not None:
            return CrackResult(True, target or "?", f"recovered PSK from {os.path.basename(capture_path)}",
                               psk=psk, tool="replay", detail={"wordlist": os.path.basename(wordlist_path)})
        return CrackResult(False, target or "?", "key not in wordlist", tool="replay",
                           detail={"wordlist": os.path.basename(wordlist_path)})


class AircrackCracker:
    """Live cracker: aircrack-ng against the capture + wordlist. If aircrack-ng
    isn't installed, returns available=False with a clear message — never a fake
    'not cracked'. (The device gate: install it via the Kali pack.)"""

    BINARY = "aircrack-ng"

    def run(self, capture_path, wordlist_path, *, bssid=None, ssid=None) -> CrackResult:
        target = (bssid or ssid or "").lower()
        if shutil.which(self.BINARY) is None:
            return CrackResult(False, target or "?",
                               f"{self.BINARY} is not installed — enable the Kali pack to crack on-device",
                               tool="", available=False)
        cmd = [self.BINARY, "-w", wordlist_path]
        if bssid:
            cmd += ["-b", bssid]
        cmd += [capture_path]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=None)
        except (OSError, subprocess.SubprocessError) as e:
            return CrackResult(False, target or "?", f"aircrack-ng failed to run: {e}", tool=self.BINARY,
                               available=False)
        text = out.stdout + out.stderr
        # aircrack-ng prints: "KEY FOUND! [ the-passphrase ]"
        marker = "KEY FOUND! ["
        if marker in text:
            psk = text.split(marker, 1)[1].split("]", 1)[0].strip()
            return CrackResult(True, target or "?", "aircrack-ng recovered the PSK", psk=psk, tool=self.BINARY)
        return CrackResult(False, target or "?", "aircrack-ng exhausted the wordlist without a match",
                           tool=self.BINARY)


def crack(capture_path: str, wordlist_path: str, *, bssid: Optional[str] = None,
          ssid: Optional[str] = None, scope=None, cracker: Optional[Cracker] = None,
          require_scope: bool = True) -> CrackResult:
    """Crack a capture you hold, optionally gated on the central Scope.

    Scope check is defense-in-depth: the capture itself was scope-gated at the
    radio, and cracking is offline, but if a Scope is supplied we still refuse a
    target that isn't authorized — so stray captures can't be worked on.
    """
    if not os.path.isfile(capture_path):
        return CrackResult(False, (bssid or ssid or "?"), f"capture file not found: {capture_path}",
                           available=False)
    if not os.path.isfile(wordlist_path):
        return CrackResult(False, (bssid or ssid or "?"), f"wordlist not found: {wordlist_path}",
                           available=False)
    if scope is not None and require_scope:
        ok, why = scope.authorize(bssid=bssid, ssid=ssid)
        if not ok:
            raise CrackRefused(f"refused to crack {bssid or ssid or '?'}: {why}")
    cracker = cracker or AircrackCracker()
    return cracker.run(capture_path, wordlist_path, bssid=bssid, ssid=ssid)
