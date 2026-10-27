"""Phone UI over Bluetooth PAN — the dashboard with no network, no cables.

The web dashboard is already responsive and viewer-rendered, so it IS the phone
UI — the missing piece is reaching it in the field with no Wi-Fi and no USB. This
brings the Pi up as a Bluetooth PAN NAP (network access point): pair the phone
once, tether over Bluetooth, and browse to the dashboard at a fixed address. The
phone gets the full picture (the new Operations tab included) over a link that
doesn't touch the air you're capturing on.

Glass-box and DRY-RUN by default: `plan()` prints the exact bring-up commands and
the URL the phone will hit; `up(apply=True)` runs them (needs a Bluetooth adapter
+ root). The plan/readiness/URL logic is pure and runner-injected, so it's tested
with no radio; the live bring-up is honestly NEEDS_HARDWARE until an adapter is
attached.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional

DEFAULT_ADAPTER = "hci0"
DEFAULT_IFACE = "pan0"
DEFAULT_IP = "172.20.10.1"
DEFAULT_CIDR = "172.20.10.0/24"
DEFAULT_PORT = 8080


@dataclass(frozen=True)
class PanPlan:
    adapter: str
    iface: str
    ip: str
    cidr: str
    port: int
    steps: List[str]
    dashboard_url: str

    def to_dict(self) -> dict:
        return {"adapter": self.adapter, "iface": self.iface, "ip": self.ip,
                "cidr": self.cidr, "port": self.port, "steps": list(self.steps),
                "dashboard_url": self.dashboard_url}


def dashboard_url(ip: str = DEFAULT_IP, port: int = DEFAULT_PORT) -> str:
    return f"http://{ip}:{port}/"


def bring_up_plan(*, adapter: str = DEFAULT_ADAPTER, iface: str = DEFAULT_IFACE,
                  ip: str = DEFAULT_IP, cidr: str = DEFAULT_CIDR,
                  port: int = DEFAULT_PORT) -> PanPlan:
    """The exact, glass-box commands to stand the Pi up as a Bluetooth NAP and
    serve the dashboard on the PAN subnet. Nothing is run — this just describes it."""
    prefix = cidr.split("/")[-1] if "/" in cidr else "24"
    lo, hi = _dhcp_range(ip)
    steps = [
        f"bluetoothctl --timeout 1 power on",
        f"bluetoothctl --timeout 1 discoverable on",
        f"bluetoothctl --timeout 1 pairable on",
        f"bt-network -s nap {iface}",                       # register the NAP server
        f"ip addr add {ip}/{prefix} dev {iface}",
        f"ip link set {iface} up",
        f"dnsmasq --interface={iface} --bind-interfaces "
        f"--dhcp-range={lo},{hi},1h",                       # hand the phone an address
        f"redux web --port {port} --bind-scope lan",        # the dashboard, on the PAN
    ]
    return PanPlan(adapter=adapter, iface=iface, ip=ip, cidr=cidr, port=port,
                   steps=steps, dashboard_url=dashboard_url(ip, port))


def _dhcp_range(ip: str) -> tuple:
    """A small DHCP pool in the PAN's /24, just above the Pi's own address."""
    parts = ip.split(".")
    if len(parts) == 4 and parts[3].isdigit():
        base = ".".join(parts[:3])
        return f"{base}.10", f"{base}.50"
    return "172.20.10.10", "172.20.10.50"


@dataclass
class PanBringup:
    """Plan + (on explicit apply) execute the Bluetooth PAN bring-up."""
    adapter: str = DEFAULT_ADAPTER
    iface: str = DEFAULT_IFACE
    ip: str = DEFAULT_IP
    cidr: str = DEFAULT_CIDR
    port: int = DEFAULT_PORT
    runner: Optional[Callable] = None

    def plan(self) -> PanPlan:
        return bring_up_plan(adapter=self.adapter, iface=self.iface, ip=self.ip,
                             cidr=self.cidr, port=self.port)

    def readiness(self, *, adapter_present: bool) -> tuple:
        if not adapter_present:
            return "needs-hardware", f"software ready — attach a Bluetooth adapter ({self.adapter})"
        return "ready", "Bluetooth adapter present"

    def up(self, *, apply: bool = False, adapter_present: bool = True,
           runner: Optional[Callable] = None) -> dict:
        """DRY-RUN unless apply=True. Needs a Bluetooth adapter present."""
        plan = self.plan()
        if not apply:
            return {"applied": False, "plan": plan.to_dict(),
                    "dashboard_url": plan.dashboard_url,
                    "note": "DRY RUN — pass apply=True (CLI: --apply) to bring the PAN up"}
        if not adapter_present:
            return {"applied": False, "dashboard_url": plan.dashboard_url,
                    "note": f"refused: no Bluetooth adapter ({self.adapter}) present"}
        run = runner or self.runner or subprocess.run
        results = []
        for cmd in plan.steps:
            try:
                r = run(cmd.split(), capture_output=True, text=True, check=False)
                ok = getattr(r, "returncode", 1) == 0
            except (OSError, subprocess.SubprocessError) as e:
                ok, r = False, None
                results.append({"cmd": cmd, "ok": False, "error": type(e).__name__})
                continue
            results.append({"cmd": cmd, "ok": ok})
        return {"applied": True, "results": results, "dashboard_url": plan.dashboard_url,
                "note": f"PAN up on {self.iface}; pair your phone, then browse {plan.dashboard_url}"}
