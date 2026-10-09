"""redux CLI — the operator entrypoint that ties the platform together.

    redux status   [--intent I] [--onboard] [--adapter]        one-shot glass-box snapshot
    redux run      --replay events.json [--cycles N] ...        run pump cycles over a recorded session
    redux packs    --dir D list | enable NAME | disable NAME    manage Packs
    redux survey   --iface wlan1mon [--demo]                    passive AP situational-awareness sweep (read-only)
    redux selftest                                             on-device software battery (paste the report back)
    redux hwtest   --iface wlan1mon [--out FILE]                validation battery: software + synthetic detection + live passive

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
from .core import Augur, Scope
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


def _build(args, driver=None) -> Augur:
    return Augur(radios=_radios(args), intent=Intent(args.intent), driver=driver)


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


class ConfigError(Exception):
    """A --config path that's missing or unparseable; main() renders it cleanly."""


def _cfg(args):
    """Load the config named by --config, or None when not given. Config values are
    consulted ONLY when --config is passed; an explicit flag always wins. So every
    command's no-config behavior is exactly as before. A bad path raises ConfigError
    (clean message + exit 4 via main), never a raw traceback."""
    path = getattr(args, "config", None)
    if not path:
        return None
    from .config import AugurConfig
    try:
        return AugurConfig.load(path)
    except FileNotFoundError:
        raise ConfigError(f"config: {path} not found")
    except Exception as e:   # malformed TOML
        raise ConfigError(f"config: failed to parse {path}: {e}")


def cmd_web(args) -> int:
    from .web import serve
    cfg = _cfg(args)
    driver = None
    if args.replay:
        events = json.loads(open(args.replay).read())
        driver = BettercapDriver(config=BettercapConfig(),
                                 transport=ReplayTransport(events=events))
    bc = _build(args, driver=driver)
    bind_scope = args.bind_scope or (cfg.web.bind_scope if cfg else None) or "localhost"
    port = args.port if args.port is not None else (cfg.web.port if cfg else None) or 8080
    token = args.token if args.token is not None else ((cfg.web.token or None) if cfg else None)
    serve(bc, port=port, bind_scope=bind_scope, token=token)   # blocks until Ctrl-C
    return 0


def cmd_cache(args) -> int:
    from .geo.db import SightingStore, DEFAULT_DB_PATH
    cfg = _cfg(args)
    db = args.db or (cfg.cache.db_path if cfg else None) or DEFAULT_DB_PATH
    store = SightingStore(db)
    try:
        if args.cache_cmd == "stats":
            s = store.stats()
            span = ""
            if s["oldest_ts"] and s["newest_ts"]:
                span = f"  · span {(s['newest_ts'] - s['oldest_ts']) / 3600:.1f}h"
            print(f"cache: {s['count']} sightings  {s['by_kind']}{span}")
        elif args.cache_cmd == "prune":
            if args.older_than_days:
                older = args.older_than_days * 86400
            elif cfg and cfg.cache.retention_days:
                older = cfg.cache.retention_days * 86400
            else:
                older = None
            max_rows = args.max_rows if args.max_rows is not None else (cfg.cache.max_rows if cfg else None)
            n = store.prune(older_than=older, max_rows=max_rows)
            print(f"pruned {n} rows; {store.count()} remain")
            if args.vacuum:
                store.vacuum()
                print("vacuumed (pages reclaimed)")
        elif args.cache_cmd == "export":
            if getattr(args, "encrypt", False):
                from .vault import Vault, crypto_available, resolve_passphrase
                if not crypto_available():
                    print("cache export --encrypt: crypto extra not installed "
                          "(pip install 'pwnagotchi-redux[crypto]')")
                    return 3
                from pathlib import Path as _P
                rows = store.query(kind=args.kind)
                sealed = Vault(resolve_passphrase(confirm=True)).seal(
                    store.export_bytes(fmt=args.format, kind=args.kind))
                _P(args.out).write_bytes(sealed)   # plaintext never hits disk
                print(f"exported {len(rows)} rows -> {args.out} (sealed, {args.format})")
            else:
                n = store.export(args.out, fmt=args.format, kind=args.kind)
                print(f"exported {n} rows -> {args.out} ({args.format})")
    finally:
        store.close()
    return 0


