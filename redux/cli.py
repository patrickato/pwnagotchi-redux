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

from .radio import Radio, Intent
from .core import Beastcore
from .engine import BettercapDriver, ReplayTransport, BettercapConfig
from .packs import PackManager, DependencyError


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
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
