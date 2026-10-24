"""redux CLI — the operator entrypoint that ties the platform together.

    redux status   [--intent I] [--onboard] [--adapter]        one-shot glass-box snapshot
    redux run      --replay events.json [--cycles N] ...        run pump cycles over a recorded session
    redux packs    --dir D list | enable NAME | disable NAME    manage Packs
    redux survey   --iface wlan1mon [--demo]                    passive AP situational-awareness sweep (read-only)
    redux selftest                                             on-device software battery (paste the report back)
    redux hwtest   --iface wlan1mon [--out FILE]                validation battery: software + synthetic detection + live passive
    redux demo     [--intent I] [--live] [--offense]            hands-off pass (gate 1.6): arrange radios → capture, one command
    redux benchpass prep | preflight | auto | run | report     the one lump-sum hardware session (resumable; `auto` self-grades; see docs/BENCH_SESSION.md)

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
# One canonical resolver (redux.core.scope.default_scope_path) is shared with the
# crack-audit pipeline so the CLI firing tools and the audit worker never arm
# against different scope files.
from .core.scope import default_scope_path as _default_scope_path

_DEFAULT_SCOPE_PATH = _default_scope_path()


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
    # Populate the rogue-AP/evil-twin detector's trusted set from the config's
    # [detect] trusted_networks (only when --config is passed; empty otherwise).
    trusted = None
    cfg = _cfg(args)
    if cfg is not None:
        from .detect.rogue_ap import parse_trusted_networks
        trusted = parse_trusted_networks(getattr(cfg.detect, "trusted_networks", []))
    return Augur(radios=_radios(args), intent=Intent(args.intent), driver=driver,
                 trusted=trusted)


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
    from .packs.locations import StateUnavailable
    try:
        if args.pack_cmd == "runtime":
            from .display.local_http import open_status
            with open_status(args.url) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("runtime status too large")
            report = json.loads(raw)
            if not isinstance(report, dict):
                raise ValueError("invalid runtime report")
            print(json.dumps(report.get("plugins", {"runtime_activation": "unknown"}), indent=2))
            return 0
        from .packs.locations import locations, mount_guard, prepare_parent
        root, state = locations(args.dir, args.state)
        guard = mount_guard(state)
        mgr = PackManager(root, state_path=state, write_guard=guard)
        if args.pack_cmd == "list":
            print(json.dumps(mgr.inventory(offset=args.offset), indent=2))
        else:
            options = json.loads(args.options) if args.pack_cmd == "configure" else None
            if args.confirm:
                prepare_parent(state, guard)
                result = mgr.apply(args.confirm, args.pack_cmd, args.name, options, args.authorize_side_effects)
            else:
                result = mgr.preview(args.pack_cmd, args.name, options, args.authorize_side_effects)
                # Private configuration is never emitted, even in previews.
                result.pop("next", None)
            print(json.dumps(result, indent=2))
    except StateUnavailable as e:
        print(f"plugin state unavailable: {e}; verify REDUXCAP before retrying", file=sys.stderr)
        return 1
    except DependencyError as e:
        print(f"plugin request rejected: {e}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, RecursionError) as e:
        print(f"plugin request failed: {type(e).__name__}; validate manifest, state and preview", file=sys.stderr)
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


def _wordlist(path):
    """Read a username/password wordlist (bounded), empty tuple on any error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = []
            for line in f:
                s = line.strip()
                if s:
                    lines.append(s)
                if len(lines) >= 10000:
                    break
            return tuple(lines)
    except OSError:
        return ()


def cmd_netrecon(args) -> int:
    """The network-layer kill-chain over the ARMED Scope (the second half of the
    chain the RF side can't do): scan one authorized subnet, or autorun the whole
    armed scope (scan -> optional cred-test -> loot). Real nmap/hydra on the Pi;
    honest tool-absence off it. Scope decides WHERE, always — nothing off-scope runs."""
    import json
    from .netrecon import recon_subnet, autorun, ReconRefused
    scope = Scope.load(args.scope_file)
    if args.netrecon_cmd == "scan":
        try:
            res = recon_subnet(args.cidr, scope=scope)
        except ReconRefused as e:
            print(f"refused: {e}")
            return 3
        if not res.available:
            print(f"scan unavailable: {res.reason}")
            return 0
        print(f"{res.reason} (via {res.tool or 'scanner'})")
        for h in res.hosts:
            ports = ", ".join((f"{p.number}/{p.proto} {p.service}".strip()) for p in h.ports)
            print(f"  {h.ip:15} {h.hostname or '-':20} {ports}")
        return 0
    # autorun: autonomous sweep over every armed CIDR
    users = _wordlist(args.userlist) if args.userlist else ()
    pws = _wordlist(args.passlist) if args.passlist else ()
    book = autorun(scope=scope, usernames=users, passwords=pws)
    print(json.dumps(book.to_dict(), indent=2))
    return 0