def cmd_init(args) -> int:
    """First-run setup: write config.toml, generate a sealed swarm key (when the
    passphrase + crypto backend are present), and print the operator checklist.
    Idempotent — existing files are left alone unless --force. The one-gesture
    'flash → armed' path for the field operator."""
    import os
    from pathlib import Path as _P
    from .config import template, MARKER
    d = _P(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    cfg_path = d / "config.toml"
    created = []
    if cfg_path.exists() and not args.force:
        print(f"init: {cfg_path} exists — leaving it (use --force to overwrite)")
    else:
        text = template()
        if args.node_id:
            text = text.replace('node_id = "augur-01"', f'node_id = "{args.node_id}"')
        cfg_path.write_text(text)
        created.append(str(cfg_path))

    ks_path = d / "swarm.keys"
    from .vault import crypto_available
    note = ""
    if ks_path.exists() and not args.force:
        note = f"swarm keystore already present at {ks_path}"
    elif not crypto_available():
        note = f"swarm keystore NOT created — install the crypto extra, then: redux mesh key gen --store {ks_path}"
    elif not os.environ.get("AUGUR_PASSPHRASE"):
        note = f"swarm keystore NOT created — set AUGUR_PASSPHRASE, then: redux mesh key gen --store {ks_path}"
    else:
        from .mesh import SwarmKeyring, save as ks_save
        from .vault import resolve_passphrase
        ring = SwarmKeyring()
        k = ring.rotate(label=args.node_id or "lab")
        ks_save(ring, ks_path, resolve_passphrase())
        created.append(f"{ks_path} (swarm key {k.kid})")

    print("Augur first-run setup")
    for c in created:
        print(f"  created: {c}")
    if note:
        print(f"  note: {note}")
    print("next steps:")
    print(f"  1. edit {cfg_path} — fill every {MARKER} field (node_id; web token for an off-box bind)")
    print("  2. export AUGUR_PASSPHRASE=…   (at-rest encryption + the swarm keystore)")
    print(f"  3. arm your lab:   redux scope --file {_DEFAULT_SCOPE_PATH} arm-lab --cidr <your.lab.cidr>")
    print(f"       (that's the scope file the firing tools read by default; override with REDUX_SCOPE)")
    print(f"  4. validate:       redux config check --config {cfg_path}")
    return 0


def cmd_selftest(args) -> int:
    """On-device software battery + environment probe. Prints PASS/FAIL/SKIP you can
    paste back; exit 1 if anything FAILed. Physical tests are listed as manual steps."""
    from .selftest import run_selftest
    checks = run_selftest()
    npass = sum(c.status == "PASS" for c in checks)
    nfail = sum(c.status == "FAIL" for c in checks)
    nskip = sum(c.status == "SKIP" for c in checks)
    print("Augur selftest")
    for c in checks:
        print(f"  [{c.status:4}] {c.name:24} {c.detail}")
    print(f"summary: {npass} pass · {nfail} fail · {nskip} skip")
    print("manual (physical) tests — walk these with Claude; see docs/HARDWARE_VALIDATION.md:")
    print("  - handshake capture on YOUR OWN AP (bettercap, monitor mode)")
    print("  - redux captap live --iface <mon> --seconds 30   (+ a short deauth burst on YOUR OWN AP)")
    print("  - fox-hunt walk · EAP on your own enterprise SSID · mesh LoRa (2 nodes) · Governor under load")
    return 1 if nfail else 0


def cmd_hwtest(args) -> int:
    """One-command on-device validation battery (cleared/detection lane). Runs the
    software self-test, a synthetic end-to-end detection check, a live radio probe,
    boot-POST and self-diagnosis, and — with --iface — a live PASSIVE survey + monitor
    capture. Emits one structured report you can paste back or commit. Listen-only.
    The physical-stimulus tests (trigger a burst on your own AP, fox-hunt walk) are
    listed as manual steps — they need a human in the RF, not this harness."""
    import time
    from .selftest import run_selftest
    from .captap import live_source, capture_run, build_probe_req, build_deauth
    from .detect.engine import DetectEngine

    lines: List[str] = []

    def out(s: str = "") -> None:
        print(s)
        lines.append(s)

    out("=== redux hwtest — validation battery (cleared/detection lane, listen-only) ===")
    out(f"time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    out("\n[1] software self-test")
    checks = run_selftest()
    for c in checks:
        out(f"  [{c.status:4}] {c.name:24} {c.detail}")
    nfail = sum(c.status == "FAIL" for c in checks)

    out("\n[2] detection pipeline — synthetic frames, no radio (proves the chain)")
    det_ok = False
    try:
        ies = [(1, b"\x82\x84\x0b\x16"), (45, b"\x2d\x40\x00")]
        src = []
        for mac in ("a2:11:11:11:11:11", "de:22:22:22:22:22"):
            for ssid in ("HomeLab", "Cafe"):
                src.append((build_probe_req(mac, ssid, ies=ies), 10.0))
        for i in range(22):
            src.append((build_deauth("de:ad:00:00:00:01", "ff:ff:ff:ff:ff:ff", "11:22:33:44:55:66"),
                        20.0 + i * 0.1))
        tap, alerts = capture_run(iter(src), engine=DetectEngine())
        reid = tap.link().summary()["reidentified"]
        fired = "deauth_flood" in {a.kind.value for a in alerts}
        det_ok = reid >= 1 and fired
        out(f"  [{'PASS' if det_ok else 'FAIL'}] cross-MAC re-id={reid}, "
            f"deauth_flood={'fired' if fired else 'MISSING'}")
    except Exception as e:  # noqa: BLE001
        out(f"  [FAIL] {e!r}")

    out("\n[3] live radio — passive survey + capture")
    if not args.iface:
        out("  [SKIP] no --iface; on the Pi re-run: redux hwtest --iface <monitor iface> --seconds 30")
    else:
        try:
            tap, alerts = capture_run(live_source(args.iface), seconds=args.seconds,
                                      engine=DetectEngine())
            aps = tap.access_points()
            s = tap.link().summary()
            kinds = sorted({a.kind.value for a in alerts})
            out(f"  [PASS] {args.iface}: frames={tap.frames_seen}, APs={len(aps)}, "
                f"identities={s['identities']} ({s['reidentified']} re-id), alerts={kinds or 'none'}")
            for a in aps[:10]:
                ch = a.channel if a.channel is not None else "-"
                out(f"        ch{ch} {a.bssid} x{a.frames} {a.ssid or '<hidden>'}")
        except RuntimeError as e:
            out(f"  [SKIP] live capture unavailable: {e}")

    # [4]-[6]: the rest of the on-device cleared checks, folded in so one run covers them.
    try:
        from .radio import probe as _probe
        live_radios = _probe()
    except Exception:  # noqa: BLE001
        live_radios = []
    out("\n[4] radio probe — live iw enumeration")
    if live_radios:
        for r in live_radios:
            out(f"  [PASS] {r.iface}: bands={sorted(r.bands)} monitor={r.monitor} "
                f"inject={r.inject} driver={r.driver or '?'}")
    else:
        out("  [SKIP] no radios enumerated (needs iw + a real adapter)")

    try:
        bc = Augur(radios=live_radios or _radios(args), intent=Intent.RECON)
    except Exception as e:  # noqa: BLE001
        bc = None
        out(f"\n[5/6] framework diagnosis — [SKIP] could not build core: {e!r}")

    if bc is not None:
        out("\n[5] boot-POST")
        try:
            from .core.post import PowerOnSelfTest
            pst = PowerOnSelfTest.standard(bc._doctor_inputs())
            out(f"  verdict: {pst.verdict(pst.results()).value}")
        except Exception as e:  # noqa: BLE001
            out(f"  [SKIP] {e!r}")

        out("\n[6] self-diagnosis (doctor)")
        try:
            rep = bc.doctor_report()
            out(f"  {rep['label']} · coverage: {rep['coverage']['reason']}")
            for f in rep["findings"]:
                out(f"    [{f['status'].upper()}] {f['area']}: {f['summary']}")
        except Exception as e:  # noqa: BLE001
            out(f"  [SKIP] {e!r}")

    out("\n[manual] physical-stimulus tests (a human in the RF, not this harness):")
    out("  - handshake capture on YOUR OWN AP · a short deauth burst on YOUR OWN AP for the flood detector")
    out("  - fox-hunt walk (RSSI gradient) · see docs/HARDWARE_VALIDATION.md")
    out("\n=== end report ===")

    if args.out:
        try:
            with open(args.out, "w") as f:
                f.write("\n".join(lines) + "\n")
            print(f"(report written to {args.out})")
        except OSError as e:
            print(f"(could not write {args.out}: {e})")
    return 1 if nfail or not det_ok else 0


def cmd_config(args) -> int:
    from pathlib import Path as _P
    import json as _json
    from .config import AugurConfig, template, DEFAULT_CONFIG_PATH
    if args.config_cmd == "init":
        out = _P(args.out or DEFAULT_CONFIG_PATH)
        if out.exists() and not args.force:
            print(f"config: {out} exists (use --force to overwrite)")
            return 4
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(template())
        print(f"wrote config template → {out}  (edit the >>> USER INPUT REQUIRED <<< fields)")
        return 0
    try:
        cfg = AugurConfig.load(args.config) if args.config else AugurConfig()
    except FileNotFoundError:
        print(f"config: {args.config} not found")
        return 4
    except Exception as e:   # malformed TOML
        print(f"config: failed to parse {args.config}: {e}")
        return 4
    if args.config_cmd == "show":
        print(_json.dumps(cfg.to_display(), indent=2))
    elif args.config_cmd == "check":
        problems = cfg.validate()
        if not problems:
            print("config OK — no problems")
            return 0
        print(f"config: {len(problems)} problem(s):")
        for pr in problems:
            print(f"  - {pr}")
        return 4
    return 0


def cmd_vault(args) -> int:
    from .vault import Vault, crypto_available, resolve_passphrase, BadVaultData
    if not crypto_available():
        print("vault: at-rest encryption unavailable — install the crypto extra:")
        print("       pip install 'pwnagotchi-redux[crypto]'")
        return 3
    try:
        v = Vault(resolve_passphrase(confirm=(args.vault_cmd == "seal")))
        if args.vault_cmd == "seal":
            n = v.seal_file(args.inp, args.out)
            print(f"sealed {args.inp} -> {args.out} ({n} bytes)")
        else:
            n = v.unseal_file(args.inp, args.out)
            print(f"opened {args.inp} -> {args.out} ({n} bytes)")
    except BadVaultData as e:
        print(f"vault: {e}")
        return 4
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
    if getattr(args, "captap_cmd", "demo") == "live":
        return _captap_live(args)
    return _captap_demo(args)


def _captap_live(args) -> int:
    """Live monitor-mode capture → the same re-id + flood-detector chain as the demo,
    but from a real radio. Honest about hardware absence (no monitor iface / no
    raw-socket privileges → a clear message, not fabricated frames). Manages its own
    receiver channel: --channel N parks it, --hop sweeps 1/6/11 (no separate `iw`)."""
    import subprocess
    import threading
    from .captap import live_source, capture_run
    from .detect.engine import DetectEngine

    def _setch(ch):
        try:
            subprocess.run(["iw", "dev", args.iface, "set", "channel", str(ch)],
                           capture_output=True, text=True, timeout=5)
        except Exception:
            pass  # best-effort; if it can't tune, capture_run reports honestly

    stop = threading.Event()
    if args.hop:
        chans = [1, 6, 11]
        print(f"redux captap live — {args.iface}, hopping {chans} (≤{args.seconds}s)")

        def _hop():
            i = 0
            while not stop.is_set():
                _setch(chans[i % len(chans)])
                i += 1
                stop.wait(2.0)
        threading.Thread(target=_hop, daemon=True).start()
    elif args.channel:
        _setch(args.channel)
        print(f"redux captap live — {args.iface} ch{args.channel} (≤{args.seconds}s)")
    else:
        print(f"redux captap live — monitor capture on {args.iface} "
              f"(≤{args.max or '∞'} frames / ≤{args.seconds}s)")
    try:
        tap, alerts = capture_run(live_source(args.iface), max_frames=args.max,
                                  seconds=args.seconds, engine=DetectEngine())
    except RuntimeError as e:
        stop.set()
        print(f"  unavailable: {e}")
        return 3
    finally:
        stop.set()
    s = tap.link().summary()
    print(f"  frames parsed: {tap.frames_seen}  |  deauth events: {len(tap.deauths)}")
    print(f"  device identities: {s['identities']} total, {s['reidentified']} re-identified across MAC")
    kinds = sorted({a.kind.value for a in alerts})
    print(f"  alerts fired: {kinds or 'none'}")
    return 0


def _captap_demo(args) -> int:
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
    # a deauth burst against an AP — enough to trip the flood detector
    for i in range(22):
        tap.feed(build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66"),
                 ts=102.0 + i * 0.1)
    linker = tap.link()
    s = linker.summary()
    print(f"  frames parsed: {tap.frames_seen}  |  deauth events staged: {len(tap.deauths)}")
    print(f"  device identities: {s['identities']} total, {s['reidentified']} re-identified across MAC")
    for i in linker.identities():
        if i.reidentified:
            print(f"    DEVICE (str {i.strength:.2f}): {i.mac_count} MACs -> one device, PNL={sorted(i.ssids)[:3]}")
    # the closed loop: captured frames through the real detect engine
    from .detect.engine import DetectEngine
    alerts = DetectEngine().feed_many(tap.detect_frames())
    kinds = sorted({a.kind.value for a in alerts})
    print(f"  captured frames → detectors: {len(tap.detect_frames())} frames, alerts fired: {kinds or 'none'}")
    return 0


def cmd_survey(args) -> int:
    """Passive situational-awareness sweep: listen only (never transmit) and inventory
    the APs in earshot — BSSID, channel, SSID, how many beacons heard — plus any
    rogue-AP / flood alerts the detectors raise during the sweep. Read-only: it does
    not target, arm, populate, or transmit anything. --demo runs on synthetic beacons."""
    from .captap import CaptureTap, capture_run, build_beacon
    from .detect.engine import DetectEngine

    alerts = []
    if getattr(args, "demo", False):
        print("redux survey — SYNTHETIC beacons (no radio)")
        tap = CaptureTap()
        demo = [("aa:bb:cc:11:22:33", "HomeLab", 6),
                ("de:ad:be:ef:00:01", "CoffeeShop", 11),
                ("12:34:56:78:9a:bc", "", 1)]          # a hidden-SSID AP
        for bssid, ssid, ch in demo:
            for _ in range(5 if ssid else 2):
                tap.feed(build_beacon(bssid, ssid, ch), ts=1.0)
    else:
        if not args.iface:
            print("  survey needs --iface <monitor interface> (or --demo for synthetic)")
            return 2
        import subprocess
        import threading
        from .captap import live_source
        stop = threading.Event()

        def _setch(ch):
            try:
                subprocess.run(["iw", "dev", args.iface, "set", "channel", str(ch)],
                               capture_output=True, text=True, timeout=5)
            except Exception:
                pass  # best-effort; capture still reports honestly if it can't tune

        if args.channel:
            _setch(args.channel)
            print(f"redux survey — passive sweep on {args.iface} ch{args.channel} (≤{args.seconds}s)")
        else:
            chans = [1, 6, 11]
            print(f"redux survey — passive sweep on {args.iface}, hopping {chans} (≤{args.seconds}s)")

            def _hop():
                i = 0
                while not stop.is_set():
                    _setch(chans[i % len(chans)])
                    i += 1
                    stop.wait(2.0)
            threading.Thread(target=_hop, daemon=True).start()
        print("  (listening only — no frames transmitted)")
        try:
            tap, alerts = capture_run(live_source(args.iface), seconds=args.seconds,
                                      max_frames=args.max, engine=DetectEngine())
        except RuntimeError as e:
            stop.set()
            print(f"  unavailable: {e}")
            return 3
        finally:
            stop.set()

    aps = tap.access_points()
    print(f"  access points seen: {len(aps)}")
    if aps:
        print(f"    {'CH':>3}  {'BSSID':<17}  {'FRAMES':>6}  SSID")
        for a in aps:
            ch = str(a.channel) if a.channel is not None else "-"
            print(f"    {ch:>3}  {a.bssid:<17}  {a.frames:>6}  {a.ssid or '<hidden>'}")
    kinds = sorted({al.kind.value for al in alerts})
    print(f"  alerts during sweep: {kinds or 'none'}")
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
    cfg = _cfg(args)
    pack = args.pack or (cfg.tft.face_pack if cfg else None) or "augur"
    for line in render(status, face=Face(args.face), ascii=args.ascii, pack=pack):
        print(line)
    return 0


def cmd_hunt(args) -> int:
    if getattr(args, "hunt_cmd", "demo") == "live":
        return _hunt_live(args)
    return _hunt_demo(args)


def _hunt_live(args) -> int:
    """Live fox-hunt: park on the target's channel, capture passively, and feed the
    real dBm signal of frames from/to the target BSSID into the warmer/colder
    gradient as you move. Listen-only — transmits nothing. Needs monitor mode + root."""
    import subprocess
    import time as _t
    from .captap import live_source, parse_dot11, rssi_from_radiotap
    from .hunt import FoxHunt, HuntObservation

    target = args.target.lower()
    if args.channel:
        try:
            subprocess.run(["iw", "dev", args.iface, "set", "channel", str(args.channel)],
                           capture_output=True, text=True, timeout=5)
        except Exception:
            pass  # best-effort; if it can't tune you'll just hear nothing and we say so
    fh = FoxHunt(target=target)
    print(f"redux hunt live — tracking {target} on {args.iface}"
          + (f" ch{args.channel}" if args.channel else "")
          + f" (walk toward/away; ≤{args.seconds:.0f}s, listen-only)")
    heard = 0
    t0 = _t.time()
    try:
        for raw, ts, rt in live_source(args.iface):
            if _t.time() - t0 >= args.seconds:
                break
            f = parse_dot11(raw, radiotap=rt, ts=ts)
            if f is None:
                continue
            if target not in (f.bssid.lower(), f.src.lower(), f.dst.lower()):
                continue
            rssi = rssi_from_radiotap(raw) if rt else None
            if rssi is None:
                continue
            st = fh.observe(HuntObservation(rssi=rssi))
            heard += 1
            print(f"  rssi {rssi:>4}  → {st.trend:7} [{st.band}]")
    except RuntimeError as e:
        print(f"  unavailable: {e}")
        return 3
    except KeyboardInterrupt:
        pass
    if not heard:
        print("  heard nothing from that target — wrong channel, out of range, "
              "or the radio's radiotap carries no signal field")
    return 0


def _hunt_demo(args) -> int:
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


def cmd_mesh_key(args) -> int:
    """Swarm-key lifecycle, persisted to a sealed keystore (needs the crypto
    extra). Passphrase via AUGUR_PASSPHRASE or prompt — never argv."""
    from pathlib import Path as _P
    import time as _t
    from .mesh import SwarmKeyring, export_token, save as ks_save, load as ks_load
    from .vault import crypto_available, resolve_passphrase, BadVaultData
    if not crypto_available():
        print("mesh key: the sealed keystore needs the crypto extra — "
              "pip install 'pwnagotchi-redux[crypto]'")
        return 3
    cfg = _cfg(args)
    args.store = args.store or (cfg.mesh.keystore if cfg else None)
    if not args.store:
        print("mesh key: a keystore path is required (--store, or [mesh].keystore via --config)")
        return 2
    ttl = (args.ttl_days * 86400) if getattr(args, "ttl_days", None) else None
    try:
        if args.key_cmd == "gen":
            ring = SwarmKeyring()
            k = ring.rotate(label=args.label, ttl=ttl)
            ks_save(ring, args.store, resolve_passphrase(confirm=True))
            print(f"generated swarm key {k.kid}" + (f" [{k.label}]" if k.label else "")
                  + f" → sealed {args.store}")
        elif args.key_cmd == "rotate":
            pw = resolve_passphrase()
            ring = ks_load(args.store, pw)
            k = ring.rotate(label=args.label, ttl=ttl)
            ks_save(ring, args.store, pw)
            print(f"rotated → current {k.kid}; {len(ring.keys) - 1} prior key(s) held for grace")
        elif args.key_cmd == "show":
            ring = ks_load(args.store, resolve_passphrase())
            now = _t.time()
            if not ring.keys:
                print("(empty keyring)")
                return 0
            for i, k in enumerate(ring.keys):
                role = "current" if i == 0 else "grace"
                exp = ("never" if k.expires is None
                       else "EXPIRED" if k.is_expired(now) else f"~{int((k.expires - now) / 3600)}h")
                print(f"  {k.kid}  {role:7} {('[' + k.label + '] ') if k.label else ''}expires {exp}")
        elif args.key_cmd == "export":
            ring = ks_load(args.store, resolve_passphrase())
            k = ring.active()
            if k is None:
                print("no active key to export")
                return 4
            print(export_token(k))   # key material — hand to a trusted peer via QR/LoRa
        elif args.key_cmd == "import":
            pw = resolve_passphrase()
            tok = _P(args.token_file).read_text().strip()
            ring = ks_load(args.store, pw)
            k = ring.import_token(tok)
            ks_save(ring, args.store, pw)
            print(f"imported swarm key {k.kid} → {args.store}")
    except BadVaultData as e:
        print(f"mesh key: {e}")
        return 4
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"mesh key: {e}")
        return 4
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


def cmd_pipeline(args) -> int:
    """Run or inspect the independent capture artifact workflow."""
    from .crack.ingest import Settings, CaptureIngestor, read_summary
    if args.pipeline_cmd == "status":
        print(json.dumps(read_summary(Settings.load(args.config).database), indent=2))
        return 0
    if args.pipeline_cmd == "ingest":
        with CaptureIngestor(Settings.load(args.config)) as worker:
            print(json.dumps(worker.scan(), indent=2))
        return 0
    if args.pipeline_cmd == "prune":
        from .crack.retention import retention_report
        report = retention_report(Settings.load(args.config),
                                  apply=args.apply,
                                  older_than_days=args.older_than_days,
                                  max_files=args.max_files)
        print(json.dumps(report, indent=2))
        return 0
    if args.pipeline_cmd == "audit":
        from .crack.audit import AuditSettings, AuditWorker
        from .core.scope import Scope
        cfg = AuditSettings.load(args.config)
        if not cfg.enabled:
            print(json.dumps({"status": "disabled", "reason": "audit opt-in is off"}))
            return 0
        with AuditWorker(cfg, Scope.load(str(cfg.scope_file))) as worker:
            print(json.dumps(worker.once(), indent=2))
        return 0
    raise ValueError("unknown pipeline command")


def cmd_live(args) -> int:
    """Inspect actual on-device state without creating synthetic radio records."""
    if args.live_cmd == "doctor":
        # Only consult the loopback dashboard owned by the running supervisor.
        # A missing listener is UNKNOWN, not a synthetic healthy Doctor report.
        from urllib.request import urlopen
        from urllib.error import URLError
        port = args.port
        if not 1 <= port <= 65535:
            print("live doctor: localhost port must be 1-65535")
            return 4
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("live health response too large")
            snapshot = json.loads(raw)
            report = snapshot.get("doctor") if isinstance(snapshot, dict) else None
            if (not isinstance(report, dict) or not isinstance(report.get("findings"), list)
                    or report.get("overall") not in
                    {"ok", "attention", "degraded", "action", "unknown"}):
                raise ValueError("no verified Doctor report in runtime snapshot")
        except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
            print(f"live doctor: unavailable ({type(error).__name__}); "
                  "check redux-live.service or use 'redux live status'")
            return 3

        def safe(value):
            # Avoid embedding untrusted terminal control codes in diagnostics.
            return "".join(ch if (ch.isprintable() and ch not in "\\x1b\\x7f")
                           else "?" for ch in str(value))[:240]
        print(f"redux live doctor: {safe(report.get('label', report['overall']))}")
        for finding in report["findings"]:
            if not isinstance(finding, dict):
                continue
            print(f"  [{safe(finding.get('status', 'unknown')).upper():8}] "
                  f"{safe(finding.get('area', 'unknown'))}: "
                  f"{safe(finding.get('summary', ''))}")
            if finding.get("reason"):
                print(f"             why: {safe(finding['reason'])}")
            if finding.get("remediation"):
                print(f"             do:  {safe(finding['remediation'])}")
        coverage = report.get("coverage")
        if isinstance(coverage, dict):
            print(f"coverage: {safe(coverage.get('reason', 'not reported'))}")
        else:
            print("coverage: UNKNOWN — not reported")
        return 0

    from pathlib import Path
    snapshot = Path(args.file)
    if snapshot.is_symlink() or not snapshot.is_file():
        print(f"live: no verified runtime snapshot at {snapshot}")
        return 3
    try:
        if snapshot.stat().st_size > 65536:
            raise ValueError("runtime snapshot exceeds expected maximum")
        data = json.loads(snapshot.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("state"), str):
            raise ValueError("runtime snapshot is not a status record")
    except (OSError, ValueError) as error:
        print(f"live: unable to parse runtime snapshot: {error}")
        return 4
    print(json.dumps(data, indent=2, sort_keys=True))
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
    wb.add_argument("--port", type=int, default=None, help="default 8080 (or [web].port from --config)")
    wb.add_argument("--bind-scope", default=None, choices=["localhost", "lan", "tailscale", "auto"],
                    help="default localhost (or [web].bind_scope from --config)")
    wb.add_argument("--token", default=None,
                    help="dashboard access token; required (auto-generated if omitted) for any off-box bind")
    wb.add_argument("--config", default=None, help="config.toml to read [web] defaults from")
    wb.add_argument("--replay", help="path to a recorded bettercap events JSON")
    wb.set_defaults(func=cmd_web)

    ca = sub.add_parser("cache", help="inspect / prune / export the sighting Cache")
    casub = ca.add_subparsers(dest="cache_cmd", required=True)

    def _db(p):
        p.add_argument("--db", default=None, help="sightings DB path (default: on-device path or [cache].db_path)")
        p.add_argument("--config", default=None, help="config.toml to read [cache] defaults from")

    _db(casub.add_parser("stats", help="counts, per-kind, and time span"))
    cap = casub.add_parser("prune", help="delete old/excess rows (bound growth)")
    _db(cap)
    cap.add_argument("--older-than-days", type=float, default=None, help="drop rows older than N days")
    cap.add_argument("--max-rows", type=int, default=None, help="keep only the most-recent N by time")
    cap.add_argument("--vacuum", action="store_true", help="reclaim pages after prune (heavy; off hot path)")
    cae = casub.add_parser("export", help="dump rows to a file (jsonl/csv)")
    _db(cae)
    cae.add_argument("--out", required=True, help="output file path")
    cae.add_argument("--format", default="jsonl", choices=["jsonl", "csv"])
    cae.add_argument("--kind", default=None, help="filter by kind (wifi/ble/sdr)")
    cae.add_argument("--encrypt", action="store_true",
                     help="seal the export at rest (needs crypto extra; passphrase via AUGUR_PASSPHRASE or prompt)")
    ca.set_defaults(func=cmd_cache)

    va = sub.add_parser("vault", help="seal/open captured data at rest (needs the crypto extra)")
    vasub = va.add_subparsers(dest="vault_cmd", required=True)
    for _name, _help in (("seal", "encrypt a file"), ("open", "decrypt a file")):
        vp = vasub.add_parser(_name, help=_help)
        vp.add_argument("--in", dest="inp", required=True, help="input file")
        vp.add_argument("--out", required=True, help="output file")
    va.set_defaults(func=cmd_vault)

    cf = sub.add_parser("config", help="unified operator config.toml: init/show/check")
    cfsub = cf.add_subparsers(dest="config_cmd", required=True)
    ci = cfsub.add_parser("init", help="write a config.toml template with USER-INPUT markers")
    ci.add_argument("--out", default=None, help="output path (default /etc/redux/config.toml)")
    ci.add_argument("--force", action="store_true", help="overwrite if it exists")
    cs = cfsub.add_parser("show", help="print the resolved config (token masked)")
    cs.add_argument("--config", default=None, help="config path (default: built-in defaults)")
    cc = cfsub.add_parser("check", help="validate a config and list problems")
    cc.add_argument("--config", default=None, help="config path to validate")
    lv = sub.add_parser("live", help="real standalone runtime status (no stub radios)")
    lv_sub = lv.add_subparsers(dest="live_cmd", required=True)
    lvs = lv_sub.add_parser("status", help="read actual on-device radio/engine state")
    lvs.add_argument("--file", default="/captures/redux/live.json",
                     help="runtime checkpoint file; defaults to /captures/redux/live.json")
    lvd = lv_sub.add_parser("doctor", help="actual live Doctor findings from localhost runtime")
    lvd.add_argument("--port", type=int, default=8080,
                     help="local Redux dashboard port (default 8080)")
    lv.set_defaults(func=cmd_live)

    cf.set_defaults(func=cmd_config)

    pl = sub.add_parser("pipeline", help="local capture processing and audit jobs")
    pl.add_argument("--config", default="/etc/redux/pipeline.toml",
                    help="pipeline TOML file (default /etc/redux/pipeline.toml)")
    plc = pl.add_subparsers(dest="pipeline_cmd", required=True)
    plc.add_parser("status", help="read capture conversion and audit statistics")
    plc.add_parser("ingest", help="process one bounded pass of saved captures")
    plc.add_parser("audit", help="run one opt-in scope-checked local audit job")
    retention = plc.add_parser("prune", help="report verified raw capture cleanup candidates (dry-run default)")
    retention.add_argument("--older-than-days", type=int, default=30,
                           help="minimum age of source and ledger record (>=7)")
    retention.add_argument("--max-files", type=int, default=100,
                           help="limit processing to at most 1000 files")
    retention.add_argument("--apply", action="store_true",
                           help="explicitly delete only verified source captures; results preserved")
    pl.set_defaults(func=cmd_pipeline)

    ini = sub.add_parser("init", help="first-run setup: write config + swarm key + operator checklist")
    ini.add_argument("--dir", default="/etc/redux", help="config directory (default /etc/redux)")
    ini.add_argument("--node-id", default=None, help="this device's unique node id")
    ini.add_argument("--force", action="store_true", help="overwrite existing config/keystore")
    ini.set_defaults(func=cmd_init)

    st = sub.add_parser("selftest", help="on-device software battery + environment probe (paste the report back)")
    st.set_defaults(func=cmd_selftest)

    hw = sub.add_parser("hwtest", help="one-command validation battery: software + synthetic detection + optional live passive radio")
    hw.add_argument("--iface", default=None, help="monitor interface for the live passive steps (e.g. wlan1mon)")
    hw.add_argument("--seconds", type=float, default=20.0, help="live capture duration (default 20)")
    hw.add_argument("--out", default=None, help="also write the report to this file (e.g. hw_report.txt)")
    hw.set_defaults(func=cmd_hwtest)

    pk = sub.add_parser("packs", help="manage Packs")
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
    cl = ctsub.add_parser("live", help="live monitor-mode capture → re-id + flood detectors (needs hardware)")
    cl.add_argument("--iface", required=True, help="monitor-mode interface (e.g. wlan1mon)")
    cl.add_argument("--seconds", type=float, default=30.0, help="stop after N seconds (default 30)")
    cl.add_argument("--max", type=int, default=None, help="stop after N frames")
    cl.add_argument("--channel", type=int, default=None, help="park the receiver on this channel (one number)")
    cl.add_argument("--hop", action="store_true", help="sweep channels 1/6/11 (no need to know the channel)")
    ct.set_defaults(func=cmd_captap)

    sv = sub.add_parser("survey", help="passive situational-awareness sweep — inventory APs in earshot (read-only, no transmit)")
    sv.add_argument("--iface", default=None, help="monitor-mode interface (e.g. wlan1mon); omit with --demo")
    sv.add_argument("--seconds", type=float, default=20.0, help="listen for N seconds (default 20)")
    sv.add_argument("--max", type=int, default=None, help="stop after N frames")
    sv.add_argument("--channel", type=int, default=None, help="park on one channel instead of hopping 1/6/11")
    sv.add_argument("--demo", action="store_true", help="synthetic beacons, no radio")
    sv.set_defaults(func=cmd_survey)

    tf = sub.add_parser("tft", help="render the on-device TFT frame to the terminal")
    add_radio_flags(tf)
    tf.add_argument("--face", default="status", choices=["plain", "status"])
    tf.add_argument("--persona", help="apply a persona first")
    tf.add_argument("--ascii", action="store_true", help="ascii fallback (no box/block glyphs)")
    tf.add_argument("--pack", default=None, choices=["augur", "owl", "fox"],
                    help="face look (default: augur, or [tft].face_pack via --config)")
    tf.add_argument("--config", default=None, help="config.toml to read [tft] defaults from")
    tf.add_argument("--demo", action="store_true", help="populate with sample data")
    tf.set_defaults(func=cmd_tft)

    hu = sub.add_parser("hunt", help="RSSI-gradient fox-hunt (warmer/colder + bearing)")
    husub = hu.add_subparsers(dest="hunt_cmd", required=True)
    hud = husub.add_parser("demo", help="simulated approach/retreat to a target")
    hud.add_argument("--target", help="target BSSID/SSID to hunt")
    hul = husub.add_parser("live", help="live fox-hunt: real RSSI gradient to a target (needs monitor mode + root)")
    hul.add_argument("--iface", required=True, help="monitor-mode interface (e.g. wlan1mon)")
    hul.add_argument("--target", required=True, help="target BSSID to hunt (your own gear)")
    hul.add_argument("--channel", type=int, default=None, help="park on the target's channel")
    hul.add_argument("--seconds", type=float, default=60.0, help="run for N seconds (default 60)")
    hu.set_defaults(func=cmd_hunt)

    me = sub.add_parser("mesh", help="off-grid swarm — authenticated distributed Scope sync")
    mesub = me.add_subparsers(dest="mesh_cmd", required=True)
    mesub.add_parser("demo", help="3-node swarm: propagate an arm + reject a forged delta")
    mk = mesub.add_parser("key", help="swarm-key lifecycle: gen/rotate/show/export/import (sealed)")
    mksub = mk.add_subparsers(dest="key_cmd", required=True)

    def _store(p):
        p.add_argument("--store", default=None, help="sealed keystore path (or [mesh].keystore via --config)")
        p.add_argument("--config", default=None, help="config.toml to read [mesh] defaults from")

    kg = mksub.add_parser("gen", help="generate a new swarm key")
    _store(kg)
    kg.add_argument("--label", default="", help="job/lab label")
    kg.add_argument("--ttl-days", type=float, default=None, help="expire the key after N days")
    kr = mksub.add_parser("rotate", help="new current key; recent kept for a grace window")
    _store(kr)
    kr.add_argument("--label", default="")
    kr.add_argument("--ttl-days", type=float, default=None)
    _store(mksub.add_parser("show", help="list key ids + status (never the key material)"))
    _store(mksub.add_parser("export", help="print the active key's exchange token (QR/LoRa)"))
    ki = mksub.add_parser("import", help="import a key exchange token from a file")
    _store(ki)
    ki.add_argument("--token-file", required=True, help="file holding an AKEY1 token")
    mk.set_defaults(func=cmd_mesh_key)

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
    try:
        return args.func(args)
    except ConfigError as e:
        print(e)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
