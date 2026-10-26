"""MAC randomization for Augur's OWN radios — blend in, don't fingerprint.

A device that keeps the same MAC across sessions is trivially tracked and
fingerprinted. This randomizes the MAC of Augur's own interfaces so the box
doesn't present a stable identity to anyone watching the air. Two flavours:

  - "vendor"  — a plausible real-vendor OUI + random NIC bytes, so the address
                blends in with ordinary consumer gear (the stealthy default);
  - "laa"     — a locally-administered random address (the U/L bit set), the
                honest "this is randomized" convention phones use.

It only ever touches THIS device's own interfaces (operator OPSEC), and the apply
step runs through an injected runner so command construction + address generation
are fully testable with no radio. Setting a MAC needs the link down briefly, so
the Operator calls this on an uplink/idle radio, never mid-capture on the monitor
radio.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable, List, Optional

_IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")

# A small set of common, real consumer-vendor OUIs to blend in with. Using a real
# OUI on your OWN device is standard anti-fingerprinting — the NIC bytes are random
# so it never collides with a specific real unit.
VENDOR_OUIS = (
    "3c:5a:b4",  # Google
    "f0:9f:c2",  # Ubiquiti
    "a4:83:e7",  # Apple
    "dc:a6:32",  # Raspberry Pi
    "b8:27:eb",  # Raspberry Pi
    "e0:cb:bc",  # Samsung
    "44:65:0d",  # Amazon
    "fc:fb:fb",  # Cisco
)


def _rand_byte(rng) -> int:
    return rng.randint(0x00, 0xff)


def random_mac(*, mode: str = "vendor", rng: Optional[random.Random] = None) -> str:
    """Generate a random MAC. 'vendor' uses a plausible real OUI; 'laa' sets the
    locally-administered bit and clears multicast on a fully random address."""
    r = rng or random
    if mode == "vendor":
        oui = r.choice(VENDOR_OUIS)
        nic = ":".join(f"{_rand_byte(r):02x}" for _ in range(3))
        return f"{oui}:{nic}"
    # locally-administered: first octet has bit1 (LAA) set, bit0 (multicast) clear
    first = (_rand_byte(r) & 0xfe) | 0x02
    rest = ":".join(f"{_rand_byte(r):02x}" for _ in range(5))
    return f"{first:02x}:{rest}"


def set_mac_commands(iface: str, mac: str) -> List[List[str]]:
    """The exact argv sequence to apply a MAC: down → set address → up."""
    return [
        ["ip", "link", "set", "dev", iface, "down"],
        ["ip", "link", "set", "dev", iface, "address", mac],
        ["ip", "link", "set", "dev", iface, "up"],
    ]


@dataclass(frozen=True)
class MacResult:
    ok: bool
    iface: str
    mac: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "iface": self.iface, "mac": self.mac, "reason": self.reason}


@dataclass
class MacRandomizer:
    """Applies a fresh random MAC to one of the device's own interfaces. `runner`
    is injected for tests; real on the device (needs CAP_NET_ADMIN)."""
    runner: Callable = None
    mode: str = "vendor"
    rng: Optional[random.Random] = None

    def randomize(self, iface: str, *, mac: Optional[str] = None) -> MacResult:
        import subprocess
        run = self.runner or subprocess.run
        if not _IFACE.fullmatch(iface):
            return MacResult(False, iface, "", "invalid interface")
        new = (mac or random_mac(mode=self.mode, rng=self.rng)).lower()
        if not _MAC.fullmatch(new):
            return MacResult(False, iface, new, "generated an invalid MAC")
        for argv in set_mac_commands(iface, new):
            try:
                res = run(argv, capture_output=True, text=True, timeout=10, check=False)
            except (OSError, subprocess.SubprocessError) as error:
                return MacResult(False, iface, new, f"{argv[2]} failed: {type(error).__name__}")
            if getattr(res, "returncode", 1) != 0:
                return MacResult(False, iface, new,
                                 f"'{' '.join(argv)}' failed: {(getattr(res, 'stderr', '') or '').strip()[:80]}")
        return MacResult(True, iface, new, f"{iface} now presents {new} ({self.mode})")
