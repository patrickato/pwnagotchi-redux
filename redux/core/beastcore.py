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
    ):
        self.bus = bus or SignalBus()
        self.narrator = narrator or Narrator()
        self.brain = brain or Brain()
        self.engine = detect_engine or DetectEngine()
        self.store = store if store is not None else SightingStore()
        self._position = position_provider
        self.governor = governor or Governor()
        self._gov: Optional[GovDecision] = None
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
        return {
            "intent": self.supervisor.intent.value,
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
