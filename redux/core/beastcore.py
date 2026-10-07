"""Beastcore — the whole platform assembled on one signal bus.

This is the capstone: it wires every subsystem together through the `SignalBus`
so they work as one legible system. One object to construct, drive, and query.

    bus  ← the hub
    Supervisor(bus)        decides radios/intent, drives bettercap, emits events
    Narrator  (bus)        voices reasons/alerts as the creature
    Brain     (bus)        advises intent from rolling activity + power
    DetectEngine           fed from driver events (via Supervisor.pump)
    SightingStore          geo-tags each event into BeastSpatialDB

Geo-tagging is honest: a sighting is stored with a real fix only when a
`position_provider` supplies one (live GPS at integration); otherwise the sighting
is still recorded, just without lat/lon. Nothing is invented.

Passive by construction — it observes, decides-advisory, and records. It never
fires; any deauth stays behind the driver's empty-by-default allowlist.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional

from ..radio import Intent
from ..detect import DetectEngine
from ..geo import SightingStore, Sighting
from .supervisor import Supervisor
from .narrator import Narrator
from .brain import Brain
from .governor import Governor, Reading, GovDecision
from .capabilities import Cap, Provider, CapabilityGraph
from .scope import Scope
from .doctor import Doctor, DoctorInputs
from .signals import SignalBus, Signal, connect_narrator


# driver event type -> sighting kind
_KIND = {"ap.new": "wifi", "ap.lost": "wifi", "client.new": "wifi",
         "ble.new": "ble", "ble.lost": "ble"}


class Beastcore:
    """Assembles and runs the full stack on one bus."""

    def __init__(
        self,
        radios=None,
        intent=Intent.RECON,
        driver=None,
        detect_engine: Optional[DetectEngine] = None,
        store: Optional[SightingStore] = None,
        narrator: Optional[Narrator] = None,
        brain: Optional[Brain] = None,
        bus: Optional[SignalBus] = None,
        position_provider: Optional[Callable[[], Optional[tuple]]] = None,
        governor: Optional[Governor] = None,
        scope: Optional[Scope] = None,
    ):
        self.bus = bus or SignalBus()
        self.narrator = narrator or Narrator()
        self.brain = brain or Brain()
        self.engine = detect_engine or DetectEngine()
        self.store = store if store is not None else SightingStore()
        self._position = position_provider
        self.governor = governor or Governor()
        self._gov: Optional[GovDecision] = None
        self.scope = scope if scope is not None else Scope()
        # SD-wear: buffer geo-tags and flush once per pump cycle (one batched
        # commit) instead of an fsync per RF event. _flush_cap bounds memory if a
        # single cycle sees a flood.
        self._sighting_buffer: List[Sighting] = []
        self._flush_cap = 512

        # consumers subscribe to the hub
        connect_narrator(self.bus, self.narrator)
        self.bus.on("*", self.brain.observe)
        self.bus.on(Signal.EVENT, self._geotag)

        # the supervisor drives the radios + bettercap and publishes to the hub
        self.supervisor = Supervisor(
            radios=radios, intent=intent, driver=driver,
            detect_engine=self.engine, bus=self.bus,
        )

    # --- geo-tagging (bus subscriber) -------------------------------------- #

    def _geotag(self, em) -> None:
        ev = em.payload.get("event")
        etype = getattr(ev, "type", "")
        kind = _KIND.get(etype)
        if kind is None:
            return
        data = getattr(ev, "data", {}) or {}
        mac = data.get("mac") or data.get("bssid") or data.get("address") or ""
        if not mac:
            return
        lat = lon = None
        if self._position is not None:
            fix = self._position()
            if fix:
                lat, lon = fix[0], fix[1]
        self._sighting_buffer.append(Sighting(
            kind=kind,
            mac=mac,
            ssid=data.get("essid") or data.get("hostname") or data.get("ssid") or "",
            lat=lat, lon=lon,
            rssi=data.get("rssi"),
            channel=data.get("channel"),
            ts=float(getattr(ev, "at", 0.0) or 0.0),
            provenance=f"bettercap:{getattr(ev, 'raw_tag', etype)}",
        ))
        if len(self._sighting_buffer) >= self._flush_cap:
            self.flush_sightings()   # safety valve within a very busy cycle

    def flush_sightings(self) -> int:
        """Write buffered sightings to the store in ONE batched transaction.
        Called once per pump cycle (and on checkpoint); returns rows flushed."""
        if not self._sighting_buffer:
            return 0
        batch, self._sighting_buffer = self._sighting_buffer, []
        self.store.insert_many(batch)
        return len(batch)

    # --- drive ------------------------------------------------------------- #

    def set_intent(self, intent) -> None:
        self.supervisor.set_intent(intent)

    def add_radio(self, radio) -> None:
        self.supervisor.add_radio(radio)

    def remove_radio(self, iface) -> None:
        self.supervisor.remove_radio(iface)

    def pump(self) -> list:
        """One cycle: drain driver events → detect + geo-tag + narrate + advise,
        then flush the cycle's sightings to the store in one batched write."""
        alerts = self.supervisor.pump()
        self.flush_sightings()
        return alerts

    def recommend(self):
        """The brain's current glass-box intent recommendation."""
        return self.brain.recommend(current=self.supervisor.intent)

    def observe_resources(self, reading: Reading, now: Optional[float] = None) -> GovDecision:
        """Feed real thermal/power/load readings to the Governor; returns its
        glass-box decision. A hardware collector calls this; the interval_scale
        it yields stretches periodic work (pump/sample/flush cadence) under load."""
        self._gov = self.governor.evaluate(reading, now if now is not None else time.time())
        return self._gov

    def govern_scale(self) -> float:
        """Current cadence multiplier (1.0 until real readings say to shed)."""
        return self._gov.interval_scale if self._gov else 1.0

    # --- observe ----------------------------------------------------------- #

    def capability_graph(self) -> CapabilityGraph:
        """Build a capability graph from the device's actual live state, so the
        Doctor (and the dashboard) can reason/explain over it. Honest: a radio
        role is 'present' only if a radio actually supports it."""
        g = CapabilityGraph()
        radios = list(getattr(self.supervisor, "radios", []) or [])
        mon = next((r for r in radios if getattr(r, "monitor", False)), None)
        inj = next((r for r in radios if getattr(r, "inject", False)), None)
        g.register(Provider.of(
            "wifi-monitor", provides=[Cap.RADIO_WIFI_MONITOR],
            present=mon is not None,
            reason=(f"{mon.iface} supports monitor" if mon else "no monitor-capable radio present")))
        g.register(Provider.of(
            "wifi-inject", provides=[Cap.RADIO_WIFI_INJECT],
            present=inj is not None,
            reason=(f"{inj.iface} supports injection" if inj else "no injection-capable radio present")))
        # location: the position provider, if it yields a fix
        fix = self._position() if self._position is not None else None
        g.register(Provider.of(
            "position", provides=[Cap.LOCATION_POSITION],
            present=fix is not None,
            reason=("a live position fix is available" if fix is not None
                    else "no position provider / no fix")))
        # consumers that depend on those capabilities (so blast-radius is real)
        g.register(Provider.of("capture", requires=[Cap.RADIO_WIFI_MONITOR], reason="handshake capture"))
        g.register(Provider.of("coverage-map", requires=[Cap.LOCATION_POSITION], reason="sighting map"))
        g.register(Provider.of("deauth-gate", requires=[Cap.RADIO_WIFI_MONITOR], reason="firing gate"))
        return g

    def _doctor_inputs(self) -> DoctorInputs:
        """The one read-only snapshot both the Doctor and the boot-POST reason
        over, so they never diverge into two health systems."""
        return DoctorInputs(
            graph=self.capability_graph(),
            governor=self._gov,
            scope=self.scope,
            detector_count=getattr(self.engine, "detector_count", None),
            sightings=self.store.count() if hasattr(self.store, "count") else None,
        )

    def doctor_report(self) -> dict:
        """A headless, glass-box self-diagnosis built from live state."""
        return Doctor().report(self._doctor_inputs())

    def boot_post(self, extra=()) -> dict:
        """The power-on self-test: the Doctor rendered as a gated, streaming boot
        checklist that cannot show READY unless the capture radio actually
        checked out this run. `extra` appends hardware probes the boot layer owns
        (TFT init, RTC, storage) — see redux.core.post.PowerOnSelfTest."""
        from .post import PowerOnSelfTest
        return PowerOnSelfTest.standard(self._doctor_inputs(), extra=extra).report()

    # --- personas (one box, pick your hat) --------------------------------- #

    def apply_persona(self, name: str) -> dict:
        """Reconfigure the box for a persona in one gesture. Sets the radio intent
        and records posture + exposure. Returns a glass-box record of what changed.

        Posture is an EXTRA gate, never a looser one: a detection-only persona
        turns offense off regardless of Scope; it can only tighten, never widen,
        what can fire. Scope still decides WHERE anything is aimed."""
        from . import persona as _persona
        from ..radio import Intent
        p = _persona.get(name)
        before = {
            "intent": self.supervisor.intent.value,
            "offense_enabled": self.offense_enabled(),
            "bind_scope": getattr(self, "_bind_scope", "localhost"),
        }
        self.supervisor.set_intent(Intent(p.intent))
        self._persona = p
        self._bind_scope = p.bind_scope
        return {
            "persona": p.name,
            "summary": p.summary,
            "reason": p.reason,
            "changed": {
                "intent": {"from": before["intent"], "to": p.intent},
                "offense_enabled": {"from": before["offense_enabled"], "to": self.offense_enabled()},
                "bind_scope": {"from": before["bind_scope"], "to": p.bind_scope},
                "detectors": p.detectors,
            },
        }

    def persona(self):
        """The applied persona, or None if the box is running unshaped (Scope-only
        governance, offense available)."""
        return getattr(self, "_persona", None)

    def offense_enabled(self) -> bool:
        """Whether firing-capable offense is on the table at all. With no persona
        applied, there is no extra gate (Scope alone governs). With one applied,
        its posture is honored — a detection-only persona hard-disables firing."""
        p = getattr(self, "_persona", None)
        return True if p is None else p.offense_available

    def dex(self):
        """The Field Dex: the recon ledger built from this device's sightings."""
        from ..dex import build_dex
        return build_dex(self.store)

    def start_expedition(self, name: str):
        """Begin a named field session (in-memory log; pass a path to persist)."""
        from ..expedition import ExpeditionLog
        if not hasattr(self, "_explog"):
            self._explog = ExpeditionLog()
        return self._explog.start(name)

    def end_expedition(self):
        from ..expedition import ExpeditionLog, wrapped
        if not hasattr(self, "_explog"):
            self._explog = ExpeditionLog()
        e = self._explog.end()
        return wrapped(self.store, e) if e is not None else None

    def located_sightings(self, limit: int = 500) -> list:
        """Recent sightings that carry a real GPS fix, newest first.

        Only sightings with both lat and lon are returned — never a fabricated
        coordinate. Touches the store, so (like status) it must be called from
        the thread that owns it. Empty when nothing has a fix yet.
        """
        if not hasattr(self.store, "query"):
            return []
        out = []
        for s in self.store.query(limit=limit):
            if s.lat is None or s.lon is None:
                continue
            out.append({
                "lat": s.lat, "lon": s.lon, "kind": s.kind,
                "ssid": s.ssid, "mac": s.mac, "ts": s.ts,
            })
        return out

    def status(self) -> dict:
        """A glass-box snapshot for a TFT / web view / log."""
        rec = self.recommend()
        p = self.persona()
        return {
            "intent": self.supervisor.intent.value,
            "persona": p.name if p else None,
            "posture": p.posture.value if p else None,
            "offense_enabled": self.offense_enabled(),
            "capture_iface": self.supervisor.capture_iface,
            "creature": self.narrator.tft(),
            "mood": self.narrator.mood.value,
            "recommendation": {"intent": rec.intent.value if rec.intent else None,
                               "reason": rec.reason, "confidence": rec.confidence},
            "sightings": self.store.count() if hasattr(self.store, "count") else None,
            "recent_alerts": len(self.bus.history(Signal.ALERT)),
            "governor": {
                "mode": self._gov.mode.value if self._gov else "full",
                "interval_scale": self.govern_scale(),
                "reason": self._gov.reason if self._gov else "FULL — no readings yet",
            },
        }
