"""Power-tier gating — the hard, non-negotiable gate for the strongest tools.

Scope decides WHERE the device may aim. Mode (LAB/FIELD) decides the posture.
This adds a third axis the operator demanded for the genuinely powerful,
easily-misused capabilities (RF transmit/replay, sub-GHz replay, IR blast,
BadUSB/HID): a **risk tier** that is stricter than ordinary gating.

Three tiers:

* **NORMAL** — ordinary capabilities, gated the usual way (scope/mode).
* **POWER** — the sensitive power-user tools. These:
    1. do **not appear at all** in FIELD (they aren't options, not merely
       refused) — `visible_in("field")` omits them;
    2. are available **only in LAB**;
    3. even in LAB require an explicit **per-action confirmation** — the
       operator must opt in ("are you sure? yes") every time, via a
       `--confirm`/dialog token. Toggling LAB is not enough.
* **FORBIDDEN** — things that cannot be aimed and hit bystanders (RF jamming,
  indiscriminate beacon/BLE spam). Never available, in any mode, confirm or not.
  They stay detection-only, per the project contract (and the law).

This module is the single source of truth: new powerful features `register()`
here and route their fire step through `authorize_power()`. Pure/testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class Tier(str, Enum):
    NORMAL = "normal"
    POWER = "power"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class PowerCapability:
    id: str                    # e.g. "sdr.replay"
    label: str                 # human label
    tier: Tier
    risk: str                  # one line: why it's sensitive / why forbidden

    @property
    def field_visible(self) -> bool:
        # Only NORMAL capabilities are ever shown in FIELD.
        return self.tier == Tier.NORMAL

    @property
    def needs_confirmation(self) -> bool:
        return self.tier == Tier.POWER

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "tier": self.tier.value,
                "risk": self.risk, "field_visible": self.field_visible,
                "needs_confirmation": self.needs_confirmation}


# Seeded registry. POWER entries may exist before their feature ships, so the
# gate is ready the moment the feature lands. FORBIDDEN entries are declared so
# a UI can explain *why* they're absent rather than silently hiding them.
_REGISTRY: Dict[str, PowerCapability] = {}


def register(cap: PowerCapability) -> PowerCapability:
    _REGISTRY[cap.id] = cap
    return cap


def _seed():
    for cap in (
        # --- POWER: LAB-only + per-action confirmation ---
        PowerCapability("sdr.tx", "SDR transmit", Tier.POWER,
                        "transmits RF — can interfere with real devices/services; TX-capable radio only"),
        PowerCapability("sdr.replay", "RF capture replay", Tier.POWER,
                        "re-transmits a captured signal (keyfob/garage/remote) — only your own gear in the cage"),
        PowerCapability("subghz.replay", "Sub-GHz replay", Tier.POWER,
                        "replays a sub-GHz remote (315/433/868/915 MHz) — can actuate real devices"),
        PowerCapability("ir.blast", "IR mass power codes (TV-B-Gone)", Tier.POWER,
                        "blasts many on/off IR codes — would affect any IR device in line of sight"),
        PowerCapability("ir.replay", "IR code replay", Tier.POWER,
                        "replays a saved IR remote code"),
        PowerCapability("badusb.hid", "BadUSB / HID injection", Tier.POWER,
                        "emulates a keyboard and types/injects keystrokes into a host"),
        PowerCapability("bt.hid", "Bluetooth HID control", Tier.POWER,
                        "emulates/drives a Bluetooth input device"),
        # --- FORBIDDEN: never available (bystander harm; illegal to transmit) ---
        PowerCapability("rf.jam", "RF jamming", Tier.FORBIDDEN,
                        "indiscriminate denial of a band — illegal to transmit, always hits bystanders"),
        PowerCapability("beacon.spam", "Indiscriminate beacon/BLE spam", Tier.FORBIDDEN,
                        "floods a whole area with fake APs/BLE adverts — cannot be aimed; detection-only"),
    ):
        register(cap)


_seed()


def get(cap_id: str) -> Optional[PowerCapability]:
    return _REGISTRY.get(cap_id)


def tier_of(cap_id: str) -> Tier:
    cap = _REGISTRY.get(cap_id)
    return cap.tier if cap else Tier.NORMAL


def is_power(cap_id: str) -> bool:
    return tier_of(cap_id) == Tier.POWER


def needs_confirmation(cap_id: str) -> bool:
    cap = _REGISTRY.get(cap_id)
    return bool(cap and cap.needs_confirmation)


def visible_in(mode: str) -> List[PowerCapability]:
    """Capabilities a UI should SHOW in this mode. In FIELD, POWER and FORBIDDEN
    are omitted entirely — they are not options. In LAB, POWER shows (with a
    confirm step); FORBIDDEN never shows anywhere."""
    lab = str(mode).strip().lower() == "lab"
    out = []
    for cap in _REGISTRY.values():
        if cap.tier == Tier.FORBIDDEN:
            continue
        if cap.tier == Tier.POWER and not lab:
            continue
        out.append(cap)
    return sorted(out, key=lambda c: (c.tier.value, c.id))


def authorize_power(cap_id: str, *, lab_mode: bool, confirmed: bool) -> tuple[bool, str]:
    """The hard gate for a POWER capability. (ok, reason), fail-closed.

    - unknown id -> treated as NORMAL (this gate doesn't apply; allow and say so)
    - FORBIDDEN  -> never
    - POWER in FIELD -> refused AND not an option (hidden)
    - POWER in LAB without confirmation -> refused, asks to confirm
    - POWER in LAB with confirmation -> allowed
    """
    cap = _REGISTRY.get(cap_id)
    if cap is None or cap.tier == Tier.NORMAL:
        return True, f"'{cap_id}' is not a power-tier capability — ordinary gating applies"
    if cap.tier == Tier.FORBIDDEN:
        return False, (f"refused: '{cap.label}' is FORBIDDEN in every mode — {cap.risk}")
    # POWER
    if not lab_mode:
        return False, (f"refused: '{cap.label}' is a POWER tool — not available in FIELD "
                       "(switch to LAB on your own gear)")
    if not confirmed:
        return False, (f"confirm required: '{cap.label}' — {cap.risk}. "
                       "Re-run with --confirm (or press OK) to proceed in LAB.")
    return True, f"authorized: '{cap.label}' in LAB with explicit confirmation"
