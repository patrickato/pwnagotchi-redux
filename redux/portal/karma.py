"""Probe harvesting + AIMED karma planning.

When a client can't find a network it knows, it shouts for it — a probe request
naming an SSID from its preferred-network list (PNL). Harvesting those probes is
pure passive recon and tells you exactly what each nearby device will
auto-join. The "karma" response is to advertise one of those SSIDs so the client
associates to you (feeding the evil-portal / capture).

Two halves, two postures — and this is the important line:

* **Harvesting is passive** — just listening to probe events. It runs in any
  mode, FIELD included, because it transmits nothing.
* **Advertising is firing**, and AGENTS.md forbids *indiscriminate* beacon spam
  (it can't be aimed and hits bystanders). So karma here is deliberately
  **aimed**: the plan advertises only SSIDs that **in-scope** clients asked for,
  refuses SSIDs whose only requesters are out of scope, and is capped — it never
  blasts a whole harvested PNL at the whole room. That keeps it a targeted
  response to devices you're authorized to assess, not a broadcast.

Everything is glass-box: the plan explains, per SSID, who asked for it and why
it's in or out. Real data only — SSIDs come from observed probes, never
invented.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _norm_mac(mac: str) -> str:
    return (mac or "").strip().lower()


@dataclass
class ProbeHarvester:
    """Builds a per-client PNL from observed probe requests. In-memory with an
    optional JSON persist so the CLI can read what the daemon harvested."""
    path: Optional[Path] = None
    now: object = time.time
    # client_mac -> {ssid: count}
    _pnl: Dict[str, Dict[str, int]] = field(default_factory=dict)
    _last: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if self.path is not None:
            self.path = Path(self.path)
            self._load()

    # --- intake ------------------------------------------------------------ #

    def note(self, client: str, ssid: str, *, now: Optional[float] = None) -> bool:
        """Record one probe (client asked for ssid). Ignores empty/broadcast
        probes (an empty SSID is a wildcard, not a named network)."""
        c = _norm_mac(client)
        s = (ssid or "").strip()
        if not c or not s:
            return False
        t = (self.now() if callable(self.now) else time.time()) if now is None else now
        self._pnl.setdefault(c, {})
        self._pnl[c][s] = self._pnl[c].get(s, 0) + 1
        self._last[c] = t
        self._save()
        return True

    def observe(self, em) -> None:
        """Bus subscriber: harvest from `client.probe` events (passive)."""
        p = getattr(em, "payload", {}) or {}
        ev = p.get("event")
        if getattr(ev, "type", "") != "client.probe":
            return
        data = getattr(ev, "data", {}) or {}
        client = (data.get("from") or data.get("client") or data.get("mac")
                  or data.get("station") or data.get("address") or "")
        ssid = data.get("essid") or data.get("ssid") or ""
        self.note(client, ssid)

    # --- queries ----------------------------------------------------------- #

    def clients(self) -> List[dict]:
        """Per-client PNL, most-recently-seen first."""
        out = []
        for c, ssids in self._pnl.items():
            out.append({"client": c, "ssids": sorted(ssids),
                        "probes": sum(ssids.values()),
                        "last_seen": self._last.get(c, 0.0)})
        return sorted(out, key=lambda r: r["last_seen"], reverse=True)

    def ssids(self) -> List[dict]:
        """SSID -> which clients asked for it, by popularity (client count)."""
        agg: Dict[str, set] = {}
        for c, ssids in self._pnl.items():
            for s in ssids:
                agg.setdefault(s, set()).add(c)
        rows = [{"ssid": s, "clients": sorted(cs), "client_count": len(cs)}
                for s, cs in agg.items()]
        return sorted(rows, key=lambda r: r["client_count"], reverse=True)

    def pnl(self, client: str) -> List[str]:
        return sorted(self._pnl.get(_norm_mac(client), {}))

    # --- persistence ------------------------------------------------------- #

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return
        self._pnl = {k: dict(v) for k, v in raw.get("pnl", {}).items()}
        self._last = {k: float(v) for k, v in raw.get("last", {}).items()}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps({"pnl": self._pnl, "last": self._last},
                                      sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


@dataclass(frozen=True)
class KarmaTarget:
    ssid: str
    clients: Tuple[str, ...]      # in-scope clients that asked for it
    reason: str


@dataclass(frozen=True)
class KarmaPlan:
    armed: bool                   # True only when scope/lab authorize advertising
    advertise: Tuple[KarmaTarget, ...]
    skipped: Tuple[dict, ...]     # {ssid, reason} for SSIDs refused (off-scope)
    reason: str

    def to_dict(self) -> dict:
        return {
            "armed": self.armed,
            "advertise": [{"ssid": t.ssid, "clients": list(t.clients), "reason": t.reason}
                          for t in self.advertise],
            "skipped": list(self.skipped),
            "reason": self.reason,
        }

    @property
    def ssid_list(self) -> List[str]:
        """The targeted SSIDs to arm the responder/AP with."""
        return [t.ssid for t in self.advertise]


# a sane cap so an "aim" can never degrade into blasting the whole room
MAX_ADVERTISE = 32


def karma_plan(harvester: ProbeHarvester, scope, *, lab_mode: Optional[bool] = None,
               max_advertise: int = MAX_ADVERTISE) -> KarmaPlan:
    """Build an AIMED karma advertise plan from harvested probes + the Scope.

    An SSID is advertised only if at least one **in-scope** client probed for it
    (a client is in scope when ``scope.permits(bssid=client)`` — lab mode
    approves on sight). SSIDs wanted only by out-of-scope clients are refused
    with a reason, so bystanders are never targeted. The result is capped.
    """
    lab = bool(getattr(scope, "lab_mode", False)) if lab_mode is None else lab_mode
    advertise: List[KarmaTarget] = []
    skipped: List[dict] = []
    for row in harvester.ssids():
        ssid = row["ssid"]
        in_scope = [c for c in row["clients"] if scope.permits(bssid=c)]
        if not in_scope:
            skipped.append({"ssid": ssid,
                            "reason": f"refused: only out-of-scope clients probed for {ssid!r} "
                                      f"({row['client_count']}) — not aiming at bystanders"})
            continue
        advertise.append(KarmaTarget(
            ssid=ssid, clients=tuple(in_scope),
            reason=f"advertise {ssid!r} — requested by {len(in_scope)} in-scope client(s)"))
        if len(advertise) >= max_advertise:
            break

    if not advertise:
        reason = "nothing to advertise: no in-scope client has probed for a named network yet"
        armed = False
    elif lab:
        reason = f"LAB — {len(advertise)} aimed SSID(s) ready to advertise to in-scope clients"
        armed = True
    elif advertise:
        # scope has in-scope clients but not lab mode -> advisory, needs arming
        reason = (f"{len(advertise)} aimed SSID(s) planned; FIELD mode is advisory — "
                  "switch to LAB (or arm explicitly) to advertise")
        armed = False
    return KarmaPlan(armed=armed, advertise=tuple(advertise),
                     skipped=tuple(skipped), reason=reason)
