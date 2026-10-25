"""Infrared — TV-B-Gone-style power blasting + a loadable IR code library.

Yes, the Pi can be a universal remote: an IR LED on a GPIO pin transmits, an IR
receiver (TSOP38238) learns. Both are cents of e-waste (any dead remote donates
an emitter; most AV gear donates a receiver). This module is the catalog + the
gated command plans; the actual blink happens through LIRC (`irsend`) or pigpio
on the device.

Two headline things the operator wanted:

* **TV-B-Gone** — blast a sequence of power (on/off) codes so an un-remoted TV
  downstairs turns off (or on). A built-in starter set of common-brand power
  codes ships; the full database can be imported.
* **A loadable code library** — import **Flipper `.ir`** files (parsed + raw)
  and LIRC configs to drive *your specific* gear, and record→store→replay.

IR transmit is a **POWER-tier** capability (`ir.blast`, `ir.replay`): hidden in
FIELD, LAB-only, per-action `--confirm` (it would hit any IR device in line of
sight). Pure/testable: parsing + command building here; nothing blinks.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional

from .core.power_tier import authorize_power


@dataclass(frozen=True)
class IRCode:
    name: str
    protocol: str = ""            # NEC / Samsung32 / RC5 / raw / ...
    address: str = ""             # parsed codes
    command: str = ""
    frequency: int = 38000        # carrier Hz (38 kHz typical)
    raw: List[int] = field(default_factory=list)   # raw timing list (µs) if raw
    kind: str = "parsed"          # "parsed" | "raw"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IRCode":
        return cls(name=d.get("name", ""), protocol=d.get("protocol", ""),
                   address=d.get("address", ""), command=d.get("command", ""),
                   frequency=int(d.get("frequency", 38000)),
                   raw=list(d.get("raw", [])), kind=d.get("kind", "parsed"))


# A tiny starter set of TV power codes (NEC-family addr/cmd), enough to be useful
# out of the box; a full TV-B-Gone DB can be imported as Flipper .ir / LIRC.
TV_POWER_CODES: tuple = (
    IRCode("Samsung TV power", "Samsung32", "07 00 00 00", "02 00 00 00"),
    IRCode("LG TV power", "NEC", "04 00 00 00", "08 00 00 00"),
    IRCode("Sony TV power", "SIRC", "01 00 00 00", "15 00 00 00"),
    IRCode("Vizio TV power", "NEC", "00 00 00 00", "0D 00 00 00"),
    IRCode("Panasonic TV power", "Kaseikyo", "40 04 00 00", "BD 00 00 00"),
    IRCode("TCL/Roku TV power", "NEC", "57 e3 00 00", "17 00 00 00"),
)


# ---- Flipper .ir parsing ------------------------------------------------- #

def parse_flipper_ir(text: str) -> List[IRCode]:
    """Parse a Flipper `.ir` file into IRCodes (both `parsed` and `raw` blocks)."""
    codes: List[IRCode] = []
    cur: Dict[str, str] = {}

    def flush():
        if not cur.get("name"):
            cur.clear(); return
        if cur.get("type") == "raw":
            raw = [int(x) for x in cur.get("data", "").split() if x.lstrip("-").isdigit()]
            codes.append(IRCode(cur["name"], "raw", frequency=int(cur.get("frequency", "38000")),
                                raw=raw, kind="raw"))
        else:
            codes.append(IRCode(cur["name"], cur.get("protocol", ""),
                                cur.get("address", ""), cur.get("command", ""), kind="parsed"))
        cur.clear()

    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            # a Filetype/Version header comment or blank — a '#' after fields ends a block
            if line.startswith("#") and cur:
                flush()
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "name" and cur:
            flush()
        cur[k] = v
    flush()
    return codes


# ---- library (JSON-backed) ---------------------------------------------- #

class IRLibrary:
    def __init__(self, path, *, now=time.time):
        self.path = Path(path)
        self._now = now

    def _raw(self) -> List[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return []

    def list(self) -> List[IRCode]:
        out = []
        for d in self._raw():
            try:
                out.append(IRCode.from_dict(d))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def get(self, name: str) -> Optional[IRCode]:
        for c in self.list():
            if c.name == name:
                return c
        return None

    def add(self, code: IRCode) -> IRCode:
        codes = [c for c in self.list() if c.name != code.name]
        codes.append(code)
        self._save(codes)
        return code

    def import_flipper(self, text: str) -> int:
        codes = {c.name: c for c in self.list()}
        added = 0
        for c in parse_flipper_ir(text):
            codes[c.name] = c
            added += 1
        self._save(list(codes.values()))
        return added

    def remove(self, name: str) -> bool:
        codes = self.list()
        kept = [c for c in codes if c.name != name]
        if len(kept) == len(codes):
            return False
        self._save(kept)
        return True

    def _save(self, codes: List[IRCode]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps([c.to_dict() for c in codes], indent=2, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, self.path)


# ---- gated plans --------------------------------------------------------- #

@dataclass(frozen=True)
class IRPlan:
    runnable: bool
    argv: List[str]
    reason: str
    count: int = 1

    def to_dict(self) -> dict:
        return {"runnable": self.runnable, "argv": list(self.argv),
                "reason": self.reason, "count": self.count}


def _irsend_argv(code: IRCode, remote: str = "redux") -> List[str]:
    return ["irsend", "SEND_ONCE", remote, re.sub(r"\s+", "_", code.name)]


def blast_plan(*, lab_mode: bool, confirmed: bool,
               codes: Optional[List[IRCode]] = None) -> IRPlan:
    """TV-B-Gone: a gated plan to blast every power code in sequence."""
    ok, why = authorize_power("ir.blast", lab_mode=lab_mode, confirmed=confirmed)
    if not ok:
        return IRPlan(False, [], why)
    seq = list(codes if codes is not None else TV_POWER_CODES)
    argv = ["irsend", "SEND_ONCE", "redux", *[re.sub(r"\s+", "_", c.name) for c in seq]]
    return IRPlan(True, argv, f"{why}; blasts {len(seq)} power code(s) in sequence", len(seq))


def replay_plan(code: IRCode, *, lab_mode: bool, confirmed: bool,
                remote: str = "redux") -> IRPlan:
    """Replay one saved IR code (gated)."""
    ok, why = authorize_power("ir.replay", lab_mode=lab_mode, confirmed=confirmed)
    if not ok:
        return IRPlan(False, [], why)
    return IRPlan(True, _irsend_argv(code, remote), f"{why}; replay '{code.name}'")
