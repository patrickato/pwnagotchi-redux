"""Crack pre-flight — is this capture actually crackable, and why?

Triage ranks the *queue* assuming each artifact is complete. This answers the
prior question for one capture: before you spend GPU hours, what crackable
material does it actually hold — a clean PMKID, a full authenticated handshake,
or a half-exchange that may never match — and if nothing, what's missing and how
to get it. A pure handshake-internals teacher.

Two inputs:
  * a `.hc22000` (already distilled) → decoded directly, no external tools.
  * a raw `.pcap/.pcapng/.cap` → converted with hcxpcapngtool first (the same
    tool the ingest pipeline uses), then decoded. Honest when the tool is
    absent: it says so rather than guessing.

For a type-02 (EAPOL) line we decode the MESSAGEPAIR enum (hashcat's low 3 bits,
documented and stable); M2+M3 / M3+M4 are authenticated pairs (the AP replied —
the strongest capture), M1+M2 is the client's challenge only (crackable, but the
4-way may not have completed, so treat a hit with a little caution). Type-01 is a
PMKID — clientless and deterministic. We deliberately do NOT assert meanings for
the high bits we aren't certain of; the raw byte is surfaced for an expert.

Real-data-only and glass-box: counts come from the artifact itself, every verdict
carries its reason, and an unreadable/toolless input fails safe to "can't assess".
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .ingest import valid_22000_line

# hashcat 22000 MESSAGEPAIR, low 3 bits (stable, documented). (label, authenticated, note)
_MESSAGEPAIR = {
    0: ("M1+M2", False, "EAPOL from M2 — the client's challenge only; the 4-way may not have "
                        "completed, so a wordlist hit is possible but not guaranteed to be the live key"),
    1: ("M1+M4", True, "EAPOL from M4"),
    2: ("M2+M3", True, "EAPOL from M2 — authenticated (the AP replied): the strongest capture"),
    3: ("M2+M3", True, "EAPOL from M3 — authenticated"),
    4: ("M3+M4", True, "EAPOL from M3 — authenticated"),
    5: ("M3+M4", True, "EAPOL from M4 — authenticated"),
}
_AP_LESS = 0x10     # client-only ("AP-LESS") capture — well-known bit
_RAW_SUFFIXES = (".pcap", ".pcapng", ".cap")


@dataclass(frozen=True)
class FrameAssessment:
    bssid: str
    ssid: str
    kind: str                # "pmkid" | "handshake"
    detail: str              # human explanation
    strong: bool             # authenticated handshake or valid PMKID


@dataclass(frozen=True)
class Preflight:
    source: str
    available: bool          # could we assess at all (tool present for raw pcap)
    crackable: bool
    pmkid: int = 0
    handshakes: int = 0
    unique_aps: int = 0
    invalid_lines: int = 0
    frames: Tuple[FrameAssessment, ...] = ()
    reason: str = ""


def _bssid_colon(twelve_hex: str) -> str:
    v = twelve_hex.lower()
    return ":".join(v[i:i + 2] for i in range(0, 12, 2)) if len(v) == 12 else v


def _decode_essid(hexstr: str) -> str:
    try:
        return bytes.fromhex(hexstr).decode("utf-8", "replace") if hexstr else ""
    except ValueError:
        return ""


def assess_22000_text(text: str, *, source: str = "<22000>") -> Preflight:
    """Decode an hc22000 blob into a crackability assessment. Pure, no tools."""
    frames: List[FrameAssessment] = []
    aps = set()
    pmkid = handshakes = invalid = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not valid_22000_line(line):
            # a non-blank, non-comment line that isn't a valid 22000 record
            if line.upper().startswith("WPA"):
                invalid += 1
            continue
        f = line.split("*")
        bssid = _bssid_colon(f[3])
        ssid = _decode_essid(f[5])
        aps.add(bssid)
        if f[1] == "01":
            pmkid += 1
            frames.append(FrameAssessment(bssid, ssid, "pmkid",
                          "PMKID — clientless and deterministic; crackable as-is", True))
        else:
            handshakes += 1
            try:
                mp_byte = int(f[8], 16)
            except ValueError:
                mp_byte = 0
            label, authed, note = _MESSAGEPAIR.get(mp_byte & 0x07, ("unknown pair", False, "unrecognised message pair"))
            apless = " [AP-LESS/client-only]" if mp_byte & _AP_LESS else ""
            detail = f"handshake {label}{apless} (messagepair 0x{mp_byte:02x}) — {note}"
            frames.append(FrameAssessment(bssid, ssid, "handshake", detail, authed))
    crackable = (pmkid + handshakes) > 0
    if crackable:
        strong = sum(1 for fr in frames if fr.strong)
        reason = (f"crackable — {pmkid} PMKID, {handshakes} handshake(s) across "
                  f"{len(aps)} AP(s); {strong} strong artifact(s)"
                  + (f"; {invalid} malformed line(s) skipped" if invalid else ""))
    else:
        reason = ("no crackable material — no valid PMKID or EAPOL pair found"
                  + (f" ({invalid} malformed line(s))" if invalid else "")
                  + ". Capture a PMKID (clientless) or a full 4-way (needs an associating client)")
    return Preflight(source=source, available=True, crackable=crackable,
                     pmkid=pmkid, handshakes=handshakes, unique_aps=len(aps),
                     invalid_lines=invalid, frames=tuple(frames), reason=reason)


def assess_capture(path: str, *, runner=subprocess.run, hcx: str = "hcxpcapngtool") -> Preflight:
    """Assess a capture file. `.hc22000` is decoded directly; a raw pcap is first
    converted with hcxpcapngtool (injectable `runner`). Honest on a missing tool
    or unreadable file — never a fabricated verdict."""
    low = path.lower()
    if low.endswith(".hc22000"):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            return Preflight(source=path, available=False, crackable=False,
                             reason=f"cannot read {path}: {e}")
        return assess_22000_text(text, source=path)

    if not low.endswith(_RAW_SUFFIXES):
        return Preflight(source=path, available=False, crackable=False,
                         reason=f"unsupported file type (expected .hc22000 or {', '.join(_RAW_SUFFIXES)})")

    with tempfile.TemporaryDirectory(prefix="redux-preflight-") as tmp:
        out = os.path.join(tmp, "converted.hc22000")
        try:
            proc = runner([hcx, "-o", out, path],
                          capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            return Preflight(source=path, available=False, crackable=False,
                             reason=f"{hcx} not installed — can't assess a raw capture; "
                                    "convert it to .hc22000 first, or run preflight on the .hc22000")
        except (OSError, subprocess.SubprocessError) as e:
            return Preflight(source=path, available=False, crackable=False,
                             reason=f"{hcx} failed: {e}")
        try:
            with open(out, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            text = ""
        pf = assess_22000_text(text, source=path)
        if not pf.crackable:
            tail = (getattr(proc, "stderr", "") or "").strip().splitlines()
            hint = (" | hcxpcapngtool: " + tail[-1]) if tail else ""
            return Preflight(source=path, available=True, crackable=False,
                             reason=pf.reason + hint, invalid_lines=pf.invalid_lines)
        return pf
