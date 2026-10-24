"""Sub-GHz signal library — capture, decode, catalog, and (gated) replay.

The Flipper-style toy for the junkyard haul: old keyfobs, garage remotes, and
cheap ISM gadgets that live below 1 GHz (300–928 MHz). Two halves, honestly
scoped to the hardware:

* **Capture / decode / catalog** — RX only, works with a plain RTL-SDR today.
  Record a signal, let rtl_433 decode what it can, and file it in a library.
* **Replay** — *transmitting* the captured signal back. An RTL-SDR CANNOT
  transmit, so replay needs a TX-capable backend: **hackrf** (hackrf_transfer),
  **rpitx** (Pi GPIO as a transmitter), or a **cc1101** module. Replay is a
  POWER-tier capability: hidden in FIELD, LAB-only, and requires an explicit
  per-action confirmation (see `redux.core.power_tier`). If no TX backend is
  present, replay is refused with a reason — capture/catalog still work.

Honest about the limits: **rolling-code** remotes (modern cars) will not replay
— that's the security working — so the library flags them rather than pretending.
Pure/testable: command builders and gating are injectable; nothing transmits here.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..core.power_tier import authorize_power

# TX backends, best-first. Each maps to the binary that proves it's present.
TX_BACKENDS: Dict[str, str] = {
    "hackrf": "hackrf_transfer",   # HackRF One: raw IQ replay, 1 MHz–6 GHz
    "rpitx": "sendook",            # Pi GPIO transmitter (cage only!)
    "cc1101": "rfcat",             # CC1101 module via rfcat
}

# rtl_433 protocol families that are rolling-code (won't replay usefully).
_ROLLING_HINTS = ("keeloq", "rolling", "hcs200", "hcs300", "hcs301", "aes")


@dataclass(frozen=True)
class SubGhzSignal:
    id: str                       # short stable id
    name: str                     # operator label
    freq_hz: float
    modulation: str = "OOK"       # OOK / ASK / FSK / raw
    protocol: str = ""            # rtl_433 model/protocol if decoded
    capture_path: str = ""        # raw IQ / .ook file on disk, if kept
    decoded: dict = field(default_factory=dict)
    rolling: bool = False         # looks like a rolling code (won't replay)
    captured_at: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SubGhzSignal":
        return cls(id=d["id"], name=d.get("name", ""), freq_hz=float(d.get("freq_hz", 0.0)),
                   modulation=d.get("modulation", "OOK"), protocol=d.get("protocol", ""),
                   capture_path=d.get("capture_path", ""), decoded=dict(d.get("decoded", {})),
                   rolling=bool(d.get("rolling", False)), captured_at=float(d.get("captured_at", 0.0)),
                   note=d.get("note", ""))


def parse_freq(s) -> float:
    """Parse '433.92M', '315M', '868k', or bare Hz -> float Hz."""
    t = str(s).strip().lower()
    mult = 1.0
    if t.endswith("g"):
        mult, t = 1e9, t[:-1]
    elif t.endswith("m"):
        mult, t = 1e6, t[:-1]
    elif t.endswith("k"):
        mult, t = 1e3, t[:-1]
    return float(t) * mult


def looks_rolling(decoded: Optional[dict], protocol: str = "") -> bool:
    """Heuristic: does this look like a rolling code (so replay is pointless)?"""
    hay = (protocol + " " + json.dumps(decoded or {})).lower()
    return any(h in hay for h in _ROLLING_HINTS)


# ---- capture (RX; works with a plain RTL-SDR) --------------------------- #

def capture_command(freq_hz: float, *, seconds: int = 10, out_path: str = "",
                    device: int = 0, decode: bool = True) -> List[str]:
    """Build the RX capture command. decode=True uses rtl_433 (identify/catalog);
    decode=False uses rtl_sdr for a raw IQ grab (what you'd replay later)."""
    f = f"{freq_hz/1e6:g}M"
    if decode:
        argv = ["rtl_433", "-d", str(device), "-f", f, "-F", "json"]
        if seconds:
            argv += ["-T", str(seconds)]
        return argv
    # raw IQ capture for later replay
    argv = ["rtl_sdr", "-d", str(device), "-f", str(int(freq_hz)), "-s", "2000000"]
    if seconds:
        argv += ["-n", str(int(2000000 * seconds))]
    argv += [out_path or "capture.cu8"]
    return argv


# ---- library (JSON-backed) ---------------------------------------------- #

class SubGhzLibrary:
    def __init__(self, path, *, now: Callable[[], float] = time.time):
        self.path = Path(path)
        self._now = now

    def _load_raw(self) -> List[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return []

    def list(self) -> List[SubGhzSignal]:
        out = []
        for d in self._load_raw():
            try:
                out.append(SubGhzSignal.from_dict(d))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def get(self, key: str) -> Optional[SubGhzSignal]:
        for s in self.list():
            if s.id == key or s.name == key:
                return s
        return None

    def add(self, name: str, freq_hz: float, *, modulation: str = "OOK", protocol: str = "",
            capture_path: str = "", decoded: Optional[dict] = None, note: str = "") -> SubGhzSignal:
        sigs = self.list()
        sid = f"sg{len(sigs)+1:03d}"
        existing = {s.id for s in sigs}
        while sid in existing:
            sid = f"sg{int(sid[2:])+1:03d}"
        sig = SubGhzSignal(
            id=sid, name=name or sid, freq_hz=float(freq_hz), modulation=modulation,
            protocol=protocol, capture_path=capture_path, decoded=dict(decoded or {}),
            rolling=looks_rolling(decoded, protocol), captured_at=self._now(), note=note)
        sigs.append(sig)
        self._save(sigs)
        return sig

    def remove(self, key: str) -> bool:
        sigs = self.list()
        kept = [s for s in sigs if s.id != key and s.name != key]
        if len(kept) == len(sigs):
            return False
        self._save(kept)
        return True

    def _save(self, sigs: List[SubGhzSignal]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps([s.to_dict() for s in sigs], indent=2, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.path)


# ---- replay (TX; POWER-tier gated, needs a TX backend) ------------------ #

def detect_tx_backend(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    """First available TX backend, or None (RTL-SDR can't transmit)."""
    for name, binary in TX_BACKENDS.items():
        if which(binary):
            return name
    return None


@dataclass(frozen=True)
class ReplayPlan:
    runnable: bool
    backend: Optional[str]
    argv: List[str]
    reason: str

    def to_dict(self) -> dict:
        return {"runnable": self.runnable, "backend": self.backend,
                "argv": list(self.argv), "reason": self.reason}


def replay_plan(signal: SubGhzSignal, *, lab_mode: bool, confirmed: bool,
                backend: Optional[str] = None,
                which: Callable[[str], Optional[str]] = shutil.which) -> ReplayPlan:
    """Build a gated sub-GHz replay plan. Order of refusal: power-tier gate
    (LAB + confirm) → rolling-code → TX backend present → capture file present."""
    ok, why = authorize_power("subghz.replay", lab_mode=lab_mode, confirmed=confirmed)
    if not ok:
        return ReplayPlan(False, None, [], why)
    if signal.rolling:
        return ReplayPlan(False, None, [], (
            f"refused: '{signal.name}' looks like a ROLLING code — replay won't work "
            "(this is the remote's security, not a bug)"))
    be = backend or detect_tx_backend(which)
    if be is None:
        return ReplayPlan(False, None, [], (
            "refused: no TX backend present — an RTL-SDR cannot transmit. "
            "Add hackrf / rpitx / cc1101 to replay (capture + catalog still work)."))
    if be not in TX_BACKENDS:
        return ReplayPlan(False, None, [], f"refused: unknown TX backend '{be}'")
    cap = signal.capture_path
    if be in ("hackrf",) and not (cap and Path(cap).is_file()):
        return ReplayPlan(False, be, [], (
            f"refused: {be} replays a raw IQ capture, but no capture file is on record "
            f"for '{signal.name}'"))
    if be == "hackrf":
        argv = ["hackrf_transfer", "-t", cap, "-f", str(int(signal.freq_hz)),
                "-s", "2000000", "-x", "20"]
    elif be == "rpitx":
        argv = ["sendook", "-f", str(int(signal.freq_hz)), cap or "signal.ook"]
    else:  # cc1101 via rfcat (operator script)
        argv = ["rfcat", "-r", f"freq={int(signal.freq_hz)}", "replay", cap or "signal.cap"]
    return ReplayPlan(True, be, argv,
                      f"authorized: replay '{signal.name}' @ {signal.freq_hz/1e6:g} MHz via {be} "
                      f"({why})")
