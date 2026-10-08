"""Augur — the whole platform assembled on one signal bus.

This is the capstone: it wires every subsystem together through the `SignalBus`
so they work as one legible system. One object to construct, drive, and query.

    bus  ← the hub
    Supervisor(bus)        decides radios/intent, drives bettercap, emits events
    Narrator  (bus)        voices reasons/alerts as the creature
    Brain     (bus)        advises intent from rolling activity + power
    DetectEngine           fed from driver events (via Supervisor.pump)
    SightingStore          geo-tags each event into SpatialDB

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
from ..face import face_for
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


class Augur:
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
        sentinel = getattr(self, "_sentinel", None)
        if sentinel is not None:
            for a in alerts:
                sentinel.observe_alert(a)
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
        # capture engines as CAPTURE_HANDSHAKE providers (AngryOxide preferred when present)
        from ..crack.capture import AngryOxideProvider, register_capture_providers
        register_capture_providers(
            g,
            angryoxide_present=AngryOxideProvider().available(),
            bettercap_present=getattr(self.supervisor, "driver", None) is not None,
        )
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
        """A headless, glass-box self-diagnosis built from live state. Adds a
        capture-engine probe (over the live graph's CAPTURE_HANDSHAKE providers) on
        top of the built-ins, so a box with no capture engine present can't read
        clean."""
        from .doctor import BUILTIN_PROBES, probe_capture_engine
        return Doctor(probes=list(BUILTIN_PROBES) + [probe_capture_engine]).report(
            self._doctor_inputs())

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
            },
            # declared, not applied here: the detector set is a preference this
            # persona expresses; pruning the live engine is not enforced from here,
            # so it's reported honestly as a declaration rather than a change.
            "declares": {"detectors": p.detectors,
                         "note": "detector set is a declared preference (not enforced here)"},
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

    # --- CSI sensing (the radio as a motion sensor) ------------------------ #

    def enable_sense(self, *, window: int = 16, sensitivity: float = 5.0,
                     vacant_after: int = 30):
        """Turn on CSI sensing. Returns the SenseEngine so a collector can feed it
        frames (and calibrate it against a quiet room first)."""
        from ..sense import SenseEngine
        self._sense = SenseEngine.create(window=window, sensitivity=sensitivity,
                                         vacant_after=vacant_after)
        return self._sense

    def sense(self):
        return getattr(self, "_sense", None)

    def observe_csi(self, frame) -> Optional[dict]:
        """Feed one CSI frame to the sense engine (enabling it on first use). If a
        Sentinel is armed, a motion reading is routed to it as an alert."""
        eng = self.sense() or self.enable_sense()
        out = eng.observe(frame)
        sentinel = getattr(self, "_sentinel", None)
        if sentinel is not None and eng._last is not None:
            sentinel.observe_motion(eng._last)
        return out

    def eap_plan(self, ssid: str, *, authorized: bool = False, iface: str = "wlan1") -> dict:
        """Plan a Scope-aimed, posture-gated WPA-Enterprise EAP harvest. Honors
        offense_enabled() and requires the SSID to be in Scope + authorized=True."""
        from ..eap import EapHarvester, EapConfig
        h = EapHarvester(config=EapConfig(iface=iface))
        plan = h.plan(self.scope, ssid, authorized=authorized, active=self.offense_enabled())
        out = plan.to_dict()
        out["engine_present"] = h.available()
        return out

    # --- sentinel (deploy-and-watch guardian) ------------------------------ #

    def enable_sentinel(self, notifier=None, *, armed: bool = False, min_severity="warn"):
        """Turn on Sentinel mode. Detector alerts from pump() and CSI motion are
        routed to it; it dispatches glass-box alerts via `notifier` (LoRa/log)."""
        from ..sentinel import Sentinel, Severity, CollectingNotifier
        sev = Severity(min_severity) if not isinstance(min_severity, Severity) else min_severity
        self._sentinel = Sentinel(notifier=notifier or CollectingNotifier(),
                                  min_severity=sev, armed=armed)
        return self._sentinel

    def sentinel(self):
        return getattr(self, "_sentinel", None)

    def sentinel_status(self) -> dict:
        s = self.sentinel()
        return s.status() if s is not None else {"enabled": False}

    # --- mesh scope-sync (bind the swarm to THIS box's Scope) --------------- #

    def enable_scope_sync(self, key, node_id: str = "node"):
        """Bind a ScopeSync to this box's own central Scope, so arming here emits
        signed deltas to the swarm and received deltas merge into the real Scope
        (not a throwaway). Returns the ScopeSync."""
        from ..mesh import ScopeSync
        k = key if isinstance(key, (bytes, bytearray)) else str(key).encode()
        self._scope_sync = ScopeSync(self.scope, bytes(k), node_id=node_id)
        return self._scope_sync

    def scope_sync(self):
        return getattr(self, "_scope_sync", None)

    # --- autonomous kill-chain operator ------------------------------------ #

    def operator(self):
        """An Operator bound to this box's live Scope, posture, and capabilities —
        so its gates reflect the real device (capture engine present or not, etc.)."""
        from ..operator import Operator
        g = self.capability_graph()
        caps = {Cap.CAPTURE_HANDSHAKE.value: g.explain(Cap.CAPTURE_HANDSHAKE)["available"]}
        return Operator(self.scope, offense_enabled=self.offense_enabled(), caps=caps)

    def campaign_plan(self, targets) -> list:
        """Dry-run plan of the kill-chain for `targets` — glass-box, nothing runs."""
        return [s.to_dict() for s in self.operator().plan(targets)]

    def run_campaign(self, targets, executors, *, recon=None) -> dict:
        """Execute the kill-chain via injected executors; the action log feeds the
        engagement report (out-of-scope steps are gated out, never run)."""
        return self.operator().run(targets, executors, recon=recon)

    def sense_status(self) -> dict:
        eng = self.sense()
        return eng.status() if eng is not None else {
            "available": False, "reason": "CSI sensing not enabled"}

    def engagement_report(self, engagement: str, operator: str, actions, *,
                          range_report=None, sanitize: bool = False) -> dict:
        """Build a chain-of-authorization report from this box's live Scope + the
        recorded actions (+ the Dex summary as findings). Out-of-scope actions are
        flagged, not hidden — see redux.report."""
        from ..report import build_report
        try:
            dex_summary = self.dex().summary
        except Exception:
            dex_summary = None
        return build_report(engagement, operator, self.scope, actions,
                            dex_summary=dex_summary, range_report=range_report,
                            sanitize=sanitize)

    # --- capture engine selection (bettercap + AngryOxide) ----------------- #

    def capture_plan(self, *, iface: str = "", prefer: str = "auto") -> dict:
        """Pick the capture engine and build a Scope-aimed, posture-correct plan,
        glass-box. Honors offense_enabled() (a detection-only persona → passive
        --notransmit) and refuses an empty scope rather than sweeping broadly."""
        from ..crack.capture import AngryOxideProvider, BettercapProvider, AngryOxideConfig
        active = self.offense_enabled()
        from ..crack.capture import select_capture_provider
        ao = AngryOxideProvider(config=AngryOxideConfig(iface=iface or "wlan1"))
        bc = BettercapProvider(driver=getattr(self.supervisor, "driver", None))
        order = [ao, bc] if prefer in ("auto", "angryoxide") else [bc, ao]
        prov, why = select_capture_provider(order)
        if prov is None:
            return {"selected_engine": None, "runnable": False, "offense_enabled": active,
                    "reason": "no capture engine available (no AngryOxide binary, no bettercap driver)"}
        plan = prov.plan(self.scope, active=active, iface=iface)
        out = plan.to_dict()
        out["selected_engine"] = prov.name
        out["offense_enabled"] = active
        return out

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

    def ingest_frames(self, frames) -> dict:
        """Feed raw 802.11 frames (an iterable of (bytes, ts) or (bytes, ts, radiotap))
        through the capture tap: probe-requests re-identify devices across MAC
        randomization in the Dex, deauth/disassoc become events for the flood
        detectors. Returns a glass-box summary. (Live monitor capture is
        needs-hardware; this takes frames from any source.)"""
        from ..captap import CaptureTap
        tap = CaptureTap()
        for item in frames:
            buf, ts = item[0], item[1]
            rt = item[2] if len(item) > 2 else False
            tap.feed(buf, radiotap=rt, ts=ts)
        linker = tap.link()
        self._captap = tap
        return {"frames": tap.frames_seen, "deauth_events": len(tap.deauths),
                "device_identities": linker.summary(), "linker": linker}

    def tft_frame(self, face="status", width: int = 46, ascii: bool = False,
                  pack: str = "augur"):
        """Render the on-device TFT frame (list of rows) from live status. Lean,
        static (no animation), monochrome-safe — see redux.tft. `pack` selects the
        face look (augur/owl/fox)."""
        from ..tft import render, Face
        f = face if isinstance(face, Face) else Face(face)
        return render(self.status(), face=f, width=width, ascii=ascii, pack=pack)

    def current_position(self):
        """This device's own current GPS fix as {lat, lon}, or None when there is
        no fix — never a fabricated coordinate. Lets the dashboard show 'you' and
        draw a track as you move."""
        if self._position is None:
            return None
        fix = self._position()
        if not fix:
            return None
        return {"lat": fix[0], "lon": fix[1]}

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
        status = {
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
        if self.sense() is not None:
            status["sense"] = self.sense_status()
        if self.sentinel() is not None:
            status["sentinel"] = self.sentinel_status()
        status["capture_engine"] = self.capture_plan().get("selected_engine")
        # Augur's face: derived from the snapshot above, so the expression always
        # traces to real state (glass-box). Both glyph sets travel so the TFT can
        # pick ascii and the web can show the nice one.
        f = face_for(status)
        status["face"] = f.eyes()
        status["face_ascii"] = f.eyes(ascii=True)
        status["face_state"] = f.state.value
        status["face_reason"] = f.reason
        return status