def cmd_sdr(args) -> int:
    """Ingest SDR tool output into the sighting store so SDR captures reach geo/Dex:
    rtl_433 ISM records (tagged with the receiver's position) or dump1090 ADS-B
    records (the aircraft's own position). Reads line-delimited JSON, one record
    per line — exactly what `rtl_433 -F json` / `dump1090 --write-json` emit."""
    import json
    from .geo.db import SightingStore, DEFAULT_DB_PATH
    from .sdr import ingest_rtl433, ingest_adsb
    cfg = _cfg(args)
    db = args.db or (cfg.cache.db_path if cfg else None) or DEFAULT_DB_PATH
    store = SightingStore(db)

    def _records(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec

    if args.adsb:
        n = ingest_adsb(_records(args.file), store)
        kind = "ADS-B aircraft"
    else:
        pos = (args.lat, args.lon) if args.lat is not None and args.lon is not None else None
        n = ingest_rtl433(_records(args.file), store, position=pos)
        kind = "ISM device"
    print(f"ingested {n} {kind} sighting(s) from {args.file}")
    return 0


def cmd_portal(args) -> int:
    """Captive / evil-portal for AUTHORIZED client testing. `plan` previews what
    would be served (no server runs). `serve` actually stands it up — and ONLY
    with --authorized — binding least-exposed with the URL logged. For a real
    engagement you supply your own page; the built-in template imitates no brand."""
    from .portal import CaptivePortal, serve
    template = None
    if getattr(args, "template", None):
        try:
            with open(args.template, encoding="utf-8") as f:
                template = f.read()
        except OSError as e:
            print(f"template: {e}")
            return 2
    portal = CaptivePortal(engagement=args.engagement,
                           **({"template": template} if template else {}))
    if args.portal_cmd == "plan":
        print(f"captive portal (engagement={args.engagement}):")
        print(f"  bind_scope: {args.bind_scope}   port: {args.port}")
        print(f"  template:   {'custom (' + args.template + ')' if template else 'built-in generic (imitates no brand)'}")
        print(f"  page bytes: {len(portal.page())}")
        print("  NOT serving (plan only) — add `serve --authorized` to run an authorized engagement.")
        return 0
    # serve: require explicit --authorized, UNLESS lab mode is on. Lab mode is the
    # own-gear switch (`redux scope lab-mode on`) that already waives pre-arming for
    # the firing paths, so it waives the portal's per-run --authorized too.
    if not getattr(args, "authorized", False):
        from .core.scope import Scope, default_scope_path
        try:
            lab = Scope.load(default_scope_path()).lab_mode
        except Exception:
            lab = False
        if not lab:
            print("refused: `serve` requires --authorized (or turn on lab mode: `redux scope lab-mode on`)")
            return 3
        print("lab mode on — `--authorized` waived for your own lab")
    # posture gate: the portal is firing-capable (credential harvest), so a
    # detection-only persona hard-disables it — same invariant as eap/capture.
    if getattr(args, "persona", None):
        from .core import persona as _persona
        if not _persona.get(args.persona).offense_available:
            print(f"refused: persona '{args.persona}' is detection-only — the portal is "
                  f"firing-capable and will not run under it")
            return 3
    try:
        httpd = serve(portal, authorized=True, port=args.port,
                      bind_scope=args.bind_scope, _serve=False)
    except ValueError as e:
        print(f"refused: {e}")
        return 3
    try:
        httpd.serve_forever()           # blocks until Ctrl-C
    except KeyboardInterrupt:
        print(f"\nportal stopped — {len(portal.submissions)} submission(s) recorded.")
    finally:
        httpd.shutdown()
    return 0


def cmd_captap(args) -> int:
    if getattr(args, "captap_cmd", "demo") == "live":
        return _captap_live(args)
    return _captap_demo(args)


def cmd_demo(args) -> int:
    """The hands-off pass (Phase-1 gate 1.6) in one command: arrange radios by
    intent → repoint the capture engine → bounded capture, every step glass-box.
    Default is a dry run on a sample inventory (no hardware). `--live` probes the
    real radios on the Pi; it is honest when none are present and never fabricates.
    Nothing transmits unless posture + Scope allow it."""
    import shutil
    from .capstone import plan_capstone, sample_inventory
    from .core import persona as _persona

    scope = Scope.load(args.scope_file)
    if args.persona:
        offense = _persona.get(args.persona).offense_available
    else:
        offense = bool(args.offense)

    if args.live:
        from .radio.probe import probe
        radios = probe()
        engine = shutil.which("bettercap") is not None or shutil.which("angryoxide") is not None
        source = "live probe"
    else:
        radios = sample_inventory()
        engine = not args.no_engine
        source = "sample inventory (dry run)"

    cap = plan_capstone(radios, args.intent, scope, offense_enabled=offense,
                        seconds=int(args.seconds), engine_available=engine)

    if getattr(args, "json", False):
        print(json.dumps(cap.as_dict(), indent=2))
        return 0 if cap.ok else 1

    print(f"=== redux demo — hands-off pass ({source}; intent={cap.intent}, "
          f"offense={'on' if offense else 'off'}) ===")
    for s in cap.steps:
        print(f"  [{'ok  ' if s.ok else 'STOP'}] {s.step:13} {s.detail}")
        print(f"         └─ {s.reason}")
    for w in cap.warnings:
        print(f"  [warn] {w}")
    print(f"summary: {cap.summary}")
    if not args.live and cap.ok:
        print("next:    run it for real on the Pi →  redux demo --live --intent "
              f"{cap.intent}" + (" --offense" if offense else ""))
    return 0 if cap.ok else 1


def cmd_benchpass(args) -> int:
    """The one lump-sum hardware session. `prep` (no hardware) prints the gear to
    stage, which gates are software-ready, and the exact command sequence. `run`
    walks every gate on the Pi, recording pass/fail/skip to a resumable ledger.
    `report` renders the consolidated result. The pass call is always yours."""
    from . import benchpass as bp

    sub = getattr(args, "benchpass_cmd", None) or "prep"
    board = getattr(args, "board", "") or ""
    ledger_path = getattr(args, "ledger", "") or ""
    if not ledger_path:
        ledger_path = f"benchpass-{board}.json" if board else "benchpass-ledger.json"
    phases = {"phase1": "Phase 1 — flashable MVP", "platform": "Platform surfaces",
              "subsystem": "Subsystem battery"}

    if sub == "prep":
        gear = bp.gear_checklist()
        by_phase = bp.gates_by_phase()
        if getattr(args, "json", False):
            print(json.dumps({"gear": gear,
                              "gates": [g.as_dict() for g in bp.GATES]}, indent=2))
            return 0
        print("=== redux benchpass — prep (run this at home, no hardware needed) ===")
        print(f"\nGear to stage ({len(gear)} items):")
        for item in gear:
            print(f"  - {item}")
        print(f"\nGates to walk ({len(bp.GATES)} total), in order:")
        for ph, label in phases.items():
            gs = by_phase.get(ph, [])
            if not gs:
                continue
            print(f"\n  {label}:")
            for g in gs:
                flag = " (physical)" if g.manual else ""
                print(f"    [{g.id}] {g.title}{flag}")
                print(f"          run:  {g.live}")
                print(f"          pass: {g.passes}")
        print("\nWhen you're at the bench:  redux benchpass run   (resumable; pass/fail/skip each)")
        return 0

    if sub == "preflight":
        import shutil
        facts = {}
        try:
            from .radio.probe import probe
            radios = probe()
        except Exception:
            radios = []
        facts["radios"] = len(radios) > 0
        facts["radios_detail"] = (f"{len(radios)} enumerated" if radios
                                  else "none enumerated (plug a radio / run on the Pi)")
        mons = [getattr(r, "iface", "?") for r in radios if getattr(r, "monitor", False)]
        facts["monitor_radio"] = bool(mons)
        facts["monitor_radio_detail"] = (", ".join(mons) if mons else "no monitor-capable adapter")
        eng = [e for e in ("bettercap", "angryoxide") if shutil.which(e)]
        facts["capture_engine"] = bool(eng)
        facts["capture_engine_detail"] = ", ".join(eng) if eng else "none on PATH"
        facts["captures_mount"] = os.path.ismount("/captures") and os.access("/captures", os.W_OK)
        facts["captures_mount_detail"] = ("mounted rw" if facts["captures_mount"]
                                          else "/captures not a writable mount")
        try:
            from .selftest import run_selftest
            checks = run_selftest()
            failed = [c for c in checks if getattr(c, "status", "") == "FAIL"]
            facts["software_battery"] = not failed
            facts["software_battery_detail"] = ("green" if not failed
                                                else f"{len(failed)} failing check(s)")
        except Exception as e:
            facts["software_battery"] = False
            facts["software_battery_detail"] = f"selftest error: {e}"
        try:
            import cryptography  # noqa: F401
            facts["crypto"] = True
        except Exception:
            facts["crypto"] = False
        board = bp.preflight(facts)
        ready = bp.preflight_ready(board)
        if getattr(args, "json", False):
            print(json.dumps({"board": board, "ready": ready}, indent=2))
            return 0 if ready else 1
        print("=== redux benchpass — preflight (run on the Pi before the session) ===")
        for key, label, _ in bp.PREREQS:
            v = board[key]
            mark = "OK  " if v["ok"] else ("----" if key == "crypto" else "MISS")
            print(f"  [{mark}] {v['label']}")
            print(f"         {v['detail']}  → unblocks {', '.join(v['unblocks'])}")
        print(f"\n{'READY' if ready else 'NOT READY'} — "
              + ("all required prerequisites present; start:  redux benchpass run"
                 if ready else "resolve the MISS rows above, then re-run preflight"))
        return 0 if ready else 1

    if sub == "matrix":
        boards = args.boards or ["pi4", "pi5"]
        ledgers = {b: bp.Ledger.load(f"benchpass-{b}.json") for b in boards}
        matrix = bp.board_matrix(bp.GATES, ledgers)
        if getattr(args, "json", False):
            print(json.dumps({"boards": boards, "matrix": matrix,
                              "all_done": bp.all_boards_done(matrix)}, indent=2))
            return 0 if bp.all_boards_done(matrix) else 1
        print("=== redux benchpass — cross-board matrix (done = every board passes) ===")
        print(f"  {'gate':8} " + " ".join(f"{b:>6}" for b in boards) + "  done")
        for r in matrix:
            cells = " ".join(f"{r['per_board'][b]:>6}" for b in boards)
            print(f"  {r['id']:8} {cells}  {'✓' if r['done'] else ''}")
        done = bp.all_boards_done(matrix)
        print(f"\n{'ALL BOARDS DONE' if done else 'not done on every board yet'}")
        return 0 if done else 1

    if sub == "report":
        led = bp.Ledger.load(ledger_path)
        rep = bp.build_report(bp.GATES, led)
        if getattr(args, "markdown", False):
            print(bp.report_markdown(rep, board=(board or led.board),
                                     image_sha=getattr(args, "image_sha", ""),
                                     model=getattr(args, "model", "")), end="")
            return 0 if rep.clean else 1
        if getattr(args, "json", False):
            print(json.dumps(rep.as_dict(), indent=2))
            return 0 if rep.clean else 1
        _render_benchpass_report(rep)
        return 0 if rep.clean else 1

    if sub == "auto":
        from .bench_auto import AutoExecutor, measure_facts
        led = bp.Ledger.load(ledger_path)
        if board:
            led.board = board
        only = getattr(args, "only", None) or None
        # Auto-measured preflight first — never start blind.
        facts = measure_facts()
        pf = bp.preflight(facts)
        print("=== redux benchpass — auto (self-grading; physical steps prompted) ===")
        for key, label, _ in bp.PREREQS:
            v = pf[key]
            mark = "OK  " if v["ok"] else ("----" if key == "crypto" else "MISS")
            print(f"  [{mark}] {v['label']}: {v['detail']}")
        if not bp.preflight_ready(pf):
            print("\nheads-up: some prerequisites are MISSING — those gates will SKIP honestly, not fake a pass.")
        print("\nWalking gates. Machine-checkable ones grade themselves; you'll be")
        print("prompted only for physical actions (plug a radio, trigger a handshake)"
              + (" — and those auto-SKIP with --yes." if getattr(args, "yes", False) else "."))

        def _confirm(msg):
            if getattr(args, "yes", False):
                print(f"  {msg} → skipped (--yes)")
                return False
            try:
                return input(f"  {msg} [Enter=yes / n=skip]: ").strip().lower() in ("", "y", "yes")
            except EOFError:
                return False

        def _ask(g):
            if getattr(args, "yes", False):
                return "skip", "needs operator judgment (--yes: skipped)"
            print(f"      run:  {g.live}")
            print(f"      pass: {g.passes}")
            try:
                a = input("  your result [pass/fail/skip]: ").strip().lower()
            except EOFError:
                return "skip", "non-interactive, no verdict"
            note = ""
            if a in ("f", "fail", "s", "skip"):
                try:
                    note = input("  note: ").strip()
                except EOFError:
                    note = ""
            return {"p": "pass", "pass": "pass", "f": "fail", "fail": "fail",
                    "s": "skip", "skip": "skip"}.get(a, "skip"), note

        # Pin sub-calls to the SAME codebase we're running, so `auto` never
        # silently drives a stale `redux` already installed on the image.
        ax = AutoExecutor(confirm=_confirm, ask=_ask,
                          redux_prefix=[sys.executable, "-m", "redux.cli"])
        try:
            rep = bp.run_benchpass(bp.GATES, ax, led,
                                   resume=getattr(args, "resume", False), only=only)
        except KeyboardInterrupt:
            led.save()
            print("\n(paused — progress saved; resume with:  redux benchpass auto --resume)")
            return 2
        print()
        _render_benchpass_report(rep)
        print(f"\nledger: {ledger_path}")
        if getattr(args, "markdown", False):
            print()
            print(bp.report_markdown(rep, board=(board or led.board)), end="")
        return 0 if rep.clean else 1

    # run: guided walk, resumable ledger
    led = bp.Ledger.load(ledger_path)
    if board:
        led.board = board
    only = getattr(args, "only", None) or None

    def executor(g):
        print(f"\n[{g.id}] {g.title}" + ("  (physical step)" if g.manual else ""))
        if g.needs:
            print(f"  needs: {', '.join(g.needs)}")
        print(f"  run:   {g.live}")
        print(f"  pass:  {g.passes}")
        try:
            ans = input("  result [pass/fail/skip/quit]: ").strip().lower()
        except EOFError:
            return "pending", "no input (non-interactive)"
        if ans in ("q", "quit"):
            raise KeyboardInterrupt
        detail = ""
        if ans in ("f", "fail", "s", "skip"):
            try:
                detail = input("  note (what happened): ").strip()
            except EOFError:
                detail = ""
        return {"p": "pass", "pass": "pass", "f": "fail", "fail": "fail",
                "s": "skip", "skip": "skip"}.get(ans, "skip"), detail

    try:
        rep = bp.run_benchpass(bp.GATES, executor, led, resume=getattr(args,"resume",False), only=only)
    except KeyboardInterrupt:
        led.save()
        print("\n(paused — progress saved; resume with:  redux benchpass run --resume)")
        return 2
    print()
    _render_benchpass_report(rep)
    print(f"\nledger: {ledger_path}")
    return 0 if rep.clean else 1


def _render_benchpass_report(rep) -> None:
    print("=== redux benchpass — consolidated result ===")
    for r in rep.rows:
        mark = {"pass": "PASS", "fail": "FAIL", "skip": "skip", "pending": "····"}.get(r["status"], "····")
        line = f"  [{mark}] {r['id']:6} {r['title']}"
        print(line)
        if r["detail"]:
            print(f"         └─ {r['detail']}")
    c = rep.counts
    print(f"\n{c.get('pass',0)} pass · {c.get('fail',0)} fail · {c.get('skip',0)} skip · "
          f"{c.get('pending',0)} pending")
    if rep.clean:
        print("CLEAN — every gate walked, no failures.")
    elif rep.complete:
        print("complete, but with failures — see the FAIL rows above.")
    else:
        print("incomplete — pending gates remain; resume with:  redux benchpass run --resume")


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
        from .radio.channels import all_band_channels, parse_channels
        if getattr(args, "channels", None):
            chans = parse_channels(args.channels)
        else:
            chans = all_band_channels(bands=tuple(args.bands) if getattr(args, "bands", None) else ("2.4", "5"))
        print(f"redux captap live — {args.iface}, sweeping {len(chans)} channels "
              f"across {'/'.join(args.bands) + 'GHz' if getattr(args, 'bands', None) else 'all bands'} (≤{args.seconds}s)")

        def _hop():
            i = 0
            while not stop.is_set():
                _setch(chans[i % len(chans)])
                i += 1
                stop.wait(getattr(args, "dwell", 2.0))
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
            from .radio.channels import all_band_channels, parse_channels
            if getattr(args, "channels", None):
                chans = parse_channels(args.channels)
            else:
                chans = all_band_channels(bands=tuple(args.bands) if getattr(args, "bands", None) else ("2.4", "5"))
            print(f"redux survey — passive sweep on {args.iface}, {len(chans)} channels "
                  f"across {'/'.join(args.bands) + 'GHz' if getattr(args, 'bands', None) else 'all bands'} (≤{args.seconds}s)")

            def _hop():
                i = 0
                while not stop.is_set():
                    _setch(chans[i % len(chans)])
                    i += 1
                    stop.wait(getattr(args, "dwell", 2.0))
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
    hc = getattr(args, "hunt_cmd", "demo")
    if hc == "live":
        return _hunt_live(args)
    if hc == "track":
        return _hunt_track(args)
    return _hunt_demo(args)


def _hunt_track(args) -> int:
    """Scope-bounded location trail demo: fixes are logged over time ONLY for an
    armed target. A bystander BSSID is refused and never written — the trail is a
    fox-hunt history of what you're hunting, not a who-was-where log of the air."""
    from .geo import TrackStore
    armed = "aa:bb:cc:dd:ee:ff"
    bystander = "11:22:33:44:55:66"
    scope = Scope(); scope.add(armed, "bssid", job="hunt")
    store = TrackStore(":memory:")
    print("redux hunt track demo — Scope-bounded location trail (in-memory)")
    # a few fixes on the armed target as you move
    for i, (la, lo, r) in enumerate([(42.00, -83.00, -70), (42.01, -83.01, -58),
                                     (42.012, -83.012, -47)]):
        ok, why = store.record_fix(scope, armed, la, lo, rssi=r, ts=100 + i, now=100 + i)
        print(f"  armed fix {i+1}: logged={ok}")
    ok, why = store.record_fix(scope, bystander, 42.02, -83.02, now=110)
    print(f"  bystander fix: logged={ok} — {why}")
    print(f"  trail length (armed): {store.count(armed)}   bystander logged: {store.count(bystander)}")
    est = store.estimate(armed)
    if est is not None:
        print(f"  estimate: {est.lat:.5f},{est.lon:.5f} — {est.reason}")
    print("  → only armed targets accumulate a trail; bystanders are never logged.")
    return 0


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


def _cmd_mesh_swarm(args) -> int:
    """Coordinated multi-device capture demo (SIMULATED): a coordinator places the
    capture chain across two nodes by capability, and each node re-gates the
    assignment against its OWN Scope before firing — the coordinator distributes
    work, never permission."""
    from .mesh import NodeProfile, SwarmCoordinator, SwarmWorker
    from .operator.operator import Operator, Phase
    key = b"swarm-shared-key"
    cap = "capture.handshake"
    target = "00:11:22:33:44:55"

    def armed():
        s = Scope(); s.add(target, "bssid", job="op"); return s

    print("redux swarm ops demo — coordinated capture across 2 nodes (SIMULATED)")
    coord = SwarmCoordinator(key, node_id="coordinator")
    coord.add_node(NodeProfile("alpha", caps={cap: True}))    # has a capture engine
    coord.add_node(NodeProfile("bravo", caps={cap: False}))   # recon-only
    for a in coord.plan([target], job="op"):
        if a.phase is Phase.CAPTURE:
            print(f"  placement: capture → {a.assignee or 'UNPLACED'} ({a.reason})")

    def captured(_t):
        return True, "handshake captured"

    # alpha is armed + capable → fires. bravo is armed but a stranger hands IT the
    # capture task directly; it has no capture engine, so its local gate refuses.
    alpha = SwarmWorker(Operator(armed(), caps={cap: True}), key, node_id="alpha",
                        executors={Phase.CAPTURE: captured})
    msgs = coord.assignments([target], job="op")
    for m in msgs:
        r = alpha.accept(m)
        if r.get("phase") == "capture" and r.get("reason", "") and "not me" not in r["reason"]:
            print(f"  alpha (armed+capable): fired={r['fired']} — {r.get('result', r['reason'])}")

    # the safety property: an UN-ARMED node refuses a correctly-signed assignment.
    blank = SwarmWorker(Operator(Scope(), caps={cap: True}), key, node_id="alpha",
                        executors={Phase.CAPTURE: captured})
    r = blank.accept(msgs[0])
    print(f"  un-armed node, VALID signature: fired={r['fired']} — {r['reason']}")
    print("  → authorization is never delegated: every node re-checks its own Scope.")
    return 0


def cmd_mesh(args) -> int:
    """Mesh demo: a 3-node swarm sharing one authorized Scope over the (simulated)
    LoRa lane — arm on one node, it converges everywhere; a forged delta (wrong
    swarm key) is rejected."""
    if getattr(args, "mesh_cmd", "demo") == "swarm":
        return _cmd_mesh_swarm(args)
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
    # No --iface: let the orchestrator assign the rogue-AP its own radio.
    plan = bc.eap_plan(args.ssid, authorized=args.authorized, iface=args.iface or "")
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
    # Per-radio engine plan: every capture radio bound to an engine, not just one.
    binds = bc.capture_bindings()
    if binds:
        print("\nper-radio plan (all capture radios, tandem widens the surface):")
        for b in binds:
            print(f"  {b['iface']} [{b['role']}/{b['mode']} "
                  f"{b['band'] + 'GHz' if b['band'] else '-'}] -> "
                  f"{b['engine'] or 'none'}: {b['reason']}")
    # Always show the AngryOxide command it WOULD run (preview; no binary needed to print it)
    primary_iface = args.iface or next((b["iface"] for b in binds
                                        if b["role"] == "capture"), "") or "wlan1"
    from .crack import AngryOxideProvider, AngryOxideConfig
    ao = AngryOxideProvider(config=AngryOxideConfig(iface=primary_iface))
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
    if args.scope_cmd == "lab-mode":
        if args.state in ("on", "off"):
            scope.lab_mode = (args.state == "on")
            scope.save(path)
        print("lab mode: " + ("ON — firing is approved on sight for your own gear "
                              "(RF targets + private networks), each logged"
                              if scope.lab_mode else
                              "OFF — targets must be armed before firing (safe default)"))
        return 0
    if args.scope_cmd == "list":
        s = scope.summary()
        print(f"scope: {s['active']} active / {s['total']} total"
              + (f", {s['expired']} expired" if s["expired"] else "")
              + (f" | jobs: {', '.join(s['jobs'])}" if s["jobs"] else "")
              + ("  (EMPTY — nothing authorized)" if s["empty"] else "")
              + ("  [LAB MODE ON]" if s["lab_mode"] else ""))
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


def cmd_wordlist(args) -> int:
    """Manage the on-device hashcat wordlist registry (list/add/pull/select)."""
    from .crack.wordlists import WordlistStore
    import shutil as _shutil
    store = WordlistStore(args.dir)
    cmd = args.wordlist_cmd
    if cmd == "list":
        items = store.list()
        if not items:
            print(f"no wordlists registered in {args.dir}")
            return 0
        for w in items:
            print(f"  {w.id}  [{w.tier:<8}] {w.name}  ({w.size_bytes} bytes)  {w.source}")
        return 0
    if cmd == "add":
        wl, reason = store.register(args.path, name=args.name, tier=args.tier, copy=args.copy)
        print(reason)
        return 0 if wl else 3
    if cmd == "pull":
        # Built-in fetcher: copy from a path the operator already has mounted
        # (e.g. the QNAP NAS mounted locally). A network transport can be wired by
        # calling WordlistStore.pull_from_nas() with a custom fetcher.
        def _mounted_copy(spec, dest):
            src = __import__("pathlib").Path(spec)
            if not src.is_file():
                raise FileNotFoundError(f"source not found (is the NAS mounted?): {spec}")
            _shutil.copyfile(src, dest)
        wl, reason = store.pull_from_nas(args.source, _mounted_copy, name=args.name, tier=args.tier)
        print(reason)
        return 0 if wl else 3
    if cmd == "select":
        wl, reason = store.select(name=args.name, tier=args.tier)
        print(reason)
        return 0 if wl else 3
    if cmd == "verify":
        problems = store.verify()
        if not problems:
            print("all registered wordlists present and unchanged")
            return 0
        for w, why in problems:
            print(f"  DRIFT {w.id} {w.name}: {why}")
        return 3
    if cmd == "remove":
        ok, reason = store.remove(args.key)
        print(reason)
        return 0 if ok else 3
    return 2


def cmd_online(args) -> int:
    """Report whether the device currently has uplink (glass-box; no data leaves)."""
    from .connectivity.online import check_online
    s = check_online(host=args.host, port=args.port, timeout=args.timeout)
    print(s.reason)
    return 0 if s.online else 1


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
    if args.pipeline_cmd == "recover":
        from .crack.recovery import recovery_report
        report = recovery_report(Settings.load(args.config),
                                 apply=args.apply, max_files=args.max_files)
        print(json.dumps(report, indent=2))
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
        scope = Scope.load(str(cfg.scope_file))
        # Lab mode makes the crack automatic; otherwise it stays config opt-in.
        if not cfg.enabled and not bool(getattr(scope, "lab_mode", False)):
            print(json.dumps({"status": "disabled",
                              "reason": "audit opt-in is off (enable in config, or turn on lab mode)"}))
            return 0
        with AuditWorker(cfg, scope) as worker:
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
            return "".join(ch if ch.isprintable()
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
    try:
        # These stat the path (and its parents); when the daemon's capture dir
        # isn't readable by the invoking user they raise EACCES rather than
        # returning False. A status command must report that, never traceback.
        is_link = snapshot.is_symlink()
        is_regular = snapshot.is_file()
    except OSError as error:
        hint = " — try running with sudo" if isinstance(error, PermissionError) else ""
        print(f"live: cannot read runtime snapshot at {snapshot} "
              f"({type(error).__name__}){hint}")
        return 3
    if is_link or not is_regular:
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
    lvs.add_argument("--file", default="/captures/live/live.json",
                     help="runtime checkpoint file; defaults to /captures/live/live.json "
                          "(the live daemon's state_dir, where it writes live.json)")
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
    recovery = plc.add_parser("recover", help="inspect or retry failed and incomplete capture processing (dry-run default)")
    recovery.add_argument("--max-files", type=int, default=100,
                          help="retry at most 1000 recorded source captures")
    recovery.add_argument("--apply", action="store_true",
                          help="explicitly retry failed or missing-output sources; no deletion")
    plc.add_parser("audit", help="run one opt-in scope-checked local audit job")
    retention = plc.add_parser("prune", help="report verified raw capture cleanup candidates (dry-run default)")
    retention.add_argument("--older-than-days", type=int, default=30,
                           help="minimum age of source and ledger record (>=7)")
    retention.add_argument("--max-files", type=int, default=100,
                           help="limit processing to at most 1000 files")
    retention.add_argument("--apply", action="store_true",
                           help="explicitly delete only verified source captures; results preserved")
    pl.set_defaults(func=cmd_pipeline)

    wl = sub.add_parser("wordlist", help="manage the hashcat wordlist registry (list/add/pull/select)")
    wl.add_argument("--dir", default="/captures/wordlists",
                    help="wordlist store directory (default /captures/wordlists)")
    wlc = wl.add_subparsers(dest="wordlist_cmd", required=True)
    wlc.add_parser("list", help="show registered wordlists (id, tier, size, source)")
    wla = wlc.add_parser("add", help="register a local wordlist")
    wla.add_argument("path", help="absolute path to the wordlist file")
    wla.add_argument("--name", default=None, help="operator label (defaults to the filename)")
    wla.add_argument("--tier", default="standard", choices=("fast", "standard", "thorough"))
    wla.add_argument("--copy", action="store_true", help="copy the file into the store dir")
    wlp = wlc.add_parser("pull", help="pull a wordlist from a mounted NAS path (e.g. a QNAP share)")
    wlp.add_argument("source", help="path to the file on the mounted NAS share")
    wlp.add_argument("--name", default=None, help="operator label (defaults to the filename)")
    wlp.add_argument("--tier", default="thorough", choices=("fast", "standard", "thorough"))
    wls = wlc.add_parser("select", help="show which wordlist an audit would use (glass-box)")
    wls.add_argument("--name", default=None, help="select this list by name/id")
    wls.add_argument("--tier", default=None, choices=("fast", "standard", "thorough"),
                     help="select the most thorough list in this tier")
    wlc.add_parser("verify", help="re-hash registered lists and report drift/missing")
    wlr = wlc.add_parser("remove", help="unregister a list (leaves the file on disk)")
    wlr.add_argument("key", help="wordlist id or name to remove")
    wl.set_defaults(func=cmd_wordlist)

    on = sub.add_parser("online", help="check whether the device currently has internet uplink")
    on.add_argument("--host", default="one.one.one.one", help="reachability host (default one.one.one.one)")
    on.add_argument("--port", type=int, default=443, help="reachability port (default 443)")
    on.add_argument("--timeout", type=float, default=2.0, help="probe timeout seconds (default 2.0)")
    on.set_defaults(func=cmd_online)

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
    pk.add_argument("--dir", help="catalog directory (installed default: /usr/share/redux/packs)")
    pk.add_argument("--state", help="writable requested-state path; use /captures/config/packs.json on images")
    psub = pk.add_subparsers(dest="pack_cmd", required=True)
    rt = psub.add_parser("runtime", help="read current process evidence from local status; never activate")
    rt.add_argument("--url", default="http://127.0.0.1:8080/api/status")
    li = psub.add_parser("list"); li.add_argument("--offset", type=int, default=0)
    en = psub.add_parser("enable"); en.add_argument("name")
    di = psub.add_parser("disable"); di.add_argument("name")
    co = psub.add_parser("configure"); co.add_argument("name"); co.add_argument("--options", required=True, help="JSON object; avoid secrets in shell history")
    ro = psub.add_parser("rollback"); ro.add_argument("name", help="installed plugin used to scope local recovery")
    for command in (en, di, co, ro):
        command.add_argument("--confirm", help="reviewed preview revision; omission is a dry run")
        command.add_argument("--authorize-side-effects", action="store_true", help="authorize requested side effects, never activate code")
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

    nr = sub.add_parser("netrecon", help="network-layer kill-chain over the armed scope (scan/cred/loot)")
    nr.add_argument("--scope-file", default=_DEFAULT_SCOPE_PATH)
    nrsub = nr.add_subparsers(dest="netrecon_cmd", required=True)
    nrs = nrsub.add_parser("scan", help="scan one authorized subnet")
    nrs.add_argument("cidr", help="CIDR to scan (must be authorized in scope)")
    nra = nrsub.add_parser("autorun", help="autonomous sweep over every armed CIDR (scan→cred→loot)")
    nra.add_argument("--userlist", help="username wordlist to cred-test discovered services")
    nra.add_argument("--passlist", help="password wordlist to cred-test discovered services")
    nr.set_defaults(func=cmd_netrecon)

    sd = sub.add_parser("sdr", help="ingest SDR output (rtl_433 ISM / dump1090 ADS-B) into the sighting store")
    sdsub = sd.add_subparsers(dest="sdr_cmd", required=True)
    sdi = sdsub.add_parser("ingest", help="ingest line-delimited JSON records from a file")
    sdi.add_argument("file", help="file of line-delimited JSON (rtl_433 -F json / dump1090 --write-json)")
    g = sdi.add_mutually_exclusive_group()
    g.add_argument("--rtl433", action="store_true", help="records are rtl_433 ISM devices (default)")
    g.add_argument("--adsb", action="store_true", help="records are dump1090 ADS-B aircraft")
    sdi.add_argument("--lat", type=float, help="receiver latitude (tags ISM sightings)")
    sdi.add_argument("--lon", type=float, help="receiver longitude (tags ISM sightings)")
    sdi.add_argument("--db", help="sighting store path")
    sd.set_defaults(func=cmd_sdr)

    po = sub.add_parser("portal", help="captive/evil-portal for AUTHORIZED client testing")
    po.add_argument("--engagement", required=True, help="named engagement you are authorized to run")
    po.add_argument("--persona", help="posture: a detection-only persona hard-disables serving")
    po.add_argument("--template", help="path to your own portal page (imitates no real brand)")
    po.add_argument("--port", type=int, default=8088)
    po.add_argument("--bind-scope", default="localhost",
                    choices=["localhost", "lan", "tailscale", "auto"])
    posub = po.add_subparsers(dest="portal_cmd", required=True)
    posub.add_parser("plan", help="preview what would be served (no server runs)")
    pos = posub.add_parser("serve", help="stand up the portal (requires --authorized)")
    pos.add_argument("--authorized", action="store_true",
                     help="confirm this is an engagement you are permitted to run")
    po.set_defaults(func=cmd_portal)

    dm = sub.add_parser("demo", help="hands-off pass (gate 1.6): arrange radios → capture, one command")
    dm.add_argument("--intent", default="recon", choices=[i.value for i in Intent],
                    help="declared intent (default recon; hunt = active capture)")
    dm.add_argument("--live", action="store_true", help="probe the real radios on the Pi (default: dry run)")
    dm.add_argument("--persona", help="posture (red/purple = offense; blue/recon = detection-only)")
    dm.add_argument("--offense", action="store_true", help="enable offense without a persona (still Scope-gated)")
    dm.add_argument("--seconds", type=float, default=30.0, help="capture duration (default 30)")
    dm.add_argument("--no-engine", action="store_true", help="dry-run as if no capture engine present")
    dm.add_argument("--json", action="store_true", help="emit the structured plan as JSON")
    dm.add_argument("--scope-file", default=_DEFAULT_SCOPE_PATH)
    dm.set_defaults(func=cmd_demo)

    bp_ = sub.add_parser("benchpass", help="the one lump-sum hardware session (prep/run/report)")
    bpsub = bp_.add_subparsers(dest="benchpass_cmd", required=False)
    bpp = bpsub.add_parser("prep", help="no hardware: gear list + ready gates + command sequence")
    bpp.add_argument("--json", action="store_true")
    bpf = bpsub.add_parser("preflight", help="on the Pi: check prerequisites before starting")
    bpf.add_argument("--json", action="store_true")
    bpr = bpsub.add_parser("run", help="walk every gate on the Pi; record pass/fail/skip (resumable)")
    bpr.add_argument("--ledger", default="", help="resumable progress file (default benchpass-<board>.json)")
    bpr.add_argument("--board", default="", help="board under test, e.g. pi4 / pi5 (done = both pass)")
    bpr.add_argument("--resume", action="store_true", help="skip gates already recorded")
    bpr.add_argument("--only", nargs="*", help="limit to these gate ids (e.g. 1.6 2b.6)")
    bpa = bpsub.add_parser("auto", help="auto-grade every machine-checkable gate; prompt only for physical steps")
    bpa.add_argument("--ledger", default="", help="resumable progress file (default benchpass-<board>.json)")
    bpa.add_argument("--board", default="", help="board under test, e.g. pi4 / pi5 (done = both pass)")
    bpa.add_argument("--resume", action="store_true", help="skip gates already recorded")
    bpa.add_argument("--only", nargs="*", help="limit to these gate ids (e.g. 1.6 2b.6)")
    bpa.add_argument("--yes", action="store_true", help="auto-skip (not fail) gates that need a physical action you can't do now")
    bpa.add_argument("--markdown", action="store_true", help="also print a paste-ready results block at the end")
    bprep = bpsub.add_parser("report", help="render the consolidated result from the ledger")
    bprep.add_argument("--ledger", default="", help="progress file (default benchpass-<board>.json)")
    bprep.add_argument("--board", default="", help="board name (selects the default ledger)")
    bprep.add_argument("--markdown", action="store_true", help="emit a paste-ready results block for IMAGE_BUILD.md")
    bprep.add_argument("--image-sha", default="", help="image SHA256 to record in the markdown block")
    bprep.add_argument("--model", default="", help="board model label for the markdown heading")
    bprep.add_argument("--json", action="store_true")
    bpm = bpsub.add_parser("matrix", help="cross-board view: done only when every board passes")
    bpm.add_argument("--boards", nargs="*", default=["pi4", "pi5"], help="boards to combine (default pi4 pi5)")
    bpm.add_argument("--json", action="store_true")
    bp_.set_defaults(func=cmd_benchpass)

    ct = sub.add_parser("captap", help="raw 802.11 tap — probe/deauth parsing + cross-MAC re-id")
    ctsub = ct.add_subparsers(dest="captap_cmd", required=True)
    ctsub.add_parser("demo", help="synthetic frames: re-identify a phone across MAC rotation")
    cl = ctsub.add_parser("live", help="live monitor-mode capture → re-id + flood detectors (needs hardware)")
    cl.add_argument("--iface", required=True, help="monitor-mode interface (e.g. wlan1mon)")
    cl.add_argument("--seconds", type=float, default=30.0, help="stop after N seconds (default 30)")
    cl.add_argument("--max", type=int, default=None, help="stop after N frames")
    cl.add_argument("--channel", type=int, default=None, help="park the receiver on this channel (one number)")
    cl.add_argument("--hop", action="store_true", help="sweep ALL channels across every band (2.4+5GHz); nothing on an off-channel or 5GHz is missed")
    cl.add_argument("--bands", action="append", choices=("2.4", "5"), help="narrow the sweep to these band(s); repeatable (default: all)")
    cl.add_argument("--channels", default=None, help="override the sweep set, e.g. 1,6,11,36,149 (default: all bands)")
    cl.add_argument("--dwell", type=float, default=2.0, help="seconds to linger on each channel while sweeping (default 2.0)")
    ct.set_defaults(func=cmd_captap)

    sv = sub.add_parser("survey", help="passive situational-awareness sweep — inventory APs in earshot (read-only, no transmit)")
    sv.add_argument("--iface", default=None, help="monitor-mode interface (e.g. wlan1mon); omit with --demo")
    sv.add_argument("--seconds", type=float, default=20.0, help="listen for N seconds (default 20)")
    sv.add_argument("--max", type=int, default=None, help="stop after N frames")
    sv.add_argument("--channel", type=int, default=None, help="park on one channel instead of sweeping all bands")
    sv.add_argument("--bands", action="append", choices=("2.4", "5"), help="narrow the sweep to these band(s); repeatable (default: all)")
    sv.add_argument("--channels", default=None, help="override the sweep set, e.g. 1,6,11,36,149 (default: all bands)")
    sv.add_argument("--dwell", type=float, default=2.0, help="seconds to linger on each channel while sweeping (default 2.0)")
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
    husub.add_parser("track", help="scope-bounded location trail: log fixes over time "
                                   "for ARMED targets only (bystanders never logged)")
    hu.set_defaults(func=cmd_hunt)

    me = sub.add_parser("mesh", help="off-grid swarm — authenticated distributed Scope sync")
    mesub = me.add_subparsers(dest="mesh_cmd", required=True)
    mesub.add_parser("demo", help="3-node swarm: propagate an arm + reject a forged delta")
    mesub.add_parser("swarm", help="coordinated capture: place the chain across nodes, "
                                   "each re-gates locally before it fires")
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
    lm = scsub.add_parser("lab-mode",
                          help="your-own-lab switch: approve firing on sight (RF + private nets), each logged")
    lm.add_argument("state", nargs="?", default="status", choices=("on", "off", "status"),
                    help="on | off | status (default: status)")
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
