"""redux CLI — the operator entrypoint that ties the platform together.

    redux status   [--intent I] [--onboard] [--adapter]        one-shot glass-box snapshot
    redux run      --replay events.json [--cycles N] ...        run pump cycles over a recorded session
    redux packs    --dir D list | enable NAME | disable NAME    manage Beast Packs

The hardware paths (live probe, a real BettercapDriver) are used when asked; the
default/testable paths (stub radios, a replay driver) need no Pi. Everything it
prints is real state — no decorative output.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

import os
import time

from .radio import Radio, Intent
from .core import Beastcore, Scope
from .engine import BettercapDriver, ReplayTransport, BettercapConfig
from .packs import PackManager, DependencyError

# The central authorized-target list every firing-capable function consults.
_DEFAULT_SCOPE_PATH = os.environ.get("REDUX_SCOPE", "/etc/pwnagotchi/scope.json")


# stub radios for the no-hardware paths (real enumeration is `probe()` on a Pi)
def _onboard() -> Radio:
    return Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                 driver="brcmfmac", onboard=True)


def _adapter() -> Radio:
    return Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True,
                 driver="mt76x2u", usb_gen=3, high_draw=True)


def _radios(args) -> List[Radio]:
    rs = []
    if getattr(args, "onboard", False):
        rs.append(_onboard())
    if getattr(args, "adapter", False):
        rs.append(_adapter())
    if not rs:
        rs.append(_onboard())  # a device always has the onboard radio
    return rs


def _build(args, driver=None) -> Beastcore:
    return Beastcore(radios=_radios(args), intent=Intent(args.intent), driver=driver)


# --- subcommands ------------------------------------------------------------- #

def cmd_status(args) -> int:
    bc = _build(args)
    print(json.dumps(bc.status(), indent=2))
    return 0


def cmd_run(args) -> int:
    driver = None
    if args.replay:
        events = json.loads(open(args.replay).read())
        driver = BettercapDriver(config=BettercapConfig(),
                                 transport=ReplayTransport(events=events))
    bc = _build(args, driver=driver)
    total_alerts = 0
    for _ in range(max(1, args.cycles)):
        total_alerts += len(bc.pump())
    status = bc.status()
    status["alerts_raised"] = total_alerts
    status["narration"] = [l.text for l in bc.narrator.lines(10)]
    print(json.dumps(status, indent=2))
    return 0


def cmd_web(args) -> int:
    from .web import serve
    driver = None
    if args.replay:
        events = json.loads(open(args.replay).read())
        driver = BettercapDriver(config=BettercapConfig(),
                                 transport=ReplayTransport(events=events))
    bc = _build(args, driver=driver)
    serve(bc, port=args.port, bind_scope=args.bind_scope)   # blocks until Ctrl-C
    return 0


def cmd_packs(args) -> int:
    mgr = PackManager(args.dir)
    try:
        if args.pack_cmd == "list":
            for p in mgr.list():
                mark = "*" if mgr.is_enabled(p.name) else " "
                print(f"[{mark}] {p.name} {p.version} ({p.kind}) {p.description}")
        elif args.pack_cmd == "enable":
            print("enabled:", ", ".join(mgr.enable(args.name)) or "(already enabled)")
        elif args.pack_cmd == "disable":
            print("disabled:", ", ".join(mgr.disable(args.name)) or "(not enabled)")
    except DependencyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


_DEFAULT_EXPEDITIONS_PATH = os.environ.get("REDUX_EXPEDITIONS", "/etc/pwnagotchi/expeditions.json")


def cmd_expedition(args) -> int:
    """Start/end a field session and print its Wrapped recap."""
    from .expedition import ExpeditionLog, wrapped
    from .geo import SightingStore
    log = ExpeditionLog.load(args.file)
    store = SightingStore(args.store) if args.store else SightingStore()
    if args.exp_cmd == "start":
        e = log.start(args.name); log.save(args.file)
        print(f"started expedition '{e.name}'")
    elif args.exp_cmd == "end":
        e = log.end()
        if e is None:
            print("no active expedition")
            return 0
        log.save(args.file)
        print(wrapped(store, e)["headline"])
    elif args.exp_cmd == "wrapped":
        e = log.current() or (log.expeditions[-1] if log.expeditions else None)
        if e is None:
            print("no expeditions yet")
            return 0
        w = wrapped(store, e)
        print(w["headline"])
        if w["by_kind"]:
            print("  by kind: " + ", ".join(f"{k}={v}" for k, v in sorted(w["by_kind"].items())))
    return 0


def cmd_dex(args) -> int:
    """Print the Field Dex — the recon ledger over recorded sightings."""
    from .dex import build_dex, link_store
    from .geo import SightingStore
    store = SightingStore(args.store) if args.store else SightingStore()
    if getattr(args, "identities", False):
        linker = link_store(store)
        s = linker.summary()
        print(f"Device identities: {s['identities']} total · {s['linkable']} linkable · "
              f"{s['reidentified']} re-identified across MAC randomization")
        if s["reidentified"]:
            print(f"  (re-id folded {s['macs_collapsed']} MACs into {s['reidentified']} devices)")
        for ident in linker.identities()[:args.top]:
            if ident.reidentified:
                print(f"  [DEVICE  str={ident.strength:.2f}] {ident.mac_count} MACs → one device: "
                      f"{', '.join(sorted(ident.macs)[:4])}"
                      + (" …" if ident.mac_count > 4 else "")
                      + (f"  PNL={sorted(ident.ssids)[:3]}" if ident.ssids else ""))
        if not s["reidentified"]:
            print("  (no cross-MAC links yet — needs PNL/IE capture; see docs/FINGERPRINT.md)")
        return 0
    dex = build_dex(store)
    s = dex.summary
    if not args.store:
        print("(no --store given; showing an empty in-memory dex)")
    print(f"Field Dex: {s['total']} known · {s['located']} located · {s['departed']} departed"
          + (f" · vendors {s['unique_vendors']}" if s['total'] else ""))
    if s["total"]:
        print("  by kind:  " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_kind"].items())))
        print("  by rarity: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_rarity"].items())))
        print("  rarest:")
        for e in dex.rarest(limit=args.top):
            print(f"    [{e.rarity.value:9}] {e.kind:4} {e.ssid or e.mac} — known {e.known_days:.0f}d")
        dep = dex.departed()
        if dep:
            print("  departed:")
            for e in dep[:args.top]:
                print(f"    {e.ssid or e.mac} — {e.reason}")
    return 0


def cmd_doctor(args) -> int:
    """Print the headless, glass-box self-diagnosis from live state."""
    bc = _build(args)
    rep = bc.doctor_report()
    print(f"redux doctor: {rep['label']}")
    for f in rep["findings"]:
        print(f"  [{f['status'].upper():8}] {f['area']}: {f['summary']}")
        if f["reason"]:
            print(f"             why: {f['reason']}")
        if f["remediation"]:
            print(f"             do:  {f['remediation']}")
    print(f"coverage: {rep['coverage']['reason']}")
    return 0


def cmd_campaign(args) -> int:
    """Autonomous kill-chain operator. `plan` shows the gated chain (glass-box, no
    exec); `demo` runs it with fake executors under a persona, then renders the
    engagement report so you see the chain-of-authorization it produced."""
    from .operator import Operator, Phase
    from .core import persona as _persona
    scope = Scope.load(args.scope_file)
    offense = _persona.get(args.persona).offense_available if args.persona else True
    targets = args.targets or ["00:11:22:33:44:55", "aa:bb:cc:dd:ee:ff"]

    if args.campaign_cmd == "plan":
        op = Operator(scope, offense_enabled=offense, caps={"capture.handshake": not args.no_engine})
        print(f"kill-chain plan (persona={args.persona or 'none'}, offense={offense}, "
              f"capture_engine={not args.no_engine}):")
        for s in op.plan(targets):
            print(f"  [{'ok   ' if s.allowed else 'BLOCK'}] {s.target:20} {s.phase.value:11} "
                  f"{s.action:18} — {s.reason}")
        return 0

    # demo: fake executors that succeed; capture engine assumed present
    op = Operator(scope, offense_enabled=offense, caps={"capture.handshake": True})
    def _ok(name):
        return lambda t: (True, f"{name} ok on {t}")
    execs = {Phase.CAPTURE: _ok("capture"), Phase.CRACK: _ok("crack"),
             Phase.PIVOT_SCAN: _ok("scan"), Phase.PIVOT_CRED: _ok("cred-test"),
             Phase.LOOT: _ok("loot")}
    out = op.run(targets, execs, recon=lambda: (True, "12 APs seen"))
    for s in out["steps"]:
        tag = "exec " if s["executed"] else ("BLOCK" if not s["allowed"] else "stop ")
        print(f"  [{tag}] {s['target']:20} {s['phase']:11} — {s.get('result') or s['reason']}")
    print(f"summary: {out['summary']['reason']}")
    from .report import build_report
    rep = build_report("autonomous campaign", "operator", scope, out["log"])
    print(f"report integrity: {rep['integrity']} ({rep['unauthorized_count']} out-of-scope)")
    return 0


def cmd_captap(args) -> int:
    """Raw-frame tap demo: SYNTHETIC probe requests from a phone that rotated its
    MAC (same PNL + IE fingerprint) plus a deauth burst, parsed and routed — shows
    cross-MAC re-identification and the staged deauth events. No radio."""
    from .captap import CaptureTap, build_probe_req, build_deauth
    print("redux captap demo — SYNTHETIC 802.11 frames (no radio)")
    tap = CaptureTap()
    # one phone, two randomized MACs, same PNL + same capability IEs
    ies = [(1, b"\x82\x84\x0b\x16"), (45, b"\x2d\x40\x00"), (127, b"\x00\x00\x00\x00\x00\x00\x40")]
    pnl = ["HomeLab-5G", "CoffeeShop", "PDX_Free_WiFi"]
    for mac in ("a2:11:11:11:11:11", "de:22:22:22:22:22"):
        for ssid in pnl:
            tap.feed(build_probe_req(mac, ssid, ies=ies), ts=100.0)
    # an unrelated device
    tap.feed(build_probe_req("b6:99:99:99:99:99", "Guest", ies=[(1, b"\x82\x84")]), ts=101.0)
    # a deauth burst against an AP
    for _ in range(5):
        tap.feed(build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66"), ts=102.0)
    linker = tap.link()
    s = linker.summary()
    print(f"  frames parsed: {tap.frames_seen}  |  deauth events staged: {len(tap.deauths)}")
    print(f"  device identities: {s['identities']} total, {s['reidentified']} re-identified across MAC")
    for i in linker.identities():
        if i.reidentified:
            print(f"    DEVICE (str {i.strength:.2f}): {i.mac_count} MACs -> one device, PNL={sorted(i.ssids)[:3]}")
    return 0


def cmd_tft(args) -> int:
    """Render the on-device TFT frame to the terminal (see it without hardware).
    --face plain|status, --ascii for a plain-terminal fallback."""
    from .tft import render, Face
    bc = _build(args)
    if args.persona:
        bc.apply_persona(args.persona)
    status = bc.status()
    if args.demo:
        status = {**status, "creature": "> hunting", "mood": "hunting",
                  "persona": "purple", "posture": "active", "capture_engine": "angryoxide",
                  "capture_iface": "wlan1", "sightings": 142, "recent_alerts": 3,
                  "governor": {"mode": "guarded"},
                  "sense": {"available": True, "sense": "motion", "occupancy": "occupied"},
                  "sentinel": {"armed": True, "dispatched": 4, "suppressed": 2,
                               "last": {"summary": "ble_skimmer", "severity": "critical"}},
                  "narration": ["ch6 dwell - 3 new APs, 1 PMKID elicited"]}
    for line in render(status, face=Face(args.face), ascii=args.ascii):
        print(line)
    return 0


def cmd_hunt(args) -> int:
    """Fox-hunt demo: a SIMULATED approach then retreat, showing warmer/colder, the
    proximity band, and (with GPS-tagged samples) a position estimate + bearing."""
    from .hunt import FoxHunt, HuntObservation
    print("redux hunt demo — SIMULATED RSSI gradient to a target (no radio)")
    fh = FoxHunt(target=args.target or "00:11:22:33:44:55")
    # approach (rising RSSI), with a couple of GPS fixes, then retreat
    seq = [(-88, None), (-82, (45.00, -93.00)), (-74, (45.001, -93.001)),
           (-66, (45.0015, -93.0012)), (-58, (45.002, -93.0015)),
           (-70, None), (-80, None), (-88, None)]
    st = None
    for rssi, pos in seq:
        obs = HuntObservation(rssi=rssi, lat=(pos[0] if pos else None),
                              lon=(pos[1] if pos else None))
        st = fh.observe(obs)
        line = f"  rssi {rssi:>4}  → {st.trend:7} [{st.band}]"
        if st.bearing_compass:
            line += f"  bearing {st.bearing_deg:.0f}deg {st.bearing_compass}"
        print(line)
    if st and st.estimate:
        e = st.estimate
        print(f"  estimate: {e['lat']:.5f},{e['lon']:.5f} +/-{e['error_radius_m']:.0f} m")
    return 0


def cmd_mesh(args) -> int:
    """Mesh demo: a 3-node swarm sharing one authorized Scope over the (simulated)
    LoRa lane — arm on one node, it converges everywhere; a forged delta (wrong
    swarm key) is rejected."""
    from .mesh import ScopeSync, LoopbackMesh, SignedMessage, sign
    key = b"swarm-shared-key"
    mesh = LoopbackMesh()
    nodes = {name: ScopeSync(Scope(), key, node_id=name) for name in ("alpha", "bravo", "charlie")}
    for n in nodes.values():
        mesh.register(n)
    print("redux mesh demo — 3-node swarm, one authenticated Scope (SIMULATED LoRa)")
    msg = nodes["alpha"].arm("00:11:22:33:44:55", "bssid", job="op1", now=100.0)
    mesh.broadcast(nodes["alpha"], msg, now=100.0)
    for name, n in nodes.items():
        print(f"  {name}: permits target? {n.scope.permits(bssid='00:11:22:33:44:55')}")
    # a stranger forges a delta with the wrong key
    forged = SignedMessage(payload={"op": "add", "kind": "ssid", "value": "EvilTarget",
                                    "job": "", "expires": None, "ts": 200.0, "origin": "attacker"},
                           sig=sign({"op": "add"}, b"WRONG-KEY"))
    ok, reason = nodes["bravo"].apply(forged, now=200.0)
    print(f"  forged delta from a stranger → applied={ok} ({reason})")
    print(f"  bravo permits the forged target? {nodes['bravo'].scope.permits(ssid='EvilTarget')}")
    return 0


def cmd_eap(args) -> int:
    """WPA-Enterprise EAP harvest. `plan` builds the Scope-aimed, posture-gated
    rogue-AP plan (refuses if the SSID isn't armed / posture passive / not
    authorized). `parse` turns a hostapd-wpe capture into crackable hashcat lines."""
    from .eap import EapHarvester, EapConfig, parse_hostapd_wpe
    if args.eap_cmd == "parse":
        creds = parse_hostapd_wpe(open(args.logfile).read())
        print(f"parsed {len(creds)} MSCHAPv2 credential(s) — crack with hashcat -m 5500:")
        for c in creds:
            print("  " + c.hashcat_5500())
        return 0
    # plan
    bc = _build(args)
    if args.persona:
        bc.apply_persona(args.persona)
    bc.scope = Scope.load(args.scope_file)
    plan = bc.eap_plan(args.ssid, authorized=args.authorized, iface=args.iface or "wlan1")
    print(f"EAP harvest for SSID '{args.ssid}': runnable={plan['runnable']} "
          f"(engine_present={plan['engine_present']}, offense={bc.offense_enabled()})")
    print(f"  {plan['reason']}")
    if plan["argv"]:
        print("  argv: " + " ".join(plan["argv"]))
    return 0


def cmd_sentinel(args) -> int:
    """Sentinel demo: a SIMULATED stream of detector alerts + CSI motion through an
    armed guardian, showing dispatch, de-dup, and armed-vs-home suppression."""
    from .sentinel import Sentinel, CollectingNotifier
    from .sense.csi import SenseReading, Sense
    try:
        from .detect import Alert, AlertKind
    except Exception:
        Alert = AlertKind = None
    print("redux sentinel demo — SIMULATED alert stream through an ARMED guardian")
    note = CollectingNotifier()
    s = Sentinel(notifier=note, armed=True)
    now = 1_793_000_000.0
    if Alert is not None:
        s.observe_alert(Alert(AlertKind.ROGUE_AP, "look-alike AP beaconing", now, "warning", bssid="aa:bb:cc:00:00:01"), now=now)
        s.observe_alert(Alert(AlertKind.DEAUTH_FLOOD, "deauth burst", now + 1, "warning", bssid="de:ad:00:00:00:01"), now=now + 1)
        s.observe_alert(Alert(AlertKind.DEAUTH_FLOOD, "deauth burst", now + 5, "warning", bssid="de:ad:00:00:00:01"), now=now + 5)  # deduped
        s.observe_alert(Alert(AlertKind.BLE_SKIMMER, "known skimmer markers", now + 8, "critical", bssid="11:22:33:44:55:66"), now=now + 8)
    # CSI motion while armed → critical; then a 'home' (disarmed) motion → suppressed
    s.observe_motion(SenseReading(Sense.MOTION, 9.9, 0.001, 42.0, "42sigma above quiet baseline"), now=now + 12)
    s.disarm()
    s.observe_motion(SenseReading(Sense.MOTION, 9.9, 0.001, 42.0, "motion (but you're home)"), now=now + 20)
    for e in note.sent:
        rep = f" x{e.repeat}" if e.repeat > 1 else ""
        print(f"  [{e.severity.value.upper():8}] {e.source:18} {e.summary}{rep} — {e.reason}")
    st = s.status()
    print(f"dispatched={st['dispatched']} suppressed={st['suppressed']} "
          f"(deduped repeats + the at-home motion) by_severity={st['by_severity']}")
    return 0


def cmd_report(args) -> int:
    """Engagement report. `demo` builds a sample (incl. one out-of-scope action to
    show the integrity flag); `build` reads a scope file + actions JSON
    ([{ts,action,target,reason,result}]) and renders Markdown."""
    from .report import EngagementAction, build_report, render_markdown
    from .frameworks import run_exercise, range_report
    if args.report_cmd == "demo":
        scope = Scope()
        scope.add("00:11:22:33:44:55", "bssid", job="acme-2026", label="client AP")
        scope.add("10.10.0.0/24", "cidr", job="acme-2026")
        acts = [
            EngagementAction(1_793_000_000.0, "wifi_recon", "00:11:22:33:44:55",
                             "survey the client's RF", "12 APs seen"),
            EngagementAction(1_793_000_100.0, "handshake_capture", "00:11:22:33:44:55",
                             "capture WPA2 handshake", "handshake captured"),
            EngagementAction(1_793_000_200.0, "net_scan", "10.10.0.0/24",
                             "enumerate the authorized subnet", "6 hosts up"),
            EngagementAction(1_793_000_300.0, "deauth", "aa:bb:cc:dd:ee:ff",
                             "stray test against an unarmed AP", "(should be flagged)"),
        ]
        cov = range_report([run_exercise("deauth", ["deauth-flood", "surveillance-sweep"]),
                            run_exercise("evil_twin", [])])
        rep = build_report("ACME Q2 wireless assessment", args.operator, scope, acts,
                           range_report=cov, sanitize=args.sanitize)
    else:
        scope = Scope.load(args.scope_file)
        raw = json.loads(open(args.actions).read())
        acts = [EngagementAction(float(e.get("ts", 0.0)), e.get("action", ""),
                                 e.get("target", ""), e.get("reason", ""), e.get("result", ""))
                for e in raw]
        rep = build_report(args.engagement, args.operator, scope, acts, sanitize=args.sanitize)
    md = render_markdown(rep)
    if getattr(args, "out", None):
        with open(args.out, "w") as f:
            f.write(md)
        print(f"wrote {args.out}  (integrity: {rep['integrity']}, "
              f"{rep['unauthorized_count']} out-of-scope)")
    else:
        print(md)
    return 0


def cmd_range(args) -> int:
    """Purple Range mode: `techniques` lists the ATT&CK→D3FEND map; `demo` runs a
    SIMULATED attack set and grades your detectors, naming the coverage gaps."""
    from .frameworks import attack_defend_pairs, run_exercise, range_report
    if args.range_cmd == "techniques":
        print("redux framework map — action: ATT&CK → D3FEND (PTES phase)")
        for p in attack_defend_pairs():
            det = f"  detectors: {', '.join(p['detects'])}" if p["detects"] else "  detectors: (none expected)"
            print(f"  {p['action']:17} {','.join(p['attack']):18} → {','.join(p['defend']):10} "
                  f"[{p['phase']}]")
            print(det)
        return 0
    # demo: a simulated engagement. The "fired" sets are illustrative, not real.
    print("redux range demo — SIMULATED attack set + detector results (not a live run)")
    scenario = [
        ("deauth", ["deauth-flood", "surveillance-sweep"]),   # fully caught
        ("evil_twin", ["rogue-AP"]),                           # partial: pineapple/karma missed
        ("captive_portal", []),                                # missed: nothing fired
        ("pmkid_capture", []),                                 # n/a: passive
        ("handshake_capture", ["handshake"]),                  # caught
    ]
    results = [run_exercise(a, fired) for a, fired in scenario]
    for r in results:
        print(f"  [{r.verdict.value.upper():9}] {r.label:28} {','.join(r.attack_ids):14} "
              f"→ {','.join(r.defend)}")
        if r.verdict.value in ("partial", "missed"):
            print(f"              {r.reason}")
    rep = range_report(results)
    cov = "n/a" if rep["coverage"] is None else f"{rep['coverage']*100:.0f}%"
    print(f"coverage: {rep['fully_detected']}/{rep['applicable']} applicable fully detected ({cov}); "
          f"{len(rep['gaps'])} gap(s)")
    return 0


def cmd_capture(args) -> int:
    """Show the capture plan: which engine is selected (AngryOxide scalpel when
    present, else bettercap), aimed only at the armed Scope, posture-correct."""
    bc = _build(args)
    if args.persona:
        bc.apply_persona(args.persona)
    # load the scope file so the plan reflects what's actually armed
    bc.scope = Scope.load(args.scope_file)
    plan = bc.capture_plan(iface=args.iface or "")
    print(f"capture engine (selected, by availability): {plan.get('selected_engine')}  "
          f"(offense_enabled={plan.get('offense_enabled')}, passive={plan.get('passive')})")
    print(f"  {plan.get('reason')}")
    if plan.get("targets"):
        print(f"  armed targets: {', '.join(plan['targets'])}")
    for k, v in (plan.get("outputs") or {}).items():
        print(f"  out[{k}]: {v}")
    # Always show the AngryOxide command it WOULD run (preview; no binary needed to print it)
    from .crack import AngryOxideProvider, AngryOxideConfig
    ao = AngryOxideProvider(config=AngryOxideConfig(iface=args.iface or "wlan1"))
    prev = ao.plan(bc.scope, active=bc.offense_enabled())
    print(f"\nAngryOxide preview (installed={ao.available()}):")
    if prev.runnable:
        print("  " + " ".join(prev.argv))
    else:
        print("  " + prev.reason)
    return 0


def cmd_sense(args) -> int:
    """CSI sensing: `demo` runs the pipeline on a SYNTHETIC quiet→motion→quiet
    sequence (no radio, clearly labelled), `replay` runs it over recorded frames.
    Honest: reports UNKNOWN until calibrated, never a default 'still'."""
    import math as _m
    from .sense import SenseEngine, CsiFrame

    def _jit(t: int, j: int) -> float:
        # deterministic, non-periodic small noise so a quiet room has real (tiny) std
        return 0.08 * (((t * 2654435761 + j * 40503) % 1000) / 1000.0 - 0.5)

    def _synth(kind: str, t: int, width: int = 32) -> CsiFrame:
        if kind == "quiet":
            amp = tuple(10.0 + _jit(t, j) for j in range(width))
        else:  # motion: large frame-to-frame swings across subcarriers
            amp = tuple(10.0 + 3.0 * _m.sin(0.80 * t + 0.50 * j) + _jit(t, j) for j in range(width))
        return CsiFrame(ts=float(t), amp=amp)

    eng = SenseEngine.create(window=args.window, sensitivity=args.sensitivity)

    if args.sense_cmd == "demo":
        print("redux sense demo — SYNTHETIC data (no radio); proves the pipeline, not the hardware")
        cal = [_synth("quiet", t) for t in range(args.window * 3)]
        info = eng.calibrate(cal)
        print(f"calibrated on quiet synthetic room: baseline={info['baseline_mean']:.4f} "
              f"(±{info['baseline_std']:.4f}, {info['samples']} samples)")
        timeline = [("quiet", 20), ("MOTION injected", 20), ("quiet", 20)]
        t = 0
        last = None
        for label, n in timeline:
            kind = "motion" if "MOTION" in label else "quiet"
            for _ in range(n):
                out = eng.observe(_synth(kind, t)); t += 1
                tag = f"{out['sense']}/{out['occupancy']}"
                if tag != last:
                    print(f"  t={t:3} [{label:16}] -> {out['sense']:7} occ={out['occupancy']:8} ({out['reason']})")
                    last = tag
        print("final:", eng.status()["occupancy"])
        return 0

    # replay
    events = json.loads(open(args.replay).read())
    frames = [CsiFrame(ts=float(e.get("ts", i)), amp=tuple(e["amp"]))
              for i, e in enumerate(events)]
    if args.calibrate > 0:
        eng.calibrate(frames[:args.calibrate])
        frames = frames[args.calibrate:]
    counts = {"motion": 0, "still": 0, "unknown": 0}
    for f in frames:
        out = eng.observe(f)
        counts[out["sense"]] = counts.get(out["sense"], 0) + 1
    print(f"replayed {len(frames)} frame(s): " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print("final occupancy:", eng.status()["occupancy"])
    return 0


def cmd_persona(args) -> int:
    """List personas, show one, or apply one to a freshly-built box (glass-box:
    prints exactly what the persona changes). 'one box, pick your hat.'"""
    from .core import persona as _persona
    if args.persona_cmd == "list":
        for p in _persona.summarize():
            off = "offense" if p["offense_available"] else "no-offense"
            print(f"  {p['name']:7} [{p['posture']:14} · {off:10}] {p['summary']}")
        return 0
    if args.persona_cmd == "show":
        p = _persona.get(args.name)
        print(f"{p.name}: {p.summary}")
        print(f"  intent={p.intent}  posture={p.posture.value}  offense_available={p.offense_available}")
        print(f"  detectors={p.detectors}  bind_scope={p.bind_scope}")
        if p.frameworks:
            print(f"  frameworks: {', '.join(p.frameworks)}")
        print(f"  why: {p.reason}")
        return 0
    # apply
    bc = _build(args)
    rec = bc.apply_persona(args.name)
    print(f"applied persona '{rec['persona']}' — {rec['summary']}")
    for k, v in rec["changed"].items():
        if isinstance(v, dict):
            print(f"  {k}: {v['from']} -> {v['to']}")
        else:
            print(f"  {k}: {v}")
    dec = rec.get("declares") or {}
    if dec:
        print(f"  declares: detectors={dec.get('detectors')} ({dec.get('note')})")
    print(f"  why: {rec['reason']}")
    return 0


def cmd_ghost(args) -> int:
    """Produce a sanitized 'ghost' of a recorded session — real MACs/SSIDs
    pseudonymized and location dropped, timing/structure preserved — so it can be
    replayed or shared without leaking real recon data. Plays via --replay."""
    from .replay import sanitize_file
    stats = sanitize_file(args.infile, args.outfile, seed=args.seed, keep_oui=not args.no_keep_oui)
    print(f"ghosted {stats['events']} event(s) → {args.outfile}: "
          f"{stats['unique_macs']} identit(y/ies) and {stats['unique_names']} name(s) pseudonymized, "
          f"locations dropped")
    print(f"replay it with:  redux web --replay {args.outfile}   (or: redux run --replay ...)")
    return 0


def cmd_post(args) -> int:
    """Run the boot-POST and render it as the device would at power-up.

    Exit code mirrors the verdict so an init script can gate on it:
    0 = READY, 1 = DEGRADED, 2 = HALT."""
    from .core.post import PowerOnSelfTest
    bc = _build(args)
    pst = PowerOnSelfTest.standard(bc._doctor_inputs())
    results = pst.results()
    if args.tft:
        print("\n".join(pst.tft_frame(results, width=args.width)))
    else:
        print(pst.console(results))
    return {"ready": 0, "degraded": 1, "halt": 2}[pst.verdict(results).value]


def cmd_scope(args) -> int:
    """Manage the central authorized-target Scope — the one list every firing
    function consults. Edits are saved atomically back to the scope file."""
    path = args.file
    scope = Scope.load(path)
    if args.scope_cmd == "list":
        s = scope.summary()
        print(f"scope: {s['active']} active / {s['total']} total"
              + (f", {s['expired']} expired" if s["expired"] else "")
              + (f" | jobs: {', '.join(s['jobs'])}" if s["jobs"] else "")
              + ("  (EMPTY — nothing authorized)" if s["empty"] else ""))
        now = time.time()
        for e in scope.entries:
            state = "active" if e.active(now) else "EXPIRED"
            exp = "" if e.expires is None else f" exp {time.strftime('%Y-%m-%d', time.gmtime(e.expires))}"
            job = f" [{e.job}]" if e.job else ""
            lab = f" — {e.label}" if e.label else ""
            print(f"  {state:7} {e.kind:5} {e.value}{job}{exp}{lab}")
        return 0
    # mutating subcommands
    if args.scope_cmd == "add":
        exp = (time.time() + args.expires_days * 86400) if args.expires_days else None
        e = scope.add(args.target, kind=args.kind, label=args.label or "", job=args.job or "", expires=exp)
        scope.save(path)
        print(f"armed: {e.kind} {e.value}" + (f" [{e.job}]" if e.job else "") + f"  (scope now has {len(scope.active_entries())} active)")
    elif args.scope_cmd == "remove":
        n = scope.remove(args.target, job=args.job)
        scope.save(path)
        print(f"removed {n} entr{'y' if n == 1 else 'ies'}")
    elif args.scope_cmd == "clear":
        n = scope.clear(job=args.job)
        scope.save(path)
        print(f"cleared {n} entr{'y' if n == 1 else 'ies'}" + (f" from job '{args.job}'" if args.job else " (whole scope)"))
    elif args.scope_cmd == "import":
        with open(args.listfile) as f:
            n = scope.bulk_load(f.read(), job=args.job or "")
        scope.save(path)
        print(f"imported {n} target(s)" + (f" into job '{args.job}'" if args.job else "") + f"; scope now has {len(scope.active_entries())} active")
    elif args.scope_cmd == "arm-lab":
        if args.auto:
            n, proposals = scope.arm_lab_auto()
            for p in proposals:
                mark = "armed " if p.accept else "skip  "
                print(f"  {mark} {p.kind:5} {p.value} — {p.reason}")
            if not proposals:
                print("  (nothing detected — are `ip`/`iw` available on this host?)")
            scope.save(path)
            print(f"lab armed (auto): {n} of your own target(s) pre-authorized; "
                  f"scope now has {len(scope.active_entries())} active")
        else:
            n = scope.arm_lab(bssids=args.bssid or [], ssids=args.ssid or [], cidrs=args.cidr or [])
            scope.save(path)
            print(f"lab armed: {n} target(s) pre-authorized (no expiry); scope now has {len(scope.active_entries())} active")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="redux", description="redux field-OS control")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_radio_flags(sp):
        sp.add_argument("--onboard", action="store_true", help="include the onboard radio")
        sp.add_argument("--adapter", action="store_true", help="include a USB adapter (Alfa-class)")
        sp.add_argument("--intent", default="recon", choices=[i.value for i in Intent])

    st = sub.add_parser("status", help="print a glass-box status snapshot")
    add_radio_flags(st); st.set_defaults(func=cmd_status)

    rn = sub.add_parser("run", help="run pump cycles (optionally over a recorded session)")
    add_radio_flags(rn)
    rn.add_argument("--replay", help="path to a recorded bettercap events JSON")
    rn.add_argument("--cycles", type=int, default=1)
    rn.set_defaults(func=cmd_run)

    wb = sub.add_parser("web", help="serve the glass-box web dashboard")
    add_radio_flags(wb)
    wb.add_argument("--port", type=int, default=8080)
    wb.add_argument("--bind-scope", default="localhost", choices=["localhost", "lan", "tailscale", "auto"])
    wb.add_argument("--replay", help="path to a recorded bettercap events JSON")
    wb.set_defaults(func=cmd_web)

    pk = sub.add_parser("packs", help="manage Beast Packs")
    pk.add_argument("--dir", required=True, help="packs directory")
    psub = pk.add_subparsers(dest="pack_cmd", required=True)
    psub.add_parser("list")
    en = psub.add_parser("enable"); en.add_argument("name")
    di = psub.add_parser("disable"); di.add_argument("name")
    pk.set_defaults(func=cmd_packs)

    dr = sub.add_parser("doctor", help="headless glass-box self-diagnosis")
    add_radio_flags(dr); dr.set_defaults(func=cmd_doctor)

    ca = sub.add_parser("campaign", help="autonomous glass-box kill-chain operator")
    ca.add_argument("--persona", help="posture (red/purple = offense; blue/recon = detection-only)")
    ca.add_argument("--scope-file", default=_DEFAULT_SCOPE_PATH)
    casub = ca.add_subparsers(dest="campaign_cmd", required=True)
    cap_ = casub.add_parser("plan", help="dry-run the gated chain (nothing executes)")
    cap_.add_argument("--targets", nargs="*", help="targets to run the chain against")
    cap_.add_argument("--no-engine", action="store_true", help="plan as if no capture engine present")
    cad_ = casub.add_parser("demo", help="run the chain with fake executors + render the report")
    cad_.add_argument("--targets", nargs="*", help="targets to run the chain against")
    ca.set_defaults(func=cmd_campaign)

    ct = sub.add_parser("captap", help="raw 802.11 tap — probe/deauth parsing + cross-MAC re-id")
    ctsub = ct.add_subparsers(dest="captap_cmd", required=True)
    ctsub.add_parser("demo", help="synthetic frames: re-identify a phone across MAC rotation")
    ct.set_defaults(func=cmd_captap)

    tf = sub.add_parser("tft", help="render the on-device TFT frame to the terminal")
    add_radio_flags(tf)
    tf.add_argument("--face", default="status", choices=["plain", "status"])
    tf.add_argument("--persona", help="apply a persona first")
    tf.add_argument("--ascii", action="store_true", help="ascii fallback (no box/block glyphs)")
    tf.add_argument("--demo", action="store_true", help="populate with sample data")
    tf.set_defaults(func=cmd_tft)

    hu = sub.add_parser("hunt", help="RSSI-gradient fox-hunt (warmer/colder + bearing)")
    husub = hu.add_subparsers(dest="hunt_cmd", required=True)
    hud = husub.add_parser("demo", help="simulated approach/retreat to a target")
    hud.add_argument("--target", help="target BSSID/SSID to hunt")
    hu.set_defaults(func=cmd_hunt)

    me = sub.add_parser("mesh", help="off-grid swarm — authenticated distributed Scope sync")
    mesub = me.add_subparsers(dest="mesh_cmd", required=True)
    mesub.add_parser("demo", help="3-node swarm: propagate an arm + reject a forged delta")
    me.set_defaults(func=cmd_mesh)

    ep = sub.add_parser("eap", help="WPA-Enterprise EAP credential capture (scope+posture gated)")
    add_radio_flags(ep)
    ep.add_argument("--persona", help="apply a persona first (posture)")
    ep.add_argument("--scope-file", default=_DEFAULT_SCOPE_PATH)
    ep.add_argument("--iface")
    epsub = ep.add_subparsers(dest="eap_cmd", required=True)
    epp = epsub.add_parser("plan", help="build the gated rogue-AP plan")
    epp.add_argument("--ssid", required=True)
    epp.add_argument("--authorized", action="store_true", help="confirm this is an authorized test")
    epr = epsub.add_parser("parse", help="hostapd-wpe capture → crackable hashcat lines")
    epr.add_argument("logfile")
    ep.set_defaults(func=cmd_eap)

    sn = sub.add_parser("sentinel", help="deploy-and-watch guardian (blue/purple)")
    snsub = sn.add_subparsers(dest="sentinel_cmd", required=True)
    snsub.add_parser("demo", help="simulated alert stream through an armed guardian")
    sn.set_defaults(func=cmd_sentinel)

    rp = sub.add_parser("report", help="engagement report — chain of authorization, out-of-scope flagged")
    rp.add_argument("--operator", default="operator", help="who ran the engagement")
    rp.add_argument("--sanitize", action="store_true", help="pseudonymize identifiers for sharing")
    rp.add_argument("--out", help="write Markdown to this path instead of stdout")
    rpsub = rp.add_subparsers(dest="report_cmd", required=True)
    rpsub.add_parser("demo", help="build a sample report (includes an out-of-scope action)")
    rpb = rpsub.add_parser("build", help="build from a scope file + actions JSON")
    rpb.add_argument("--engagement", default="engagement")
    rpb.add_argument("--scope-file", default=_DEFAULT_SCOPE_PATH)
    rpb.add_argument("--actions", required=True, help="actions JSON: [{ts,action,target,reason,result}]")
    rp.set_defaults(func=cmd_report)

    rg = sub.add_parser("range", help="purple Range mode — grade your detectors vs ATT&CK (and D3FEND)")
    rgsub = rg.add_subparsers(dest="range_cmd", required=True)
    rgsub.add_parser("techniques", help="list the ATT&CK→D3FEND framework map")
    rgsub.add_parser("demo", help="run a simulated attack set and score detector coverage")
    rg.set_defaults(func=cmd_range)

    cap = sub.add_parser("capture", help="show the capture plan (engine selection, Scope-aimed)")
    add_radio_flags(cap)
    cap.add_argument("--iface", help="capture interface (default wlan1)")
    cap.add_argument("--persona", help="apply a persona first (affects passive/active posture)")
    cap.add_argument("--scope-file", default=_DEFAULT_SCOPE_PATH, help="scope store path")
    capsub = cap.add_subparsers(dest="capture_cmd", required=True)
    capsub.add_parser("plan", help="print the selected engine + argv, glass-box")
    cap.set_defaults(func=cmd_capture)

    se = sub.add_parser("sense", help="CSI sensing — the radio as a motion/presence sensor")
    se.add_argument("--window", type=int, default=16, help="sliding window size (frames)")
    se.add_argument("--sensitivity", type=float, default=5.0, help="z-score motion threshold")
    sesub = se.add_subparsers(dest="sense_cmd", required=True)
    sesub.add_parser("demo", help="run the pipeline on synthetic quiet→motion→quiet data")
    ser = sesub.add_parser("replay", help="run over recorded CSI frames (JSON: [{ts,amp[]}])")
    ser.add_argument("replay"); ser.add_argument("--calibrate", type=int, default=0,
                     help="use the first N frames as the quiet baseline")
    se.set_defaults(func=cmd_sense)

    pe = sub.add_parser("persona", help="one box, pick your hat (red/blue/purple/recon/mesh/sigint)")
    pesub = pe.add_subparsers(dest="persona_cmd", required=True)
    pesub.add_parser("list", help="list the built-in personas")
    pesh = pesub.add_parser("show", help="show one persona's config"); pesh.add_argument("name")
    pea = pesub.add_parser("apply", help="apply a persona and print what it changes")
    pea.add_argument("name"); add_radio_flags(pea)
    pe.set_defaults(func=cmd_persona)

    gh = sub.add_parser("ghost", help="sanitize a recorded session for safe replay/sharing")
    gh.add_argument("infile", help="a recorded bettercap events JSON")
    gh.add_argument("outfile", help="where to write the sanitized ghost")
    gh.add_argument("--seed", default="redux-ghost", help="pseudonymization seed (stable mapping)")
    gh.add_argument("--no-keep-oui", action="store_true",
                    help="synthesize locally-administered MACs instead of preserving vendor OUIs")
    gh.set_defaults(func=cmd_ghost)

    po = sub.add_parser("post", help="run the boot-POST (power-on self-test)")
    add_radio_flags(po)
    po.add_argument("--tft", action="store_true", help="render as the small TFT boot frame")
    po.add_argument("--width", type=int, default=40, help="TFT frame width in chars")
    po.set_defaults(func=cmd_post)

    ex = sub.add_parser("expedition", help="start/end a field session + Wrapped recap")
    ex.add_argument("--file", default=_DEFAULT_EXPEDITIONS_PATH, help="expeditions store path")
    ex.add_argument("--store", help="path to a sightings store (for the recap)")
    exsub = ex.add_subparsers(dest="exp_cmd", required=True)
    exstart = exsub.add_parser("start"); exstart.add_argument("name")
    exsub.add_parser("end")
    exsub.add_parser("wrapped")
    ex.set_defaults(func=cmd_expedition)

    dx = sub.add_parser("dex", help="the Field Dex — recon ledger over sightings")
    dx.add_argument("--store", help="path to a sightings store (SQLite)")
    dx.add_argument("--top", type=int, default=10, help="how many rarest/departed to show")
    dx.add_argument("--identities", action="store_true",
                    help="show device identities (re-identified across MAC randomization)")
    dx.set_defaults(func=cmd_dex)

    sc = sub.add_parser("scope", help="manage the central authorized-target list")
    sc.add_argument("--file", default=_DEFAULT_SCOPE_PATH, help="scope store path")
    scsub = sc.add_subparsers(dest="scope_cmd", required=True)
    scsub.add_parser("list", help="show the scope (active / expired / jobs)")
    sa = scsub.add_parser("add", help="authorize one target (BSSID / SSID / CIDR, auto-detected)")
    sa.add_argument("target")
    sa.add_argument("--kind", choices=["bssid", "ssid", "cidr"], help="override auto-detection")
    sa.add_argument("--job", help="group under a named engagement")
    sa.add_argument("--label", help="human note")
    sa.add_argument("--expires-days", type=float, default=0.0, help="auto-expire after N days")
    sr = scsub.add_parser("remove", help="remove a target"); sr.add_argument("target"); sr.add_argument("--job")
    scl = scsub.add_parser("clear", help="clear the whole scope, or one --job"); scl.add_argument("--job")
    si = scsub.add_parser("import", help="bulk-load targets from a list file (one per line)")
    si.add_argument("listfile"); si.add_argument("--job")
    al = scsub.add_parser("arm-lab", help="pre-authorize your own gear (no expiry)")
    al.add_argument("--auto", action="store_true",
                    help="auto-detect your own kit (own private subnet/radios/AP) and arm it, zero typing")
    al.add_argument("--bssid", action="append"); al.add_argument("--ssid", action="append"); al.add_argument("--cidr", action="append")
    sc.set_defaults(func=cmd_scope)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
