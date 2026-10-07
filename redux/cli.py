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
    from .dex import build_dex
    from .geo import SightingStore
    store = SightingStore(args.store) if args.store else SightingStore()
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
    al.add_argument("--bssid", action="append"); al.add_argument("--ssid", action="append"); al.add_argument("--cidr", action="append")
    sc.set_defaults(func=cmd_scope)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
