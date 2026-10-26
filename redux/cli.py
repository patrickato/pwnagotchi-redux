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

def _live_snapshot(port=8080, timeout=2.0):
    """The running daemon's REAL status snapshot from its loopback API, or None if
    no daemon is listening / the response is unusable.

    This is device truth. Building a fresh local Augur instead (see cmd_status)
    yields a no-hardware STUB — onboard-only radios, no live engine — that reports
    `capture_iface=wlan0 / capture_engine=null / face=blind` even while the daemon
    is happily capturing on the real adapter. That stub masquerading as device
    state is a glass-box lie, so every status/doctor path prefers this first."""
    from urllib.request import urlopen
    from urllib.error import URLError
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 1 <= port <= 65535:
        return None
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=timeout) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            return None
        snap = json.loads(raw)
        return snap if isinstance(snap, dict) else None
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None


def cmd_status(args) -> int:
    # A live supervisor owns the real capture radio + engine. Recomputing a fresh
    # local snapshot here is a no-hardware STUB that flatly contradicts a running
    # daemon (the "blind on wlan0" trap). Prefer the daemon's real status; fall back
    # to the stub only when nothing is listening, and LABEL it so it can never be
    # mistaken for device state.
    port = int(getattr(args, "port", 8080) or 8080)
    live = None if getattr(args, "no_daemon", False) else _live_snapshot(port)
    if live is not None:
        live.setdefault("source", "live-daemon")
        print(json.dumps(live, indent=2))
        return 0
    snap = _build(args).status()
    snap["source"] = ("offline-stub — no live daemon on 127.0.0.1:%d; this is a "
                      "no-hardware snapshot, NOT device state "
                      "(run on the device, or use 'redux live status')") % port
    print(json.dumps(snap, indent=2))
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
    if getattr(args, "dna", False):
        # Device DNA (#85): behavioral re-id past MAC randomization. Extends the
        # exact-match reid above to a SIMILARITY match, catching devices that
        # rotated MAC AND sent no PNL. Live traces come from the raw-frame tap;
        # --demo proves the linker over synthetic behavior.
        from .dex import DnaLinker, BehaviorTrace
        link = DnaLinker()
        if getattr(args, "demo", False):
            common = dict(timestamps=(0.0, 10.0, 20.0, 30.0), rates=(1, 2, 5, 11),
                          channels=(1, 6, 11), ie_hash="cafebabe")
            link.add(BehaviorTrace("a2:11:11:11:11:11", **common))
            link.add(BehaviorTrace("de:22:22:22:22:22", **common))   # same device, new MAC
            link.add(BehaviorTrace("ff:00:00:00:00:99", rates=(6, 9, 12, 54),
                                   channels=(36,), ie_hash="0ther", timestamps=(0.0, 60.0)))
            print("redux dex --dna — SYNTHETIC behavioral traces (no radio)")
        else:
            print("redux dex --dna — live behavioral DNA needs the raw-frame tap feeding "
                  "traces (timing/rates/IE); run with --demo to see the linker.")
            return 0
        s = link.summary()
        print(f"Device DNA: {s['macs_seen']} MAC(s) → {s['devices']} device(s) · "
              f"{s['reidentified']} re-identified by behavior")
        for d in link.identities():
            if d.reidentified:
                print(f"  [DEVICE conf={d.confidence:.2f}] {len(d.macs)} MACs → one device "
                      f"(matched on {', '.join(d.matched_on)}): {', '.join(sorted(d.macs))}")
        print(f"  {s['note']}")
        return 0
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
        if s.get("by_vendor"):
            print(f"  identified: {s['identified_vendors']} ({', '.join(f'{k}={v}' for k, v in s['by_vendor'].items())})")
        if s.get("randomized"):
            print(f"  randomized MACs (privacy/phone): {s['randomized']}")
        print("  rarest:")
        for e in dex.rarest(limit=args.top):
            print(f"    [{e.rarity.value:9}] {e.kind:4} {e.ssid or e.mac} — known {e.known_days:.0f}d")
        dep = dex.departed()
        if dep:
            print("  departed:")
            for e in dep[:args.top]:
                print(f"    {e.ssid or e.mac} — {e.reason}")
    return 0


def cmd_ir(args) -> int:
    """Infrared: TV-B-Gone power blasting + a loadable IR code library (import
    Flipper .ir). Transmit is POWER-tier (LAB + --confirm). Needs an IR LED on
    GPIO + LIRC on the device; this prints the gated command/plan."""
    from . import ir as IR
    from .core.mode import read_mode
    lib = IR.IRLibrary(args.lib)
    cmd = args.ir_cmd
    if cmd == "codes":
        for c in IR.TV_POWER_CODES:
            print(f"  {c.name:<24} {c.protocol}")
        return 0
    if cmd == "list":
        codes = lib.list()
        if not codes:
            print(f"no IR codes saved ({args.lib}) — import with `redux ir import <file.ir>`")
            return 0
        for c in codes:
            print(f"  {c.name:<24} {c.protocol or c.kind}")
        return 0
    if cmd == "import":
        try:
            n = lib.import_flipper(Path(args.file).read_text(errors="replace"))
        except OSError as e:
            print(f"cannot read {args.file}: {e}"); return 3
        print(f"imported {n} IR code(s) into {args.lib}")
        return 0
    lab = read_mode(getattr(args, "scope_file", None)) == "lab"
    if cmd == "blast":
        p = IR.blast_plan(lab_mode=lab, confirmed=bool(args.confirm))
        print(p.reason)
        if p.runnable:
            print("\nrun this (cage only):\n  " + " ".join(p.argv))
        return 0 if p.runnable else 3
    if cmd == "replay":
        code = lib.get(args.name)
        if code is None:
            print(f"no IR code '{args.name}' (see `redux ir list`)"); return 3
        p = IR.replay_plan(code, lab_mode=lab, confirmed=bool(args.confirm))
        print(p.reason)
        if p.runnable:
            print("\nrun this:\n  " + " ".join(p.argv))
        return 0 if p.runnable else 3
    return 2


def cmd_subghz(args) -> int:
    """Sub-GHz signal library: capture/catalog (RX, works with RTL-SDR) and gated
    replay (POWER-tier: LAB + confirm + a TX backend). For junkyard keyfobs,
    garage remotes, ISM gadgets below 1 GHz."""
    from .sdr.subghz import (SubGhzLibrary, capture_command, replay_plan,
                             detect_tx_backend, parse_freq as _parse_freq)
    lib = SubGhzLibrary(args.lib)
    cmd = args.subghz_cmd
    if cmd == "capture":
        fhz = _parse_freq(args.freq)
        argv = capture_command(fhz, seconds=args.seconds, out_path=args.out or "",
                               device=args.device, decode=not args.raw)
        print(f"capture @ {fhz/1e6:g} MHz ({'raw IQ' if args.raw else 'decode/catalog'}):")
        print("  " + " ".join(argv))
        if not args.raw:
            print("\nthen add decoded signals to the library with `redux subghz add`")
        return 0
    if cmd == "list":
        sigs = lib.list()
        if not sigs:
            print(f"no signals in the library ({args.lib})")
            return 0
        for s in sigs:
            roll = " [ROLLING — won't replay]" if s.rolling else ""
            print(f"  {s.id}  {s.name:<16} {s.freq_hz/1e6:g} MHz  {s.protocol or s.modulation}{roll}")
        return 0
    if cmd == "add":
        s = lib.add(args.name, _parse_freq(args.freq), protocol=args.protocol or "",
                    capture_path=args.capture or "", note=args.note or "")
        print(f"added {s.id} '{s.name}' @ {s.freq_hz/1e6:g} MHz"
              + (" (looks rolling-code — replay won't work)" if s.rolling else ""))
        return 0
    if cmd == "remove":
        print("removed" if lib.remove(args.id) else f"no signal '{args.id}'")
        return 0
    if cmd == "replay":
        from .core.mode import read_mode
        sig = lib.get(args.id)
        if sig is None:
            print(f"no signal '{args.id}' (see `redux subghz list`)")
            return 3
        lab = read_mode(getattr(args, "scope_file", None)) == "lab"
        p = replay_plan(sig, lab_mode=lab, confirmed=bool(args.confirm),
                        backend=getattr(args, "backend", None))
        print(p.reason)
        if p.runnable:
            print("\nrun this (cage only):\n  " + " ".join(p.argv))
        else:
            be = detect_tx_backend()
            if be is None:
                print("  (no TX backend detected — capture/catalog still work)")
        return 0 if p.runnable else 3
    return 2


def cmd_toolbox(args) -> int:
    """Curated catalog of useful, safe tools and the exact command to add each —
    the easy 'wire in C' path. `install` prints the command (--run executes it)."""
    from . import toolbox as TB
    cmd = args.toolbox_cmd
    if cmd == "list":
        rows = TB.find(getattr(args, "query", "") or "", category=getattr(args, "category", None))
        if not rows:
            print("no matching tools")
            return 0
        print("  ✓ = installed on this device   · = not found (add with `redux toolbox install <name>`)")
        present = 0
        for t in rows:
            here = TB.is_installed(t)
            present += here
            mark = "✓" if here else "·"
            en = f"  -> {t.enables}" if t.enables else ""
            print(f"  {mark} [{t.category:<9}] {t.name:<13} {t.summary}{en}")
        print(f"\n  {present}/{len(rows)} installed")
        return 0
    if cmd == "show":
        t = TB.get(args.name)
        if t is None:
            print(f"unknown tool '{args.name}' (see `redux toolbox list`)")
            return 3
        state = "installed ✓" if TB.is_installed(t) else "not installed"
        baked = " (ships in the stock image)" if t.baked else ""
        print(f"{t.name} — {t.summary}\n  category: {t.category}\n  status:   {state}{baked}")
        if t.enables:
            print(f"  enables:  {t.enables}")
        if t.needs_hw:
            print(f"  needs:    {t.needs_hw}")
        if t.note:
            print(f"  note:     {t.note}")
        print("  install:")
        for c in t.install:
            print(f"    {c}")
        return 0
    if cmd == "install":
        cmds = TB.install_commands(args.name)
        if cmds is None:
            print(f"unknown tool '{args.name}' (see `redux toolbox list`)")
            return 3
        if not getattr(args, "run", False):
            print(f"to add {args.name}, run:")
            for c in cmds:
                print(f"  {c}")
            print("\n(or re-run with --run to execute on this device)")
            return 0
        import subprocess
        for c in cmds:
            if c.strip().startswith("#"):
                continue
            print(f"$ {c}")
            rc = subprocess.run(c, shell=True).returncode
            if rc != 0:
                print(f"command failed (exit {rc}) — stopping")
                return 3
        return 0
    return 2


def cmd_capabilities(args) -> int:
    """The honest option-space: every capability Augur ships, what each needs, and
    a live readiness state (ready / needs-hardware / tool-missing / planned). Reads
    the manifest and probes the real environment; links to toolbox (install) and
    guidance (how-to) rather than duplicating them."""
    import json as _json
    from .core import manifest as M
    cmd = args.capabilities_cmd
    if cmd == "list":
        feats = [f for f in M.list_features()
                 if not getattr(args, "category", None) or f.category == args.category]
        if not feats:
            print("no matching capabilities")
            return 0
        if getattr(args, "json", False):
            print(_json.dumps([M.readiness_record(f) for f in feats], indent=2))
            return 0
        print("  ✓ ready   ◐ needs hardware   ○ tool missing   · planned          🔒 = LAB power-tier")
        counts = {r: 0 for r in M.Readiness}
        for f in feats:
            state, reason = M.readiness(f)
            counts[state] += 1
            lock = " 🔒" if f.power_tier else "   "
            print(f"  {M.GLYPH[state]}{lock} [{f.category:<7}] {f.id:<16} {f.title}")
            if state in (M.Readiness.TOOL_MISSING, M.Readiness.PLANNED):
                print(f"          └─ {reason}")
        c = M.completion(feats)
        print(f"\n  {c['summary']}")
        return 0
    if cmd == "show":
        f = M.get(args.id)
        if f is None:
            print(f"unknown capability '{args.id}' (see `redux capabilities list`)")
            return 3
        if getattr(args, "json", False):
            print(_json.dumps(M.readiness_record(f), indent=2))
            return 0
        state, reason = M.readiness(f)
        print(f"{f.id} — {f.title}   [{M.GLYPH[state]} {state.value}]")
        print(f"  {f.summary}")
        print(f"  category: {f.category}" + ("   🔒 LAB power-tier (per-action confirm)" if f.power_tier else ""))
        print(f"  status:   {reason}")
        if f.needs_hardware:
            print(f"  hardware: {f.needs_hardware}")
        if f.binaries:
            print(f"  binaries: {', '.join(f.binaries)}")
        if f.imports:
            print(f"  python:   {', '.join(f.imports)}")
        if f.note and f.note != reason:
            print(f"  note:     {f.note}")
        if f.toolbox:
            from . import toolbox as TB
            cmds = TB.install_commands(f.toolbox)
            if cmds:
                print(f"  install:  {cmds[0]}" + ("  …" if len(cmds) > 1 else ""))
        if f.guidance:
            from . import guidance as G
            topic = G.get(f.guidance)
            if topic:
                print(f"  how-to:   `redux help {f.guidance}` — {topic.command or topic.title}")
        if f.related:
            print(f"  related:  {', '.join(f.related)}")
        return 0
    if cmd == "completion":
        c = M.completion()
        if getattr(args, "json", False):
            print(_json.dumps(c, indent=2))
            return 0
        print("Augur capability completion (evidence-based — a plan is never counted as ready)\n")
        order = [M.Readiness.READY, M.Readiness.NEEDS_HARDWARE, M.Readiness.TOOL_MISSING, M.Readiness.PLANNED]
        for r in order:
            print(f"  {M.GLYPH[r]} {r.value:<14} {c['by_state'][r.value]}")
        print(f"\n  {c['implemented']}/{c['total']} capabilities have a real code path; "
              f"{c['planned']} still planned")
        print(f"  {c['summary']}")
        return 0
    return 2


def cmd_simulate(args) -> int:
    """Boot the whole live stack with no Pi and no radios: a fake engine replays a
    recorded scenario through the REAL supervisor / Augur / Doctor / status-API /
    TFT model. `redux live status --port <p>` and the dashboard work against it
    unchanged — iterate on the integrated system between bench sessions."""
    import json as _json
    from .replay.device_sim import run_simulation, snapshot_once, DEFAULT_SCENARIO
    scenario = DEFAULT_SCENARIO
    if getattr(args, "scenario", None):
        try:
            loaded = _json.loads(open(args.scenario).read())
        except (OSError, ValueError) as error:
            print(f"could not read scenario '{args.scenario}': {error}")
            return 4
        if not isinstance(loaded, list):
            print("scenario must be a JSON list of {tag,time,data} events")
            return 4
        scenario = loaded
    if getattr(args, "once", False):
        print(_json.dumps(snapshot_once(scenario=scenario, ticks=args.ticks), indent=2))
        return 0
    port = args.port
    print(f"simulated Augur booting on 127.0.0.1:{port}  (Ctrl-C to stop)")
    print(f"  check it:  redux live status --port {port}")
    print(f"             redux live doctor --port {port}")
    print(f"  or open:   http://127.0.0.1:{port}/")
    try:
        run_simulation(scenario=scenario, web_port=port, seconds=args.seconds)
    except KeyboardInterrupt:
        print("\nsimulator stopped")
    return 0


def cmd_shadow(args) -> int:
    """Blue-shadow: what a defender would see from each action. With no action it
    lists every action's footprint (loudest first); with one it shows the detail +
    a quieter way to the same goal. Learn evasion by seeing the footprint."""
    import json as _json
    from .frameworks.blueshadow import shadow, all_shadows
    if getattr(args, "action", None):
        s = shadow(args.action)
        if getattr(args, "json", False):
            print(_json.dumps(s, indent=2)); return 0
        if not s.get("known"):
            print(s["reason"]); return 3
        print(f"{s['action']} — {s['label']}")
        print(f"  footprint: {s['footprint']}")
        print(f"  blue sees: {', '.join(s['seen_by']) if s['seen_by'] else 'nothing in the wireless suite'}")
        print(f"  ATT&CK: {', '.join(s['attack'])}   D3FEND: {', '.join(s['defend'])}   PTES: {s['ptes_phase']}")
        if s.get("stealthier"):
            print(f"  quieter: {s['stealthier']['action']} — {s['stealthier']['why']}")
        if s.get("note"):
            print(f"  note: {s['note']}")
        return 0
    rows = all_shadows()
    if getattr(args, "json", False):
        print(_json.dumps(rows, indent=2)); return 0
    print("what blue sees, per action (loudest first):")
    for s in rows:
        glyph = "🔊" if s["loud"] else "🔇"
        seen = ", ".join(s["seen_by"]) if s["seen_by"] else "—"
        alt = f"   → quieter: {s['stealthier']['action']}" if s.get("stealthier") else ""
        print(f"  {glyph} {s['action']:18} seen by: {seen}{alt}")
    return 0


def cmd_hardware(args) -> int:
    """What's plugged in, and what it unlocks. Enumerates USB devices, maps the
    recognized ones to the capability they add (+ its live readiness), and flags
    anything it doesn't recognize so an unknown dongle is never silently ignored."""
    import json as _json
    from .core.hwprofile import profile
    entries = profile()
    if getattr(args, "json", False):
        print(_json.dumps([e.to_dict() for e in entries], indent=2))
        return 0
    if not entries:
        print("no USB devices seen (or /sys unavailable here)")
        return 0
    known = [e for e in entries if e.known]
    unknown = [e for e in entries if not e.known]
    print("plugged in → what it unlocks:")
    for e in known:
        tag = f" [{e.state}]" if e.state else ""
        print(f"  ✓ {e.usb_id:12} {e.device:38} → {e.feature}{tag}")
    for e in unknown:
        print(f"  ? {e.usb_id:12} {e.device:38} → unrecognized (may add a capability)")
    print(f"\n  {len(known)} recognized, {len(unknown)} unknown. "
          f"See `redux capabilities list` for what each unlocked feature needs.")
    return 0


def cmd_stealth(args) -> int:
    """Stealth / OPSEC for Augur's OWN device. `mac` randomizes a radio's MAC so
    the box doesn't present a stable fingerprint (vendor-plausible by default).
    Dry by default (prints the new MAC + the exact commands); --apply executes."""
    from .radio.macrand import MacRandomizer, random_mac, set_mac_commands
    cmd = args.stealth_cmd
    if cmd == "quiet":
        from .core.stealth import QuietConfig
        path = getattr(args, "file", None) or "/captures/live/stealth.json"
        q = QuietConfig.load(path)
        sub = args.quiet_cmd
        if sub == "on":
            q.enabled = True
            if getattr(args, "budget", None) is not None:
                q.frame_budget = max(0, args.budget)
            q.save(path)
            print(f"QUIET/GHOST mode ON — passive-first, frame budget {q.frame_budget}, "
                  f"MAC randomize {'on' if q.randomize_mac else 'off'}; transmit needs confirm")
            return 0
        if sub == "off":
            q.enabled = False
            q.save(path)
            print("QUIET/GHOST mode OFF — existing scope/power gates apply as normal")
            return 0
        # status
        print(f"quiet: {'ON' if q.enabled else 'off'}  (prefer_passive={q.prefer_passive}, "
              f"frame_budget={q.frame_budget}, randomize_mac={q.randomize_mac})")
        return 0
    if cmd == "mac":
        if not getattr(args, "apply", False):
            mac = random_mac(mode=args.mode)
            print(f"would set {args.iface} -> {mac}  ({args.mode})")
            for c in set_mac_commands(args.iface, mac):
                print("  " + " ".join(c))
            print("\n(re-run with --apply to change it on this device; needs root)")
            return 0
        res = MacRandomizer(mode=args.mode).randomize(args.iface)
        print(res.reason)
        return 0 if res.ok else 3
    if cmd == "pace":
        # Low-and-slow planner (#92): show WHEN a run of actions would fire to stay
        # under the footprint cap, honouring Quiet mode. Glass-box, no radio.
        from .core.lowslow import LowAndSlowScheduler, LowSlowConfig
        from .core.stealth import QuietConfig
        cfg = LowSlowConfig(window_seconds=args.window, max_weighted_per_window=args.cap,
                            min_gap_seconds=args.min_gap, jitter_seconds=args.jitter)
        sched = LowAndSlowScheduler(config=cfg, seed=args.seed)
        quiet = None
        if getattr(args, "quiet", False):
            quiet = QuietConfig.load(getattr(args, "file", None) or "/captures/live/stealth.json")
        print(f"low-and-slow plan — {args.count}x {args.kind}, cap {cfg.max_weighted_per_window}/"
              f"{int(cfg.window_seconds)}s, gap {cfg.min_gap_seconds}+<={cfg.jitter_seconds}s jitter"
              + (" [quiet ON]" if quiet and quiet.enabled else ""))
        t, fired, guard = 0.0, 0, 0
        last_reason = ""
        while fired < args.count and guard < args.count * 2000 + 20:
            guard += 1
            d = sched.permit(t, kind=args.kind, quiet=quiet, confirmed=args.confirm)
            if d.allowed:
                sched.record(t, kind=args.kind)
                fired += 1
                mm, ss = divmod(int(t), 60)
                print(f"  #{fired:<3} t+{mm:02d}:{ss:02d}  (window {sched.window_weight(t)}/{cfg.max_weighted_per_window})")
                t = sched._next_allowed
            else:
                last_reason = d.reason
                if d.reason.lower().startswith("quiet"):     # hard block — Quiet denies it
                    break
                t += max(0.5, d.wait_seconds)
        if fired == 0:
            print(f"  nothing scheduled — {last_reason}")
            return 0
        span = int(t)
        print(f"  -> {fired} action(s) spread over {span // 60}m{span % 60:02d}s; {sched.status(t)['note']}")
        return 0
    return 2


def cmd_notebook(args) -> int:
    """Lab notebook: a session journal for learning — log what you did and render
    a clean report of the session."""
    from .notebook import LabNotebook
    nb = LabNotebook(args.path)
    cmd = args.notebook_cmd
    if cmd == "log":
        e = nb.record(args.kind, args.summary)
        print(f"logged [{e.kind}] {e.summary}")
        return 0
    if cmd == "show":
        import time as _t
        es = nb.entries()
        if not es:
            print(f"notebook empty ({args.path})")
            return 0
        for e in es:
            ts = _t.strftime("%H:%M:%S", _t.localtime(e.ts))
            print(f"  {ts}  [{e.kind:<8}] {e.summary}")
        return 0
    if cmd == "report":
        rep = nb.report(title=args.title)
        if getattr(args, "out", None):
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(rep)
            print(f"wrote report: {args.out}")
        else:
            print(rep)
        return 0
    if cmd == "clear":
        print(nb.clear())
        return 0
    return 2


def cmd_tpms(args) -> int:
    """Re-identify returning vehicles by their tire-pressure sensor IDs, from an
    rtl_433 JSON capture (receive-only recon)."""
    import json as _json
    from .sdr.tpms import reidentify
    recs = []
    try:
        with open(args.file, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = _json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    recs.append(r)
    except OSError as e:
        print(f"cannot read {args.file}: {e}")
        return 3
    vehicles = reidentify(recs, min_gap_s=args.min_gap)
    if not vehicles:
        print(f"no TPMS sensors found in {args.file}")
        return 0
    returning = [v for v in vehicles if v.returning]
    if returning:
        print(f"⚠ {len(returning)} returning vehicle(s):")
    for v in vehicles:
        mark = "⚠ " if v.returning else "  "
        p = f"  {v.last_pressure_kpa:.0f} kPa" if v.last_pressure_kpa else ""
        print(f"{mark}{v.sensor_id:<12} {v.model:<16} ×{v.count}{p}  — {v.reason}")
    return 0


def cmd_signals(args) -> int:
    """The Signal Dex: one catalog of everything seen or captured across every
    radio/medium — WiFi/BLE/ISM/ADS-B/pager/ACARS sightings + sub-GHz + IR."""
    from .dex.signal_dex import build_signal_dex
    from .geo import SightingStore, DEFAULT_DB_PATH
    from .sdr.subghz import SubGhzLibrary
    from .ir import IRLibrary
    store = SightingStore(args.db or DEFAULT_DB_PATH)
    sg = SubGhzLibrary(args.subghz_lib)
    ir = IRLibrary(args.ir_lib)
    view = build_signal_dex(sighting_store=store, subghz_lib=sg, ir_lib=ir)
    s = view.summary
    print(f"Signal Dex: {s['total']} signal(s) across {len(s['by_domain'])} medium(s)")
    if s["total"]:
        print("  by medium: " + ", ".join(f"{k}={v}" for k, v in s["by_domain"].items()))
    only = getattr(args, "domain", None)
    shown = view.by_domain(only) if only else view.entries
    for e in shown[:args.limit]:
        print(f"  [{e.domain:<7}] {e.identifier:<20} {e.label[:24]:<24} {e.detail}")
    if not shown:
        print("  (nothing catalogued yet)")
    return 0


def cmd_gps(args) -> int:
    """Read the current position from gpsd (the standard Linux GPS daemon).
    Works for USB/serial/Bluetooth GPS once gpsd owns it. Glass-box."""
    from .geo.gpsd import GpsdClient
    host, port = args.host, args.port
    c = GpsdClient(host, port, timeout=args.timeout)
    fix = c.poll_fix()
    if fix is None:
        print(f"no fix from gpsd at {host}:{port} "
              "(is gpsd running and does it have a GPS lock?)")
        return 1
    print(f"fix: {fix.lat:.6f}, {fix.lon:.6f}  (via gpsd {host}:{port})")
    return 0


def cmd_here(args) -> int:
    """Temporal RF diff — what changed at this location since your last visit.

    A 'location' is a manual --label (works indoors / without GPS) or a geohash
    cell from --lat/--lon or a live gpsd fix. Reports NEW / GONE / CHANGED devices
    vs the last visit here, built from the sightings the device already recorded.
    Counter-surveillance in a box; glass-box throughout.
    """
    from .geo.db import SightingStore, DEFAULT_DB_PATH
    from .geo.location_memory import LocationMemory, rf_diff, location_key, build_entity_map, DEFAULT_MEMORY_PATH
    lat, lon, label = getattr(args, "lat", None), getattr(args, "lon", None), getattr(args, "label", None)
    if not label and (lat is None or lon is None):
        try:
            from .geo.gpsd import GpsdClient
            fix = GpsdClient(args.host, args.port, timeout=args.timeout).poll_fix()
            if fix is not None:
                lat, lon = fix.lat, fix.lon
        except Exception:
            pass
    key = location_key(lat=lat, lon=lon, label=label, precision=args.precision)
    if key is None:
        print("no location — pass --label <name>, or --lat/--lon, or run gpsd with a fix")
        return 2
    since = None
    if getattr(args, "window_hours", None):
        import time as _t
        since = _t.time() - args.window_hours * 3600
    # Optional: de-randomize via the probe PNL so a device returning under a fresh
    # MAC isn't miscounted as GONE+NEW (reid entity resolution).
    entities = None
    pnl = getattr(args, "pnl", None)
    if pnl:
        try:
            from .portal.karma import ProbeHarvester
            entities = build_entity_map(ProbeHarvester(path=pnl).clients())
        except Exception:
            entities = None
    d = rf_diff(SightingStore(args.db or DEFAULT_DB_PATH),
                LocationMemory(args.memory or DEFAULT_MEMORY_PATH),
                key=key, precision=args.precision, rssi_delta=args.rssi_delta,
                since=since, save=not args.no_save, entities=entities)
    if entities:
        print(f"(de-randomized via {len(set(entities.values()))} known device identity/ies from the probe PNL)")
    print(d.summary)
    if d.first_visit:
        return 0
    if d.new:
        print(f"\nNEW ({len(d.new)}):")
        for x in d.new:
            print("  + " + x.label())
    if d.gone:
        print(f"\nGONE ({len(d.gone)}):")
        for x in d.gone:
            print("  - " + x.label())
    if d.changed:
        print(f"\nCHANGED ({len(d.changed)}):")
        for c in d.changed:
            print(f"  ~ {c.kind} {c.ssid or c.mac}: {c.why}")
    if not (d.new or d.gone or d.changed):
        print("  (nothing changed since last visit)")
    return 0


def cmd_power(args) -> int:
    """Power-tier gate: the strongest tools (RF TX/replay, sub-GHz, IR, BadUSB)
    are hidden in FIELD, LAB-only, and need a per-action confirmation even in LAB.
    `list` shows what's available in a mode; `check` shows the gate decision."""
    from .core import power_tier as P
    from .core.mode import read_mode
    mode = getattr(args, "mode", None) or read_mode(getattr(args, "scope_file", None))
    if args.power_cmd == "list":
        vis = P.visible_in(mode)
        print(f"capabilities available in {mode.upper()} mode:\n")
        for c in vis:
            tag = " (needs confirm)" if c.needs_confirmation else ""
            print(f"  [{c.tier.value:<6}] {c.id:<14} {c.label}{tag}")
        hidden = [c for c in P._REGISTRY.values() if c not in vis]
        if hidden:
            print("\nnot available here:")
            for c in hidden:
                why = "FORBIDDEN (never)" if c.tier == P.Tier.FORBIDDEN else "POWER — LAB only"
                print(f"  [{why}] {c.id:<14} {c.risk}")
        return 0
    if args.power_cmd == "check":
        lab = mode.strip().lower() == "lab"
        ok, why = P.authorize_power(args.capability, lab_mode=lab,
                                    confirmed=bool(getattr(args, "confirm", False)))
        print(("ALLOW: " if ok else "REFUSE: ") + why)
        return 0 if ok else 3
    return 2
def cmd_oui(args) -> int:
    """MAC vendor lookup / device classification / full-DB import (glass-box).
    Built-in table out of the box; `import` merges a full IEEE/manuf DB on top."""
    from .geo import oui as O
    cmd = args.oui_cmd
    if cmd == "lookup":
        table = O.full_table()
        print(O.label_mac(args.mac, table) or "(not a MAC)")
        return 0
    if cmd == "classify":
        c = O.classify(args.mac, O.full_table())
        print(json.dumps(c.to_dict(), indent=2))
        return 0
    if cmd == "import":
        n, reason = O.import_ieee(args.source, args.dest)
        print(reason)
        return 0 if n else 3
    if cmd == "stats":
        print(json.dumps(O.table_stats(O.full_table()), indent=2))
        return 0
    return 2


def cmd_learn(args) -> int:
    """The experience layer: what has actually worked, with honest sample sizes.
    Real data only — numbers always carry their trial count; untried options are
    flagged (and explored), never fabricated."""
    from .core.experience import ExperienceStore, DIMENSIONS
    store = ExperienceStore(args.path)
    cmd = args.learn_cmd
    if cmd == "summary":
        print(json.dumps(store.summary(), indent=2))
        return 0
    if cmd in ("stats", "rank"):
        dim = args.dim
        if dim not in DIMENSIONS:
            print(f"unknown dimension '{dim}' (choose {', '.join(DIMENSIONS)})")
            return 3
        cands = None
        if cmd == "rank" and getattr(args, "candidates", None):
            cands = [c.strip() for c in args.candidates.split(",") if c.strip()]
            # numeric dims (channel) compare as ints
            if dim in ("channel",):
                cands = [int(c) if c.isdigit() else c for c in cands]
        from .core.experience import rank
        stats = rank(store.load(), dim, candidates=cands)
        if not stats:
            print(f"no experience recorded for '{dim}' yet")
            return 0
        for s in stats:
            print(f"  {s.reason}")
        return 0
    if cmd == "reset":
        print(store.reset())
        return 0
    return 2


def cmd_karma(args) -> int:
    """Probe harvesting (passive) + aimed karma planning (scope-gated). Harvest
    runs in any mode; advertising is aimed only at in-scope clients, never spam."""
    from .portal.karma import ProbeHarvester, karma_plan
    h = ProbeHarvester(path=args.store)
    cmd = args.karma_cmd
    if cmd == "clients":
        rows = h.clients()
        if not rows:
            print(f"no probes harvested yet ({args.store})")
            return 0
        for r in rows:
            print(f"  {r['client']}  probes={r['probes']:<4} PNL: {', '.join(r['ssids'])}")
        return 0
    if cmd == "ssids":
        rows = h.ssids()
        if not rows:
            print(f"no probed SSIDs yet ({args.store})")
            return 0
        for r in rows:
            print(f"  {r['ssid']!r:28} wanted by {r['client_count']} client(s)")
        return 0
    if cmd == "plan":
        from .core.scope import Scope, default_scope_path
        scope = Scope.load(args.scope_file or default_scope_path())
        plan = karma_plan(h, scope)
        print(json.dumps(plan.to_dict(), indent=2))
        return 0
    return 2


def cmd_reid(args) -> int:
    """Re-identify physical devices across randomized MACs from harvested probe
    PNLs. Links MACs that share distinctive saved-network names (weighted by
    rarity) — the PNL belongs to the device, not the rotating MAC. Glass-box:
    every linked device prints the exact SSID evidence and a confidence."""
    from .portal.karma import ProbeHarvester
    from .geo.reid import reidentify
    from .geo.mac_normalize import is_locally_administered
    clients = ProbeHarvester(path=args.store).clients()
    if not clients:
        print(f"no probes harvested yet ({args.store}) — run passive probe harvesting first")
        return 0
    entities = reidentify(clients, min_link_weight=args.min_weight,
                          max_common_fraction=args.max_common_fraction)
    # Enrich into dossiers by joining each entity's MACs to the sightings store
    # (where/when/signal) and OUI (vendor). Fail-safe: absent/empty store -> no dossier.
    dossiers = {}
    db = getattr(args, "db", None)
    if db and entities:
        try:
            from .geo.db import SightingStore
            from .geo.reid import enrich_entities
            from .geo.oui import lookup_oui
            by_mac = {}
            for s in SightingStore(db).query():
                by_mac.setdefault((s.mac or "").lower(), []).append(s)
            for e, d in zip(entities, enrich_entities(entities, by_mac, vendor_lookup=lookup_oui,
                                                       precision=getattr(args, "precision", 7))):
                dossiers[e.macs] = d
        except Exception:
            dossiers = {}
    if args.json:
        rows = []
        for e in entities:
            row = {"macs": list(e.macs), "randomized": e.randomized,
                   "shared_ssids": list(e.shared_ssids), "probes": e.probes,
                   "confidence": e.confidence}
            d = dossiers.get(e.macs)
            if d:
                row["dossier"] = {"vendor": d.vendor, "sightings": d.sightings,
                                  "last_seen": d.last_seen, "strongest_rssi": d.strongest_rssi,
                                  "places": list(d.places)}
            rows.append(row)
        print(json.dumps(rows, indent=2))
        return 0
    if not entities:
        print(f"{len(clients)} client MAC(s) seen — no cross-MAC links "
              "(no shared distinctive probe SSIDs)")
        return 0
    linked = sum(len(e.macs) for e in entities)
    print(f"{len(entities)} re-identified device(s) spanning {linked} MACs "
          f"(of {len(clients)} seen):")
    for e in entities:
        print(f"\n  device: {len(e.macs)} MACs, {e.randomized} randomized  "
              f"[{e.confidence}]  {e.probes} probes")
        d = dossiers.get(e.macs)
        if d:
            print(f"    dossier: {d.summary}")
        print("    linked by PNL: " + (", ".join(e.shared_ssids[:8]) or "(chained)"))
        for m in e.macs:
            tag = " (randomized)" if is_locally_administered(m) else ""
            print(f"      {m}{tag}")
    return 0


def cmd_wps(args) -> int:
    """WPS / Pixie-Dust: passive `wash` discovery + scope-gated attack plan/run.
    Finding WPS APs is passive; attacking is gated (FIELD needs --authorized +
    scope; LAB approves on sight). Glass-box throughout."""
    from . import wps as W
    cmd = args.wps_cmd
    if cmd == "scan":
        with open(args.file, encoding="utf-8", errors="replace") as f:
            targets = W.parse_wash(f.read())
        if not targets:
            print(f"no WPS APs found in {args.file}")
            return 0
        for t in targets:
            lock = " [LOCKED]" if t.locked else ""
            print(f"  {t.bssid}  ch{t.channel or '?':<3} {t.ssid or '(hidden)'}{lock}")
        print(f"\n{len(targets)} WPS-enabled AP(s). Target one with `redux wps plan --bssid <b>`.")
        return 0
    # plan / run
    from .core.scope import Scope, default_scope_path
    scope = Scope.load(getattr(args, "scope_file", None) or default_scope_path())
    lab = bool(getattr(scope, "lab_mode", False))
    p = W.plan(W.WpsTarget(bssid=args.bssid.lower(), channel=args.channel, ssid=args.ssid or ""),
               scope, tool=args.tool, pixie=not args.no_pixie, iface=args.iface,
               authorized=bool(getattr(args, "authorized", False)), lab_mode=lab)
    if cmd == "plan":
        print(p.reason)
        if p.runnable:
            print("\ncommand:\n  " + " ".join(p.argv))
            if not W.tool_available(p.tool):
                print(f"\nnote: '{p.tool}' is not installed on this device")
        return 0 if p.runnable else 3
    if cmd == "run":
        res = W.run_plan(p, timeout=args.timeout)
        print(json.dumps(res, indent=2))
        return 0 if res.get("status") in ("recovered", "completed") else 3
    return 2
def cmd_tracker(args) -> int:
    """Unwanted-tracker check: which devices have been MOVING WITH YOU (persistent
    across time AND distance) — the Apple/Google 'is it following me' heuristic."""
    from .geo import follower_analysis
    from .geo.follower import ObservationLog
    rows = ObservationLog(args.log).load()
    if not rows:
        print(f"no observation history yet ({args.log}) — the live runtime logs BLE "
              "observations here as it runs")
        return 0
    verdicts = follower_analysis(
        rows, min_duration_s=args.min_minutes * 60.0, min_span_m=args.min_span_m)
    flagged = [v for v in verdicts if v.following]
    if args.all:
        show = verdicts
    else:
        show = flagged
    if not show:
        print(f"no devices flagged as following you ({len(verdicts)} device(s) analyzed)")
        return 0
    if flagged:
        print(f"⚠ {len(flagged)} device(s) may be following you:")
    for v in show:
        mark = "⚠ " if v.following else "  "
        print(f"{mark}{v.mac}  {v.reason}")
    return 0


def cmd_crack_distribute(args) -> int:
    """Split one hashcat job across the mesh: partition a wordlist (--skip/--limit)
    or a mask keyspace (-s/-l) into disjoint per-node slices. Glass-box; prints
    the exact command each node runs. Pure planning — dispatch rides the swarm."""
    from .crack.distributed import distribute, count_lines
    attack = int(args.attack)
    base = [args.binary, "-m", str(args.mode), "-a", str(attack), args.hashfile]
    if attack == 0:
        if not args.wordlist:
            print("refused: wordlist attack (-a 0) needs --wordlist")
            return 3
        try:
            total = count_lines(args.wordlist)
        except OSError as e:
            print(f"cannot read wordlist: {e}")
            return 3
        base.append(args.wordlist)
    elif attack == 3:
        if not args.mask or args.keyspace is None:
            print("refused: mask attack (-a 3) needs --mask and --keyspace "
                  "(get it from `hashcat -m <m> -a 3 <mask> --keyspace`)")
            return 3
        total = int(args.keyspace)
        base.append(args.mask)
    else:
        print(f"refused: distribute supports attack 0 (wordlist) or 3 (mask), not {attack}")
        return 3
    jobs = distribute(base, total=total, attack=attack, n_nodes=args.nodes)
    print(f"splitting {total} units across {args.nodes} node(s):\n")
    for j in jobs:
        print(f"  [{j.node_id}] {j.reason}")
        if j.argv:
            print("      " + " ".join(j.argv))
    return 0
def cmd_push(args) -> int:
    """Push-to-phone alerts via ntfy / Pushover / webhook. `test` sends a hello;
    `send` sends your title+message. Secrets are passed at call time, never
    stored. Notifies a short summary — never a content dump."""
    from .connectivity.push import PushConfig, PushSender
    cfg = PushConfig(service=args.service, target=args.target,
                     token=getattr(args, "token", "") or "")
    ok, reason = cfg.validate()
    if not ok:
        print(f"refused: {reason}")
        return 3
    sender = PushSender(cfg)
    if args.push_cmd == "test":
        res = sender.send("redux", "test notification from your device",
                          priority=3, tags="white_check_mark")
    else:  # send
        res = sender.send(args.title, args.message,
                          priority=args.priority, tags=getattr(args, "tags", "") or "")
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 3
def cmd_help(args) -> int:
    """In-product guidance for new users: info-bubbles, when-to-use, and how-to
    for every feature. The same content the TFT/web surfaces show."""
    from . import guidance as G
    if getattr(args, "search", None):
        hits = G.search(args.search)
        if not hits:
            print(f"no help topics match '{args.search}'")
            return 0
        for t in hits:
            print(f"  {t.id:<16} {t.bubble}")
        return 0
    if getattr(args, "topic", None):
        t = G.get(args.topic)
        if t is None:
            print(f"unknown topic '{args.topic}' — run `redux help` for the list")
            return 3
        print(f"# {t.title}\n")
        print(t.bubble + "\n")
        print(f"WHEN: {t.when}\n")
        print("HOW:")
        print(t.how)
        if t.command:
            print(f"\nTRY: {t.command}")
        if t.related:
            print(f"\nSEE ALSO: {', '.join(t.related)}")
        return 0
    print("Augur help — topics (run `redux help <topic>` for details):\n")
    for t in G.list_topics():
        print(f"  {t.id:<16} {t.title}")
    print("\nTip: `redux help <topic>` for a how-to, or `redux help --search <word>`.")
    return 0


def _print_doctor(rep, *, suffix="") -> None:
    print(f"redux doctor: {rep.get('label', rep.get('overall', 'unknown'))}{suffix}")
    for f in rep.get("findings", []):
        if not isinstance(f, dict):
            continue
        print(f"  [{str(f.get('status', 'unknown')).upper():8}] "
              f"{f.get('area', 'unknown')}: {f.get('summary', '')}")
        if f.get("reason"):
            print(f"             why: {f['reason']}")
        if f.get("remediation"):
            print(f"             do:  {f['remediation']}")
    cov = rep.get("coverage")
    print(f"coverage: {cov.get('reason', 'not reported') if isinstance(cov, dict) else 'not reported'}")


def cmd_doctor(args) -> int:
    """Print the headless, glass-box self-diagnosis.

    Prefers the running daemon's REAL Doctor (the live supervisor owns the actual
    radio/engine). Only when no daemon is listening does it fall back to a clearly
    labeled no-hardware stub — never a stub masquerading as device state."""
    port = int(getattr(args, "port", 8080) or 8080)
    live = None if getattr(args, "no_daemon", False) else _live_snapshot(port)
    if live is not None:
        rep = live.get("doctor")
        if isinstance(rep, dict) and isinstance(rep.get("findings"), list):
            _print_doctor(rep, suffix="  [live daemon]")
            return 0
    rep = _build(args).doctor_report()
    _print_doctor(rep, suffix=f"  [offline stub — no live daemon on :{port}]")
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

    if args.campaign_cmd == "unleashed":
        from .operator import autopilot
        from .core.authlog import record_event
        armed = args.targets or [e.value for e in scope.active_entries() if e.kind == "bssid"]
        if not armed:
            armed = ["00:11:22:33:44:55"]        # nothing armed -> a single demo target
        def _ok(name):
            return lambda t: (True, f"{name} ok on {t}")
        execs = {Phase.CAPTURE: _ok("capture"), Phase.CRACK: _ok("crack"),
                 Phase.JOIN: _ok("join"), Phase.PIVOT_SCAN: _ok("scan"),
                 Phase.PIVOT_CRED: _ok("cred-test"), Phase.LOOT: _ok("loot")}
        prioritize = None
        if getattr(args, "adaptive", False):
            try:
                from .operator import by_vendor_success
                from .core.experience import ExperienceStore
                from .geo.oui import lookup_oui
                rows = ExperienceStore(getattr(args, "experience", None) or "/captures/live/experience.jsonl").load()
                prioritize = by_vendor_success(rows, lookup_oui)
            except Exception:
                prioritize = None
        rep = autopilot(scope, target_source=lambda: armed, executors=execs,
                        recon=lambda: (True, f"{len(armed)} target(s) in cage"),
                        max_cycles=args.cycles, confirmed=args.confirm, logger=record_event,
                        prioritize=prioritize)
        if not rep.ran:
            print(f"UNLEASHED — not run [{rep.mode.upper()}]: {rep.reason}")
            return 2
        print(f"UNLEASHED [{rep.mode.upper()}] — {rep.reason}  "
              f"(executed {rep.total_executed}, blocked {rep.total_blocked} across {len(rep.cycles)} cycle(s))")
        print("  note: CLI run uses fake executors to prove the loop + gate; real RF runs under the daemon.")
        return 0

    if args.campaign_cmd == "plan":
        op = Operator(scope, offense_enabled=offense, caps={"capture.handshake": not args.no_engine})
        print(f"kill-chain plan (persona={args.persona or 'none'}, offense={offense}, "
              f"capture_engine={not args.no_engine}):")
        for s in op.plan(targets):
            print(f"  [{'ok   ' if s.allowed else 'BLOCK'}] {s.target:20} {s.phase.value:11} "
                  f"{s.action:18} — {s.reason}")
        return 0

    # demo: fake executors that succeed; capture engine + uplink assumed present
    op = Operator(scope, offense_enabled=offense,
                  caps={"capture.handshake": True, "radio.wifi.uplink": True})
    def _ok(name):
        return lambda t: (True, f"{name} ok on {t}")
    execs = {Phase.CAPTURE: _ok("capture"), Phase.CRACK: _ok("crack"),
             Phase.JOIN: _ok("join"), Phase.PIVOT_SCAN: _ok("scan"),
             Phase.PIVOT_CRED: _ok("cred-test"), Phase.LOOT: _ok("loot")}
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
    armed scope (scan -> optional cred-test -> loot). Runs real nmap/hydra when
    installed (Kali pack), honest tool-absence otherwise. Scope decides WHERE,
    always — nothing off-scope runs; hydra cred-testing is active and scope-gated."""
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


def _parse_freq(s):
    """Parse a frequency like '433.92M', '162.025M', '25k', or bare Hz -> float Hz."""
    t = str(s).strip().lower()
    mult = 1.0
    if t.endswith("m"):
        mult, t = 1e6, t[:-1]
    elif t.endswith("k"):
        mult, t = 1e3, t[:-1]
    elif t.endswith("g"):
        mult, t = 1e9, t[:-1]
    return float(t) * mult


def cmd_sdr(args) -> int:
    """Ingest SDR tool output into the sighting store so SDR captures reach geo/Dex:
    rtl_433 ISM records (tagged with the receiver's position) or dump1090 ADS-B
    records (the aircraft's own position). Reads line-delimited JSON, one record
    per line — exactly what `rtl_433 -F json` / `dump1090 --write-json` emit."""
    import json
    # Guided presets (no store needed): "pick what to listen to -> tuned command".
    if getattr(args, "sdr_cmd", None) in ("presets", "tune"):
        from .sdr.presets import list_presets, find_preset
        if args.sdr_cmd == "presets":
            for p in list_presets():
                freq = f" @ {p.to_dict().get('freq', '—')}" if p.freq_hz else ""
                print(f"  {p.name:<9} {p.label}{freq}")
                print(f"            {p.what}")
                if p.region_note:
                    print(f"            region: {p.region_note}")
            print("\nTune one with: redux sdr tune <preset> [--device N] [--freq 433.92M] [--scan-range 430M:440M:25k]")
            return 0
        preset = find_preset(args.preset)
        if preset is None:
            print(f"unknown preset '{args.preset}' — see `redux sdr presets`")
            return 3
        fhz = _parse_freq(args.freq) if getattr(args, "freq", None) else None
        cmd = preset.command(device_index=args.device, freq_hz=fhz,
                             duration_s=getattr(args, "duration", None),
                             scan_range=getattr(args, "scan_range", None) or None)
        print(f"{preset.label} — {preset.what}\n")
        if preset.help:
            print(preset.help + "\n")
        if preset.region_note:
            print(f"NOTE: {preset.region_note}\n")
        print("run this:\n  " + cmd.shell())
        _next = {
            "rtl433": "redux sdr ingest <file> --rtl433",
            "adsb": "redux sdr ingest <file> --adsb",
            "pocsag": "redux sdr ingest <file> --pocsag",
            "acars": "redux sdr ingest <file> --acars",
            "spectrum": "redux sdr spectrum <file>",
        }.get(cmd.decoder)
        if _next:
            print(f"\nthen: {_next}")
    # Waterfall: make the spectrum visible from an rtl_power sweep (no store).
    if getattr(args, "sdr_cmd", None) == "waterfall":
        from .sdr.waterfall import Waterfall
        with open(args.file, encoding="utf-8", errors="replace") as f:
            wf = Waterfall.from_rtl_power(f)
        rows, cols = wf.shape
        if rows == 0:
            print(f"no sweeps found in {args.file}")
            return 0
        print(wf.to_ascii(width=args.width))
        print(f"\n{rows} sweep(s) × {cols} bins, {wf.db_min:.0f}..{wf.db_max:.0f} dB")
        if args.out:
            with open(args.out, "wb") as fh:
                fh.write(wf.to_pgm())
            print(f"wrote grayscale image: {args.out} (PGM)")
        if getattr(args, "svg", None):
            with open(args.svg, "w", encoding="utf-8") as fh:
                fh.write(wf.to_svg(width=args.width))
            print(f"wrote SVG heatmap: {args.svg} (web/phone view)")
        return 0
    from .geo.db import SightingStore, DEFAULT_DB_PATH
    from .sdr import (ingest_rtl433, ingest_adsb, ingest_pocsag, ingest_acars,
                      parse_rtl_power)

    # `spectrum` is analysis only — no store needed.
    if getattr(args, "sdr_cmd", None) == "spectrum":
        with open(args.file, encoding="utf-8", errors="replace") as f:
            sweep = parse_rtl_power(f, peak_threshold_db=args.threshold, max_peaks=args.max_peaks)
        d = sweep.to_dict()
        print(json.dumps(d, indent=2))
        if sweep.peaks:
            print("\nactive signals (strongest first):")
            for p in sweep.peaks:
                print(f"  {p.describe()}")
        else:
            print("\nno signals above the noise floor in this sweep")
        return 0

    cfg = _cfg(args)
    db = args.db or (cfg.cache.db_path if cfg else None) or DEFAULT_DB_PATH
    store = SightingStore(db)
    pos = (args.lat, args.lon) if args.lat is not None and args.lon is not None else None

    def _lines(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line.rstrip("\n")

    def _records(path):
        for line in _lines(path):
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
        n, kind = ingest_adsb(_records(args.file), store), "ADS-B aircraft"
    elif getattr(args, "pocsag", False):
        n = ingest_pocsag(_lines(args.file), store, position=pos)
        kind = "POCSAG pager (capcode only; messages not stored)"
    elif getattr(args, "acars", False):
        n, kind = ingest_acars(_records(args.file), store, position=pos), "ACARS aircraft (text not stored)"
    else:
        n, kind = ingest_rtl433(_records(args.file), store, position=pos), "ISM device"
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
    from .report import (EngagementAction, build_report, render_markdown,
                         render_html, actions_from_authlog)
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
        authlog_integrity = None
        if getattr(args, "from_authlog", False):
            from .core import authlog as A
            log = A.AuthLog(args.authlog or A.default_authlog_path(),
                            key=A.load_key(args.key or A.default_key_path()))
            acts = actions_from_authlog(log.entries())
            vr = log.verify()
            authlog_integrity = {"ok": vr.ok, "reason": vr.reason,
                                 "signed": vr.signed, "count": vr.count}
        elif args.actions:
            raw = json.loads(open(args.actions).read())
            acts = [EngagementAction(float(e.get("ts", 0.0)), e.get("action", ""),
                                     e.get("target", ""), e.get("reason", ""), e.get("result", ""))
                    for e in raw]
        else:
            print("report build: pass --actions <file> or --from-authlog")
            return 2
        rep = build_report(args.engagement, args.operator, scope, acts,
                           sanitize=args.sanitize, authlog_integrity=authlog_integrity)
    md = render_markdown(rep)
    wrote = []
    if getattr(args, "out", None):
        with open(args.out, "w") as f:
            f.write(md)
        wrote.append(args.out)
    if getattr(args, "html", None):
        with open(args.html, "w") as f:
            f.write(render_html(rep))
        wrote.append(args.html)
    if wrote:
        print(f"wrote {', '.join(wrote)}  (integrity: {rep['integrity']}, "
              f"{rep['unauthorized_count']} out-of-scope)")
    else:
        print(md)
    return 0


def cmd_range(args) -> int:
    """Purple Range mode: `techniques` lists the ATT&CK→D3FEND map; `demo` runs a
    SIMULATED attack set and grades your detectors, naming the coverage gaps."""
    from .frameworks import attack_defend_pairs, run_exercise, range_report
    if args.range_cmd == "livefire":
        from .core.scope import Scope
        from .operator import Operator, Phase
        from .intel.livefire import live_fire
        scope = Scope.load(getattr(args, "scope_file", None) or _DEFAULT_SCOPE_PATH)
        targets = args.targets or [e.value for e in scope.active_entries() if e.kind == "bssid"] or ["00:11:22:33:44:55"]
        op = Operator(scope, offense_enabled=True, caps={"capture.handshake": True})
        out = op.run(targets, {ph: (lambda t: (True, "ok")) for ph in Phase if ph is not Phase.RECON},
                     recon=lambda: (True, "seen"))
        executed = [s["action"] for s in out["steps"] if s.get("executed") and s["phase"] != "recon"]
        fired = {a: (args.fired or []) for a in executed}        # blue detections (from --fired / sentinel)
        rep = live_fire(scope, executed_actions=executed, fired_by_action=fired, confirmed=args.confirm)
        if not rep.ran:
            print(f"LIVE-FIRE — not run [{rep.mode.upper()}]: {rep.reason}")
            return 2
        print(f"LIVE-FIRE [{rep.mode.upper()}] — {rep.reason}")
        for r in rep.results:
            print(f"  [{r['verdict'].upper():10}] {r['label']:28} {','.join(r['attack_ids'])}")
            if r["verdict"] in ("missed", "partial"):
                print(f"              {r['reason']}")
        c = rep.coverage or {}
        print(f"\nblue coverage: {c.get('reason', '(none)')}")
        print("note: CLI uses fake red executors + --fired for blue; a cage run wires the Operator + Sentinel.")
        return 0
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
    # Adaptive method pick: what the device's own experience says works best. The
    # methods available depend on what's present (AngryOxide binary, injection radio).
    try:
        from .crack.adaptive_capture import recommend_capture_method, method_table, CAPTURE_METHODS
        from .core.experience import ExperienceStore
        avail = list(CAPTURE_METHODS) if ao.available() else [m for m in CAPTURE_METHODS if m != "angryoxide"]
        rows = ExperienceStore(getattr(args, "experience", None)
                               or "/captures/live/experience.jsonl").load()
        pick, why = recommend_capture_method(rows, available=avail)
        print(f"\nadaptive method (from experience): {pick}")
        print(f"  {why}")
        tbl = method_table(rows, available=avail)
        if any(r["trials"] for r in tbl):
            for r in tbl:
                print(f"    {r['method']:13} {r['successes']}/{r['trials']}  score {r['score']}")
    except Exception:
        pass   # experience is optional; never let it break the capture plan
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
    from .core.authlog import record_event
    path = args.file
    scope = Scope.load(path)
    if args.scope_cmd == "lab-mode":
        if args.state in ("on", "off"):
            scope.lab_mode = (args.state == "on")
            scope.save(path)
            record_event("scope.lab_mode", {"state": args.state})
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
        record_event("scope.add", {"kind": e.kind, "value": e.value, "job": e.job,
                                   "label": e.label, "expires": e.expires})
        print(f"armed: {e.kind} {e.value}" + (f" [{e.job}]" if e.job else "") + f"  (scope now has {len(scope.active_entries())} active)")
    elif args.scope_cmd == "remove":
        n = scope.remove(args.target, job=args.job)
        scope.save(path)
        record_event("scope.remove", {"target": args.target, "job": args.job, "removed": n})
        print(f"removed {n} entr{'y' if n == 1 else 'ies'}")
    elif args.scope_cmd == "clear":
        n = scope.clear(job=args.job)
        scope.save(path)
        record_event("scope.clear", {"job": args.job, "removed": n})
        print(f"cleared {n} entr{'y' if n == 1 else 'ies'}" + (f" from job '{args.job}'" if args.job else " (whole scope)"))
    elif args.scope_cmd == "import":
        with open(args.listfile) as f:
            n = scope.bulk_load(f.read(), job=args.job or "")
        scope.save(path)
        record_event("scope.import", {"job": args.job, "added": n, "file": args.listfile})
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
            record_event("scope.arm_lab", {"auto": True, "armed": n})
            print(f"lab armed (auto): {n} of your own target(s) pre-authorized; "
                  f"scope now has {len(scope.active_entries())} active")
        else:
            n = scope.arm_lab(bssids=args.bssid or [], ssids=args.ssid or [], cidrs=args.cidr or [])
            scope.save(path)
            record_event("scope.arm_lab", {"auto": False, "armed": n})
            print(f"lab armed: {n} target(s) pre-authorized (no expiry); scope now has {len(scope.active_entries())} active")
    return 0


def cmd_authlog(args) -> int:
    """Chain-of-authorization log — tamper-evident, provable engagement record.
    Every scope change is recorded here automatically; `record` adds a manual
    note, `verify` proves the chain is intact (and signatures, with a key)."""
    from .core import authlog as A
    path = args.log or A.default_authlog_path()
    key_path = args.key or A.default_key_path()
    cmd = args.authlog_cmd
    if cmd == "init-key":
        try:
            A.generate_key(key_path, overwrite=args.force)
        except FileExistsError:
            print(f"a signing key already exists at {key_path}\n"
                  "  (use --force to replace it — WARNING: that invalidates every "
                  "existing signature, so past entries will no longer verify)")
            return 1
        print(f"signing key created at {key_path} (owner-only). New auth-log entries "
              "will be signed; back this key up — it proves the log came from this device.")
        return 0
    if cmd == "record":
        detail = {}
        for kv in (args.detail or []):
            k, _, v = kv.partition("=")
            if k:
                detail[k] = v
        entry = A.AuthLog(path, key=A.load_key(key_path)).record(args.event, detail)
        print(f"recorded #{entry.seq} {entry.event}  hash={entry.hash[:12]}…"
              + ("  (signed)" if entry.sig else "  (unsigned — run `redux authlog init-key` to sign)"))
        return 0
    if cmd == "verify":
        r = A.AuthLog(path, key=A.load_key(key_path)).verify()
        print(("OK    " if r.ok else "FAIL  ") + r.reason)
        return 0 if r.ok else 1
    if cmd == "show":
        entries = A.AuthLog(path).entries()
        if args.json:
            print(json.dumps([{"seq": e.seq, "ts": e.ts, "event": e.event,
                               "detail": e.detail, "hash": e.hash,
                               "signed": bool(e.sig)} for e in entries], indent=2))
            return 0
        if not entries:
            print(f"auth log is empty ({path})")
            return 0
        for e in entries:
            when = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(e.ts))
            det = "  ".join(f"{k}={v}" for k, v in sorted(e.detail.items()))
            mark = "✓" if e.sig else "·"
            print(f"  #{e.seq:<4} {when}  {mark} {e.event:18} {det}")
        return 0
    return 2


def cmd_scrub(args) -> int:
    """Secure-wipe / panic-scrub of the device's OWN collected data — never a
    target's. `status` shows your on-disk exposure; `plan` is a dry-run of what a
    mode would wipe vs keep; `run` executes (requires --apply). scrub keeps the
    brain (experience/wordlists/graph); panic burns it all but leaves the box
    bootable. DRY-RUN by default."""
    from .core.scrub import SecureWipe
    sw = SecureWipe(root=getattr(args, "root", None) or "/captures")
    mode = "panic" if getattr(args, "panic", False) else "scrub"
    cmd = args.scrub_cmd
    try:
        if cmd == "status":
            exp = sw.exposure()
            print(f"on-disk exposure — {exp['note']}")
            for tier, v in sorted(exp["by_tier"].items()):
                print(f"  {tier:17} {v['items']} item(s)  {v['bytes']} bytes")
            return 0
        if cmd == "plan":
            plan = sw.plan(mode)
            w = [i for i in plan["wipe"] if i.exists]
            print(f"scrub plan [{mode.upper()}] — would wipe {len(w)} item(s), keep the rest:")
            for i in w:
                print(f"  WIPE  [{i.tier:16}] {i.path}  ({i.size} B) — {i.reason}")
            for i in plan["keep"]:
                if i.exists:
                    print(f"  keep  [{i.tier:16}] {i.path}")
            print(f"  ({'panic burns the brain too; ' if mode == 'panic' else 'brain preserved; '}"
                  "run `redux scrub run" + (" --panic" if mode == 'panic' else "") + " --apply` to execute)")
            return 0
        if cmd == "run":
            res = sw.execute(mode, apply=getattr(args, "apply", False),
                             overwrite=not getattr(args, "no_overwrite", False))
            if not res["applied"]:
                print(f"scrub [{mode.upper()}] DRY RUN — would wipe {len(res['would_wipe'])} item(s); "
                      f"pass --apply to execute")
                for i in res["would_wipe"]:
                    print(f"  WIPE [{i['tier']}] {i['path']}")
                return 0
            print(f"scrub [{mode.upper()}] — wiped {res['wiped']} item(s); kept {len(res['kept'])}")
            for r in res["results"]:
                if not r["ok"]:
                    print(f"  ! {r['path']}: {r['result']}")
            print(f"  note: {res['flash_caveat']}")
            return 0
    except ValueError as e:
        print(str(e))
        return 2
    return 2


def cmd_airwave(args) -> int:
    """Airwave DVR — scrub back through RF history. An append-only timeline of
    what was on the air, so you can rewind and ask 'what was here at 3:42pm?'.
    Reads the device DVR (JSONL) or a --demo timeline."""
    from .radio.airwave_dvr import AirwaveDVR, AirwaveEvent
    dvr = AirwaveDVR(path=(None if getattr(args, "demo", False) else (args.dvr or "/captures/live/airwave.jsonl")))
    if getattr(args, "demo", False):
        base = 1000.0
        dvr.record_many([
            AirwaveEvent(base + 0, "beacon", "aa:00:00:00:00:01", "HomeNet", 6, -45),
            AirwaveEvent(base + 2, "beacon", "aa:00:00:00:00:01", "HomeNet", 6, -46),
            AirwaveEvent(base + 5, "pmkid", "aa:00:00:00:00:01", "HomeNet", 6, -47),
            AirwaveEvent(base + 61, "deauth", "bb:00:00:00:00:02", "", 11, -60),
            AirwaveEvent(base + 63, "eapol", "bb:00:00:00:00:02", "Guest", 11, -61),
            AirwaveEvent(base + 130, "beacon", "cc:00:00:00:00:03", "FarAP", 149, -72),
        ])
        print("redux airwave — SYNTHETIC RF timeline (no radio)")
    else:
        n = dvr.load()
        print(f"redux airwave — {n} event(s) from {dvr.path}")
    cmd = args.airwave_cmd
    if cmd == "summary":
        s = dvr.summary()
        print(f"  {s['note']}  |  {s['unique_aps']} AP(s); kinds: {s['kinds'] or 'none'}")
        return 0
    if cmd == "timeline":
        rows = dvr.timeline(bucket_seconds=args.bucket)
        if not rows:
            print("  (empty)")
            return 0
        lo = rows[0]["t"]
        for r in rows:
            off = int(r["t"] - lo)
            bar = "#" * min(40, r["count"])
            print(f"  t+{off // 60:02d}:{off % 60:02d}  {bar} {r['count']} ({r['top_kind']}, {r['aps']} AP)")
        return 0
    if cmd == "scrub":
        snap = dvr.scrub(args.at, radius=args.radius)
        print(f"  @ t={args.at} (+/-{args.radius}s): {snap['events']} event(s), "
              f"{len(snap['aps'])} AP(s), bands {snap['bands']}, kinds {snap['kinds']}")
        for b in snap["aps"]:
            print(f"    AP {b}")
        return 0
    if cmd == "track":
        hist = dvr.track(args.bssid)
        print(f"  {args.bssid}: seen {len(hist)} time(s)")
        for e in hist[:40]:
            print(f"    t={e.ts} {e.kind} ch{e.channel} {e.ssid or ''}")
        return 0
    return 2


def cmd_responder(args) -> int:
    """Responder-style hash harvesting on the joined LAN — the move the kill-chain
    JOIN phase sets up. Poison LLMNR/NBT-NS/mDNS so Windows hosts authenticate to
    us, harvest NetNTLMv1/v2, and drop the hashes into the crack queue. A firing
    path: FIELD needs the LAN armed + --authorized; LAB runs unleashed; --analyze
    (passive, no poisoning) is always allowed."""
    from .netrecon.responder import ResponderHarvest, to_crack_lines
    rh = ResponderHarvest(args.iface, seconds=getattr(args, "seconds", 60),
                          analyze_only=getattr(args, "analyze", False))
    if args.responder_cmd == "plan":
        print("responder plan — " + ("ANALYZE-only (passive, no poisoning)" if rh.analyze_only
                                     else "poisons LLMNR / NBT-NS / mDNS on the joined LAN"))
        print("  " + " ".join(rh.plan()))
        print("  harvest  : NetNTLMv1 → hashcat -m 5500   |   NetNTLMv2 → hashcat -m 5600")
        print("  correlate: JOIN (#1) puts us on the LAN → responder → hashes → crack queue")
        print("  posture  : FIELD needs the LAN armed + --authorized; LAB unleashed; -A always ok")
        return 0
    scope = None if getattr(args, "lab", False) else Scope.load(getattr(args, "scope_file", None) or _DEFAULT_SCOPE_PATH)
    hashes, reason = rh.harvest(scope=scope, authorized=getattr(args, "authorized", False),
                                lab=getattr(args, "lab", False))
    print(f"responder {args.iface} — {reason}")
    if not hashes:
        return 0
    grouped = to_crack_lines(hashes)
    out_dir = getattr(args, "out_dir", None) or "/captures/loot/netntlm"
    os.makedirs(out_dir, exist_ok=True)
    for mode, lines in sorted(grouped.items()):
        path = os.path.join(out_dir, f"netntlm-{mode}.txt")
        with open(path, "a") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"  {len(lines)} hash(es) -> {path}   (crack: hashcat -m {mode} {path} <wordlist>)")
    for h in hashes:
        print(f"    [{h.kind}] {h.username}@{h.domain or '.'}")
    return 0


def cmd_pmkid(args) -> int:
    """Passive PMKID sweep — ZERO-TRANSMIT loot. Harvest the PMKIDs APs volunteer
    on their own (no deauth, no association — nothing sent), then correlate them
    against the beacon inventory, the crackability pre-flight, the central Scope
    and the already-captured list, and rank the new loot. The quietest loot path
    there is: a WIDS has nothing to detect. `plan` explains the posture; `sweep`
    runs over the engine's real .22000 output (--from-22000) or synthetic (--demo)."""
    from .crack.passive_pmkid import PassivePmkidSweep, parse_22000_pmkid
    cmd = args.pmkid_cmd
    scope = None
    if getattr(args, "scoped", False):
        scope = Scope.load(getattr(args, "scope_file", None) or _DEFAULT_SCOPE_PATH)
    retired = set()
    try:
        from .crack.captured import retired_bssids
        retired = retired_bssids()
    except Exception:
        retired = set()

    if cmd == "plan":
        print("passive PMKID sweep — ZERO-TRANSMIT loot")
        print("  posture  : listen only; no deauth, no association request, nothing sent")
        print("  harvest  : PMKIDs an AP volunteers in EAPOL M1 whenever any client joins")
        print("  correlate: + beacon inventory (SSID/ch) + crackability + scope + captured-list + wordlist forge")
        print(f"  scope    : {'device scope' if scope is not None else 'LAB unscoped — every AP in earshot is fair game'}")
        print(f"  skip     : {len(retired)} already-captured / ignored AP(s)")
        print("  footprint: silent — nothing leaves the antenna, so a blue team has nothing to flag")
        print("  run      : redux pmkid sweep --from-22000 /captures/<file>.22000   (real engine output)")
        print("             redux pmkid sweep --demo                                 (synthetic, no radio)")
        return 0

    sweep = PassivePmkidSweep(scope=scope, retired=retired)
    if getattr(args, "demo", False):
        from .detect.frames import Frame, FrameType
        demo = [
            Frame(type=FrameType.PMKID, ts=1.0, bssid="aa:bb:cc:00:11:22",
                  ssid="Linksys00112", channel=6, security="wpa2-psk", rssi=-48),
            Frame(type=FrameType.PMKID, ts=2.0, bssid="de:ad:be:ef:00:01",
                  ssid="NETGEAR-Guest", channel=11, security="wpa2-psk", rssi=-67),
        ]
        sweep.feed_many(demo)
        print("redux pmkid sweep — SYNTHETIC PMKID sightings (no radio)")
    elif getattr(args, "from_22000", None):
        if not os.path.exists(args.from_22000):
            print(f"  no such file: {args.from_22000}")
            return 2
        with open(args.from_22000, "r", errors="replace") as fh:
            frames = parse_22000_pmkid(fh.read())
        sweep.feed_many(frames)
        print(f"redux pmkid sweep — {len(frames)} PMKID(s) parsed from {args.from_22000}")
    else:
        print("nothing to sweep: pass --from-22000 <file> (real engine output) or --demo (synthetic)")
        print("  (continuous live monitor-mode PMKID harvest runs under the capture daemon)")
        return 2

    summ = sweep.summary()
    sweep.save_state()        # surface the harvest to the UIs (TFT/web/phone) via live-state
    if getattr(args, "json", False):
        print(json.dumps({"summary": summ, "hits": [h.to_dict() for h in sweep.hits()]}, indent=2))
        return 0
    print(f"  {summ['note']}  [{summ['footprint']}]")
    print(f"  {summ['aps_leaking_pmkid']} leaking PMKID | {summ['crackable']} crackable | "
          f"{summ['in_scope']} in-scope | {summ['already_captured']} already owned | {summ['new_loot']} NEW")
    for h in sweep.hits():
        flag = ("NEW  " if h.new_loot else "OWNED" if h.already_captured
                else "----- " if h.in_scope else "SCOPE")
        ch = f"ch{h.channel}" if h.channel is not None else "ch?"
        rssi = f"{h.rssi}dBm" if h.rssi is not None else ""
        print(f"    [{flag}] {h.bssid}  {ch:5} {rssi:7} {(h.ssid or '<hidden>'):22} {h.reason}")
    return 0


def cmd_loot(args) -> int:
    """Recovered-credential vault + scope-gated network pivot. The vault is
    owner-only on-device storage, auth-log-linked (never the secret). `pivot`
    is a firing path: FIELD plans unless --authorized and every target must be
    armed; LAB runs end-to-end on your own private subnets."""
    from .loot import LootVault, LootEntry, pivot, default_loot_path
    vault = LootVault(args.vault or default_loot_path())
    cmd = args.loot_cmd
    if cmd == "add":
        newly = vault.add(LootEntry(kind="wifi-psk", target=(args.bssid or "").lower(),
                                    secret=args.psk, ssid=args.ssid or "",
                                    source=args.source or "manual"))
        who = args.bssid or args.ssid or "(unspecified)"
        print(f"{'added' if newly else 'updated'} wifi-psk for {who}  (vault holds {len(vault)})")
        return 0
    if cmd == "list":
        ents = vault.entries()
        if args.json:
            rows = []
            for e in ents:
                d = e.to_dict()
                if not args.reveal:
                    red = f"[redacted — {len(e.secret)} chars]"
                    d["secret"] = red
                    if "psk" in d:
                        d["psk"] = red
                rows.append(d)
            print(json.dumps(rows, indent=2))
            return 0
        if not ents:
            print(f"loot vault is empty ({vault.path})")
            return 0
        for e in ents:
            sec = e.secret if args.reveal else f"[redacted — {len(e.secret)} chars]"
            print(f"  {e.kind:12} {(e.target or e.ssid or '-'):22} {e.service or '-':6} "
                  f"{e.username or '-':10} {sec}  [{e.source}]")
        if not args.reveal:
            print("  (secrets redacted; pass --reveal to show)")
        return 0
    if cmd == "pivot":
        from .core.scope import Scope
        scope = Scope.load(args.scope_file or _DEFAULT_SCOPE_PATH)
        usernames = args.user or ["admin", "root"]
        passwords = list(args.pw or []) + vault.psk_list()
        res = pivot(scope, authorized=args.authorized, subnet=args.subnet,
                    usernames=usernames, passwords=passwords, vault=vault)
        if not res.executed:
            print(f"PIVOT [{res.mode.upper()}] — NOT fired: {res.reason}")
            for s in res.planned_steps:
                print(f"  plan: {s}")
            return 0
        print(f"PIVOT [{res.mode.upper()}] — {res.reason}; {res.vault_added} new credential(s) vaulted")
        for n in res.notes:
            print(f"  {n}")
        return 0
    if cmd == "triage":
        from .loot import triage as _triage
        results = _triage(args.files)
        if args.json:
            print(json.dumps(results, indent=2))
            return 0
        for path, r in results.items():
            flag = " ⚑ FLAGGED" if r.get("flagged") else ""
            print(f"\n{path}{flag}")
            for tool, info in r.get("tools", {}).items():
                if not info.get("available"):
                    print(f"  {tool:9} — {info.get('reason', 'unavailable')}")
                elif tool == "strings":
                    n = info.get("secret_count", 0)
                    print(f"  {tool:9} — {n} secret-looking string(s)" +
                          (f": {', '.join(info['secret_hits'][:3])}…" if n else ""))
                elif tool == "steghide":
                    print(f"  {tool:9} — {'possible hidden data' if info.get('maybe_embedded') else 'nothing obvious'}")
                else:
                    first = (info.get("metadata") or info.get("signatures") or "").splitlines()
                    print(f"  {tool:9} — {first[0] if first else 'no output'}")
        return 0
    if cmd == "files":
        # Scope-gated SMB file-loot: with a recovered credential, pull the
        # interesting files off a host straight into forensic triage. A firing
        # path — the host must be armed (or LAB), same gate as pivot.
        from .core.scope import Scope
        from .loot.files import SmbLooter
        scope = Scope.load(args.scope_file or _DEFAULT_SCOPE_PATH)
        ok, reason = scope.authorize(ip=args.host)
        if not ok:
            print(f"file-loot — NOT fired: {reason}")
            return 0
        print(f"file-loot {args.host} — {reason}")
        looter = SmbLooter(max_files=args.max_files)
        pulled, notes = looter.loot_host(args.host, args.user, args.pw, dest=args.dest)
        for n in notes:
            print(f"  {n}")
        if not pulled:
            print("  no files pulled")
            return 0
        print(f"  pulled {len(pulled)} file(s) into {args.dest}")
        for p in pulled:
            print(f"    {p}")
        if args.triage:
            from .loot import triage as _triage
            results = _triage(pulled)
            flagged = [p for p, r in results.items() if r.get("flagged")]
            print(f"  triaged {len(results)} file(s)" +
                  (f", {len(flagged)} FLAGGED" if flagged else ""))
            for p in flagged:
                print(f"    ⚑ {p}")
        return 0
    return 2


def cmd_narrate(args) -> int:
    """Explain-as-it-goes — replay the auth log with each offensive action paired
    to how blue detects and stops it (the purple learning view). Read-only; works
    in any mode."""
    from .intel.narrate import narrate
    from .core import authlog as A
    try:
        entries = A.AuthLog(args.authlog or A.default_authlog_path()).entries()
    except Exception:
        entries = []
    lines = narrate(entries)
    if args.json:
        print(json.dumps([{"ts": l.ts, "event": l.event, "detail": l.detail,
                           "technique": l.technique, "detection": l.detection,
                           "mitigation": l.mitigation} for l in lines], indent=2))
        return 0
    import time as _t
    print(f"NARRATE — {len(lines)} logged action(s), red action + blue answer")
    for l in lines:
        when = _t.strftime("%H:%M:%S", _t.gmtime(l.ts)) if l.ts else "--:--:--"
        print(f"\n  {when}  {l.event}  {l.detail}")
        if l.technique:
            print(f"      blue sees:  {l.detection}")
            print(f"      blue stops: {l.mitigation}")
    if not lines:
        print("  (auth log empty — run some scope/campaign actions first)")
    return 0


def cmd_graph(args) -> int:
    """Entity graph — the whole cage as one picture: physical-device nodes (reid
    merges a device's randomized MACs), 'travels-with' edges (co-occurrence
    fusion), annotated with where/when/vendor and whether its key was recovered.
    Read-only; the payoff of reid + fusion + sightings + loot + OUI."""
    from .intel.graph import build_graph
    from .geo.reid import reidentify
    from .geo.oui import lookup_oui
    reid_entities, fusion_groups, by_mac, cracked = [], [], {}, set()
    try:
        from .portal.karma import ProbeHarvester
        reid_entities = reidentify(ProbeHarvester(path=args.pnl).clients())
    except Exception:
        reid_entities = []
    try:
        from .geo.follower import ObservationLog
        from .intel.fusion import fuse
        fusion_groups = fuse(ObservationLog(args.obs).load())
    except Exception:
        fusion_groups = []
    try:
        from .geo.db import SightingStore
        for s in SightingStore(args.db).query():
            by_mac.setdefault((s.mac or "").lower(), []).append(s)
    except Exception:
        by_mac = {}
    try:
        from .loot.vault import LootVault
        for e in LootVault(args.loot).entries():
            if e.target:
                cracked.add(e.target.lower())
    except Exception:
        cracked = set()
    g = build_graph(reid_entities=reid_entities, fusion_groups=fusion_groups,
                    sightings_by_mac=by_mac, cracked_macs=cracked, vendor_lookup=lookup_oui)
    if args.json:
        print(json.dumps({
            "summary": g.summary,
            "nodes": [{"id": n.id, "macs": list(n.macs), "randomized": n.randomized,
                       "vendor": n.vendor, "sightings": n.sightings, "last_seen": n.last_seen,
                       "strongest_rssi": n.strongest_rssi, "places": list(n.places),
                       "cracked": n.cracked, "linked_by": list(n.linked_by)} for n in g.nodes],
            "edges": [{"a": e.a, "b": e.b, "relation": e.relation,
                       "confidence": e.confidence, "evidence": e.evidence} for e in g.edges]},
            indent=2))
        return 0
    print(f"ENTITY GRAPH — {g.summary}")
    for n in g.nodes:
        tag = "*" if n.cracked else " "
        print(f"\n {tag}{n.id}")
        print(f"    {n.summary}")
        for m in n.macs:
            print(f"      {m}")
    if g.edges:
        print("\n  travels-with:")
        for e in g.edges:
            print(f"    {e.a}  <->  {e.b}   [{e.confidence}, {e.evidence} shared window(s)]")
    if not g.nodes:
        print("  (nothing observed yet — run the daemon to populate sightings/probes)")
    return 0


def cmd_firehose(args) -> int:
    """Glass-box firehose — one time-ordered stream of every decision (auth log)
    and every device seen (sightings). A LAB-only diagnostic: too noisy/revealing
    for FIELD, so it's refused there. Read-only."""
    from .core.mode import read_mode
    mode = read_mode(getattr(args, "scope_file", None))
    if mode != "lab":
        print("firehose is a LAB-only diagnostic — not available in FIELD (switch to LAB on your own gear)")
        return 2
    from .intel.firehose import firehose
    from .core import authlog as A
    entries, sights = [], []
    try:
        entries = A.AuthLog(args.authlog or A.default_authlog_path()).entries()
    except Exception:
        entries = []
    try:
        from .geo.db import SightingStore, DEFAULT_DB_PATH
        sights = SightingStore(args.db or DEFAULT_DB_PATH).query(limit=args.tail)
    except Exception:
        sights = []
    lines = firehose(entries, sights, limit=args.tail)
    if args.json:
        print(json.dumps([{"ts": l.ts, "source": l.source, "text": l.text} for l in lines], indent=2))
        return 0
    import time as _t
    print(f"GLASS-BOX FIREHOSE [LAB] — {len(lines)} event(s), oldest first")
    for l in lines:
        when = _t.strftime("%H:%M:%S", _t.gmtime(l.ts)) if l.ts else "--:--:--"
        print(f"  {when}  {l.source:8} {l.text}")
    if not lines:
        print("  (nothing recorded yet — run the daemon / some scope actions first)")
    return 0


def cmd_locate(args) -> int:
    """Direction-find a signal to its origin from positioned RSSI (fox-hunt). Shows
    warmer/colder, a best-guess source location, and a compass heading. Re-run as
    you move to track in real time. Read-only."""
    from .geo.follower import ObservationLog
    from .intel.locate import locate
    from .geo.oui import lookup_oui
    try:
        obs = ObservationLog(args.obs).load()
    except Exception:
        obs = []
    target = (args.target or "").strip()
    tl = target.lower()
    rows = [o for o in obs if (not tl or (o.mac or "").lower() == tl)]
    res = locate(rows, target=target or "source", window=args.window)
    vendor = lookup_oui(target) if target else None
    if args.json:
        print(json.dumps({"target": res.target, "vendor": vendor, "readings": res.readings,
                          "trend": res.trend, "strongest_rssi": res.strongest_rssi,
                          "strongest_at": res.strongest_at, "est_lat": res.est_lat,
                          "est_lon": res.est_lon, "bearing": res.bearing,
                          "confidence": res.confidence, "guidance": res.guidance}, indent=2))
        return 0
    print(f"LOCATING {res.target}" + (f"  ({vendor})" if vendor else "")
          + f"  — {res.readings} positioned reading(s)")
    if res.readings == 0:
        print("  no readings with RSSI + GPS yet — move around near the source while the box"
              " logs BLE/Wi-Fi observations (needs a GPS fix)")
        return 0
    print(f"  trend: {res.trend}   strongest: {res.strongest_rssi} dBm")
    print(f"  {res.guidance}")
    print(f"  confidence: {res.confidence}")
    print("  (re-run as you move — the estimate sharpens with spread; a 2nd node would let it multilaterate)")
    return 0


def cmd_fuse(args) -> int:
    """Co-occurrence fusion — cluster devices that repeatedly appear in the same
    time+place window (carried together). Reads the observation time-series log.
    Radio-agnostic: BLE today, true cross-radio once WiFi is also logged. Read-only;
    co-presence is correlation, not proof — every group carries its evidence."""
    from .geo.follower import ObservationLog
    from .intel.fusion import fuse
    try:
        obs = ObservationLog(args.log).load()
    except Exception:
        obs = []
    if not obs:
        print(f"no observation history yet ({args.log}) — the daemon logs located "
              "observations there as it runs (BLE today)")
        return 0
    groups = fuse(obs, window_s=args.window_seconds, precision=args.precision,
                  min_co=args.min_co, min_score=args.min_score)
    if args.json:
        print(json.dumps([{"macs": list(g.macs), "co_windows": g.co_windows,
                           "score": g.score, "confidence": g.confidence,
                           "windows_seen": g.windows_seen} for g in groups], indent=2))
        return 0
    seen = len({(o.mac or "").lower() for o in obs})
    print(f"CO-OCCURRENCE FUSION — {len(groups)} device group(s) that travel together "
          f"(from {len(obs)} observations of {seen} device(s))")
    if not groups:
        print("  (no devices repeatedly co-located — nothing travels together in this history)")
    for g in groups:
        print(f"\n  [{g.confidence:6}] {g.summary}")
        for m in g.macs:
            print(f"      {m}")
    print("\nnote: co-presence is correlation, not proof; two devices on the same bus co-occur too.")
    return 0


def cmd_radar(args) -> int:
    """Threat radar — counter-surveillance from the sightings the box already
    records: evil twins, KARMA lures, and suspicious new strong APs. Passive and
    defensive; identical in LAB and FIELD. Honest about its frame-level blind spot."""
    from .intel.radar import scan_sightings
    trusted = list(args.trusted or [])
    # armed BSSIDs in the scope count as your own gear (trusted)
    if args.scope_file is not None or not trusted:
        try:
            from .core.scope import Scope
            scope = Scope.load(args.scope_file or _DEFAULT_SCOPE_PATH)
            trusted += [e.value for e in scope.active_entries() if e.kind == "bssid"]
        except Exception:
            pass
    pnl_ssids = []
    if args.pnl:
        try:
            from .portal.karma import ProbeHarvester
            pnl_ssids = [r["ssid"] for r in ProbeHarvester(path=args.pnl).ssids() if r.get("ssid")]
        except Exception:
            pnl_ssids = []
    rows = []
    try:
        from .geo.db import SightingStore
        rows = SightingStore(args.db or "/captures/live/sightings.db").query(kind="wifi")
    except Exception:
        rows = []
    rep = scan_sightings(rows, pnl_ssids=pnl_ssids, trusted_bssids=trusted,
                         new_window_s=args.new_window_hours * 3600.0, strong_rssi=args.strong_rssi)
    if args.json:
        print(json.dumps({
            "scanned_aps": rep.scanned_aps,
            "alerts": [{"kind": a.kind, "severity": a.severity, "ssid": a.ssid,
                        "bssids": list(a.bssids), "detail": a.detail, "advice": a.advice}
                       for a in rep.alerts],
            "notes": list(rep.notes)}, indent=2))
        return 0
    print(f"THREAT RADAR — {len(rep.alerts)} alert(s) across {rep.scanned_aps} AP(s) seen")
    for a in rep.alerts:
        print(f"\n  [{a.severity.upper():6}] {a.kind}  {a.ssid!r}")
        print(f"    {a.detail}")
        print(f"    BSSIDs: {', '.join(a.bssids)}")
        print(f"    -> {a.advice}")
    if not rep.alerts:
        print("  (nothing suspicious in the stored sightings)")
    for n in rep.notes:
        print(f"\nnote: {n}")
    return 0


def cmd_explain(args) -> int:
    """Purple-team explain & defend — for a technique Augur can run, how a
    defender detects it and how they stop it. Informational; mode-agnostic."""
    from .intel.defend import explain, list_techniques
    if not args.technique:
        print("techniques (redux explain <key>):")
        for key, name in list_techniques():
            print(f"  {key:14} {name}")
        return 0
    t = explain(args.technique)
    if t is None:
        print(f"unknown technique {args.technique!r}. Try: "
              + ", ".join(k for k, _ in list_techniques()))
        return 2
    if args.json:
        print(json.dumps({"key": t.key, "name": t.name, "what": t.what,
                          "detection": list(t.detection), "mitigation": list(t.mitigation),
                          "standards": list(t.standards)}, indent=2))
        return 0
    print(f"{t.name}  ({t.key})")
    print(f"\n  WHAT\n    {t.what}")
    print("\n  HOW IT'S DETECTED")
    for d in t.detection:
        print(f"    - {d}")
    print("\n  HOW TO DEFEND")
    for m in t.mitigation:
        print(f"    - {m}")
    if t.standards:
        print(f"\n  CLOSES IT: {', '.join(t.standards)}")
    return 0


def cmd_forge(args) -> int:
    """Target-aware wordlist/mask forge — turn recon into a targeted candidate
    list. Read-only; identical in LAB and FIELD. Run these before a generic list."""
    from .crack.forge import forge
    pnl_seeds = []
    if args.pnl:
        try:
            from .portal.karma import ProbeHarvester
            pnl_seeds = [r["ssid"] for r in ProbeHarvester(path=args.pnl).ssids() if r.get("ssid")]
        except Exception:
            pnl_seeds = []
    # Environment-grown mode (#91): mine seeds from everything the box observed —
    # the PNLs above, the SSIDs the passive-PMKID sweep logged, and the strings in
    # any looted files — then grow the wordlist from THIS place. A+B+C → one list.
    if getattr(args, "environment", False):
        from .crack.environment import EnvironmentHarvester
        harv = EnvironmentHarvester()
        harv.add_pnl(pnl_seeds)
        for tok in (args.token or []):
            harv.add_ssid(tok)
        grown = 0
        # SSIDs the zero-transmit PMKID sweep already harvested (live-state file)
        try:
            from .crack.passive_pmkid import load_state as _pmkid_state
            for h in _pmkid_state().get("hits", []):
                if h.get("ssid"):
                    harv.add_ssid(h["ssid"]); grown += 1
                if h.get("bssid"):
                    harv.add_vendor(h["bssid"])
        except Exception:
            pass
        # strings/metadata/filenames from looted files (triage #2 over the loot dir)
        if getattr(args, "loot_dir", None):
            try:
                import os as _os
                from .loot.triage import triage as _triage
                files = []
                for root, _d, names in _os.walk(args.loot_dir):
                    files.extend(_os.path.join(root, n) for n in names)
                if files:
                    harv.learn_triage(_triage(files)); grown += len(files)
            except Exception:
                pass
        f = harv.grow(args.ssid or "", bssid=args.bssid,
                      min_len=args.min_len, max_words=args.max)
        summ = harv.summary()
        print(f"ENVIRONMENT-GROWN — {summ['note']}  (sources: {summ['by_source'] or 'none'})")
    else:
        f = forge(args.ssid or "", tokens=args.token or [], pnl=pnl_seeds,
                  bssid=args.bssid, min_len=args.min_len, max_words=args.max)
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write("\n".join(f.words) + ("\n" if f.words else ""))
        except OSError as e:
            print(f"could not write {args.out}: {e}")
            return 1
        print(f"wrote {len(f.words)} candidate(s) to {args.out}  (seeds: {', '.join(f.seeds) or 'none'})")
        for m in f.masks:
            print(f"  mask {m.mask}  — {m.note}")
        return 0
    if args.json:
        print(json.dumps({"ssid": f.ssid, "seeds": list(f.seeds), "vendor": f.vendor,
                          "words": list(f.words),
                          "masks": [{"mask": m.mask, "note": m.note} for m in f.masks],
                          "notes": list(f.notes)}, indent=2))
        return 0
    print(f"FORGE — {len(f.words)} targeted candidate(s) from seeds: {', '.join(f.seeds) or 'none'}"
          + (f"  [vendor: {f.vendor}]" if f.vendor else ""))
    for w in f.words[:40]:
        print(f"  {w}")
    if len(f.words) > 40:
        print(f"  … (+{len(f.words) - 40} more; use --out FILE to save all)")
    print("masks (hashcat -a 3):")
    for m in f.masks:
        print(f"  {m.mask}  — {m.note}")
    for n in f.notes:
        print(f"note: {n}")
    return 0


def cmd_crackprep(args) -> int:
    """Crack-prep: chain preflight -> triage -> forge over the capture queue into a
    single ranked work order (crack these first; here's the wordlist for each).
    Read-only; identical in LAB and FIELD."""
    from pathlib import Path
    from .crack.preflight import assess_capture
    from .crack.triage import parse_22000_targets, triage_crack_queue
    from .crack.forge import forge
    from .crack.prep import crack_prep
    files = [Path(args.file)] if args.file else (
        sorted(Path(args.dir).rglob("*.hc22000")) if Path(args.dir).is_dir() else [])
    tuples, strong = [], set()
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tuples.extend(parse_22000_targets(text))
        for fr in assess_capture(str(fp)).frames:
            if fr.strong:
                strong.add(fr.bssid)
    triaged = triage_crack_queue(tuples)
    items = crack_prep(triaged, strong, forge_fn=forge,
                       limit=(args.top or None), tokens=args.token or [])
    wrote = {}
    if args.out_dir and items:
        try:
            Path(args.out_dir).mkdir(parents=True, exist_ok=True)
            for it in items:
                if it.forged_words:
                    p = Path(args.out_dir) / f"{it.bssid.replace(':', '')}.txt"
                    p.write_text("\n".join(it.forged_words) + "\n", encoding="utf-8")
                    wrote[it.bssid] = str(p)
        except OSError as e:
            print(f"could not write wordlists: {e}")
            return 1
    if args.json:
        print(json.dumps([{"ssid": it.ssid, "bssid": it.bssid, "artifacts": list(it.artifacts),
                           "tier": it.tier, "prob": it.prob, "strong": it.strong,
                           "wordlist_hint": it.wordlist_hint, "forged": len(it.forged_words),
                           "wordlist_file": wrote.get(it.bssid)} for it in items], indent=2))
        return 0
    src = args.file or args.dir
    print(f"CRACK-PREP — {len(items)} target(s) to work, best first, from {src}")
    if not items:
        print("  (no crackable captures found — capture a handshake/PMKID first)")
    for it in items:
        print(f"  {it.summary}")
        print(f"      wordlist: {it.wordlist_hint}" + (f"  -> {wrote[it.bssid]}" if it.bssid in wrote else ""))
    return 0


def cmd_preflight(args) -> int:
    """Is this capture crackable, and why? Decodes PMKID/EAPOL completeness
    before you spend GPU time. Read-only; identical in LAB and FIELD."""
    from .crack.preflight import assess_capture
    pf = assess_capture(args.file)
    if args.json:
        print(json.dumps({
            "source": pf.source, "available": pf.available, "crackable": pf.crackable,
            "pmkid": pf.pmkid, "handshakes": pf.handshakes, "unique_aps": pf.unique_aps,
            "invalid_lines": pf.invalid_lines, "reason": pf.reason,
            "frames": [{"bssid": f.bssid, "ssid": f.ssid, "kind": f.kind,
                        "strong": f.strong, "detail": f.detail} for f in pf.frames],
        }, indent=2))
        return 0 if pf.available else 2
    print(("OK  " if pf.available else "N/A ") + pf.source)
    print(f"  {pf.reason}")
    for fr in pf.frames:
        print(f"    {'*' if fr.strong else '-'} {fr.kind:9} {fr.ssid or '(hidden)'}  {fr.bssid}")
        print(f"        {fr.detail}")
    return 0 if pf.available else 2


def cmd_triage(args) -> int:
    """Crack ROI triage + passive-capture forecast — a learning aid for where to
    spend effort. Every number is a rough heuristic for ORDERING work, not a
    guarantee. Reads real captured artifacts and real harvested probe counts."""
    from pathlib import Path
    from .crack import triage as T
    # --- crack ROI over the captured .hc22000 queue ---------------------- #
    files = [Path(args.file)] if args.file else (
        sorted(Path(args.dir).rglob("*.hc22000")) if Path(args.dir).is_dir() else [])
    tuples = []
    for fp in files:
        try:
            tuples.extend(T.parse_22000_targets(fp.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    rows = T.triage_crack_queue(tuples)
    # --- passive-capture forecast from harvested PNL --------------------- #
    forecasts = []
    try:
        from .portal.karma import ProbeHarvester
        forecasts = T.triage_capture(ProbeHarvester(path=args.pnl).ssids(),
                                     reassoc_per_hour=args.reassoc_per_hour)
    except Exception:
        forecasts = []
    if args.json:
        print(json.dumps({
            "crack_queue": [{"ssid": r.ssid, "bssid": r.bssid, "artifacts": list(r.artifacts),
                             "prob": r.prob, "priority": r.priority, "tier": r.tier,
                             "wordlist": r.wordlist_hint, "why": r.rationale} for r in rows],
            "capture_forecast": [{"ssid": c.ssid, "clients": c.clients,
                                  "mean_hours": c.mean_hours, "eta": c.eta,
                                  "recommend": c.recommend} for c in forecasts],
        }, indent=2))
        return 0
    src = args.file or args.dir
    print(f"CRACK ROI — {len(rows)} captured target(s) in {src}  (heuristic order, not a success rate)")
    if not rows:
        print("  (no .hc22000 artifacts found — capture a handshake/PMKID first)")
    for r in rows:
        print(f"  [{r.tier:6}] p≈{r.prob:.2f}  {r.ssid or '(hidden)'}  {r.bssid}  {{{','.join(r.artifacts)}}}")
        print(f"            wordlist: {r.wordlist_hint}")
        print(f"            {r.rationale}")
    print(f"\nCAPTURE FORECAST — {len(forecasts)} probed SSID(s) from {args.pnl}  "
          f"(~{args.reassoc_per_hour}/h per-client reassoc assumption; rough estimate)")
    if not forecasts:
        print("  (no harvested probes yet)")
    for c in forecasts:
        print(f"  {c.ssid!r}  clients={c.clients}  passive ETA: {c.eta}")
        print(f"            -> {c.recommend}")
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
        if getattr(args, "clear", False):
            print(store.clear_selected())
            return 0
        if args.name:
            wl, reason = store.set_selected(args.name)   # persist the pick so it sticks
        else:
            wl, reason = store.select(tier=args.tier)    # show the effective pick (honours the persisted one)
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


_ATTACK_NAMES = {
    "straight": 0, "dict": 0, "wordlist": 0,
    "combinator": 1, "combo": 1,
    "mask": 3, "brute": 3, "bruteforce": 3,
    "hybrid-wm": 6, "hybrid": 6,
    "hybrid-mw": 7,
}


def _resolve_attack(value):
    """Accept a friendly attack name or a bare int; None -> use recipe default."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in _ATTACK_NAMES:
        return _ATTACK_NAMES[v]
    if v.isdigit():
        return int(v)
    return None


def cmd_crack(args) -> int:
    """Guided, any-hash cracking front-end (not just Wi-Fi): browse hash types,
    identify a sample, plan the exact hashcat command, and run it (gated by
    default, unlocked in LAB). Glass-box throughout."""
    from pathlib import Path
    from .crack import recipes as R
    cmd = args.crack_cmd

    if cmd == "modes":
        rows = R.find(getattr(args, "query", "") or "")
        if getattr(args, "category", None):
            rows = [r for r in rows if r.category == args.category]
        if not rows:
            print("no hash types match")
            return 0
        print(f"{'slug':<16} {'-m':>6}  {'category':<9} label")
        for r in rows:
            flag = " (slow)" if r.slow else ""
            print(f"{r.name:<16} {r.mode:>6}  {r.category:<9} {r.label}{flag}")
        print(f"\n{len(rows)} type(s). Pick one as --type <slug> for `crack plan`/`crack run`.")
        return 0

    if cmd == "id":
        try:
            blob = Path(args.hashfile).read_text(errors="replace")[:65536]
        except OSError as e:
            print(f"cannot read {args.hashfile}: {e}")
            return 3
        sample = next((ln.strip() for ln in blob.splitlines()
                       if ln.strip() and not ln.lstrip().startswith("#")), "")
        if not sample:
            print("no hash line found in file")
            return 3
        cands = R.identify(sample)
        if not cands:
            print("could not recognize the hash shape — browse `redux crack modes` and pick by hand")
            return 0
        print(f"sample: {sample[:72]}{'…' if len(sample) > 72 else ''}\n")
        print("likely hash types (best first):")
        for r in cands:
            s = R.suggest(r)
            print(f"  --type {r.name:<14} -m {r.mode:<6} {r.label}")
            print(f"       suggest: attack {s.attack}"
                  + (f" + '{s.rules}' rules" if s.use_rules else " (no rules)")
                  + f" — {s.reason}")
        return 0

    if cmd == "wifi":
        # Alternative crack engine: aircrack-ng run DIRECTLY against a .cap/.pcap
        # WPA/WPA2 handshake (the hashcat path above converts to -m 22000 first;
        # this skips the conversion). Same scope posture as `crack run`: refused
        # unless LAB mode or --authorized, and crack()/scope.authorize(bssid)
        # refuses a BSSID that isn't in scope. Glass-box, honest tool-absence.
        cap = args.capture
        if not Path(cap).is_file():
            print(f"capture not found: {cap}")
            return 3
        wl_path = getattr(args, "wordlist_path", None)
        if wl_path is None:
            from .crack.wordlists import resolve_wordlist
            wl_path, wl_reason = resolve_wordlist(args.wordlist_dir, name=getattr(args, "wordlist", None))
            if wl_path is None:
                print(f"no wordlist: {wl_reason}")
                return 3
        argv = ["aircrack-ng", "-w", wl_path, "-b", args.bssid, cap]
        if not args.run:
            print("aircrack-ng plan (glass-box):\n  " + " ".join(argv))
            print("\nengine: aircrack-ng — the alternative to the hashcat -m 22000 path"
                  " (skips the pcap->22000 conversion).")
            print(f"bssid {args.bssid} is scope-gated; --run needs LAB mode or --authorized.")
            return 0
        from .core.mode import read_mode
        if not (read_mode(getattr(args, "scope_file", None)) == "lab" or args.authorized):
            print("refused: cracking is gated — enable LAB mode or pass --authorized")
            return 3
        from .core.scope import Scope, default_scope_path
        try:
            scope = Scope.load(args.scope_file or default_scope_path())
        except Exception:
            scope = Scope()
        from .crack import crack, CrackRefused, AircrackCracker
        try:
            res = crack(cap, wl_path, bssid=args.bssid, scope=scope, cracker=AircrackCracker())
        except CrackRefused as refusal:
            print(f"refused: {refusal}")
            return 3
        print(json.dumps({"cracked": res.cracked, "target": res.target, "reason": res.reason,
                          "psk": res.psk, "tool": res.tool, "available": res.available}, indent=2))
        return 0 if (res.cracked or res.available) else 3

    # plan / run share option resolution
    recipe = R.by_name(getattr(args, "type", "") or "")
    if recipe is None:
        print(f"unknown hash type '{getattr(args, 'type', '')}' — see `redux crack modes`")
        return 3
    atk = _resolve_attack(getattr(args, "attack", None))
    sug = R.suggest(recipe)
    # smart default rules: if the user didn't say, use the suggestion (fast hashes only)
    rules = getattr(args, "rules", None)
    if rules is None and (atk in (None, 0)) and sug.use_rules:
        rules = sug.rules
    # resolve a wordlist from the store (by name, or the persisted pick) unless a direct path was given
    wl_path = getattr(args, "wordlist_path", None)
    if wl_path is None and (atk in (None, 0, 1, 6, 7)):
        from .crack.wordlists import resolve_wordlist
        wl_path, wl_reason = resolve_wordlist(args.wordlist_dir, name=getattr(args, "wordlist", None))
        if wl_path is None and cmd == "plan":
            print(f"note: {wl_reason}")
    outfile = getattr(args, "outfile", None)
    cplan = R.plan(recipe, args.hashfile, attack=atk, wordlist=wl_path,
                   rules=rules, mask=getattr(args, "mask", None),
                   binary=args.binary, outfile=outfile)

    if cmd == "plan":
        print(cplan.explanation)
        if cplan.ok:
            print("\ncommand:\n  " + " ".join(cplan.argv))
        return 0 if cplan.ok else 3

    if cmd == "run":
        from .crack.anycrack import CrackSettings, run_plan
        from .core.mode import read_mode
        lab = read_mode(getattr(args, "scope_file", None)) == "lab"
        settings = CrackSettings(binary=args.binary,
                                 results_dir=Path(args.results_dir),
                                 max_runtime_seconds=args.runtime)
        res = run_plan(cplan, settings, authorized=bool(getattr(args, "authorized", False)),
                       lab_mode=lab, session="redux-crack-" + recipe.name)
        print(json.dumps(res, indent=2))
        return 0 if res.get("status") in ("recovered", "exhausted", "completed") else 3
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


def cmd_captured(args) -> int:
    """The already-pwned / ignore list: APs lab-mode offense skips (verified
    captures, plus any the operator chooses to ignore)."""
    from .crack.captured import (IgnoreList, captured_from_ingest,
                                  DEFAULT_JOBS_DB, DEFAULT_IGNORE_PATH)
    db = getattr(args, "db", None) or DEFAULT_JOBS_DB
    path = getattr(args, "file", None) or DEFAULT_IGNORE_PATH
    cmd = args.captured_cmd
    if cmd == "list":
        auto = captured_from_ingest(db)
        manual = IgnoreList(path).items()
        if not auto and not manual:
            print("no retired APs yet (none captured, none manually ignored)")
            return 0
        for bssid in sorted(set(auto) | set(manual)):
            tags = ([". captured"] if bssid in auto else []) + \
                   (["ignored"] if bssid in manual else [])
            ssid = manual.get(bssid, {}).get("ssid", "")
            print(f"  {bssid}  [{', '.join(t.lstrip('. ') for t in tags)}]"
                  + (f"  {ssid}" if ssid else ""))
        return 0
    if cmd == "add":
        ok, reason = IgnoreList(path).add(args.bssid, ssid=getattr(args, "ssid", "") or "")
        print(("ignoring " if ok else "") + reason)
        return 0 if ok else 3
    if cmd == "remove":
        ok, reason = IgnoreList(path).remove(args.bssid)
        print(("re-targeting " if ok else "") + reason)
        return 0 if ok else 3
    return 2


def cmd_mode(args) -> int:
    """Operating mode switch: LAB (unlocked, automatic) <-> FIELD (passive, gated).
    What the TFT mode switch calls under the hood."""
    from .core.mode import read_mode, set_mode, toggle_mode
    sub = getattr(args, "mode_cmd", None) or "status"
    if sub == "status":
        print(f"mode: {read_mode().upper()}")
        return 0
    res = toggle_mode() if sub == "toggle" else set_mode(sub)
    print(res["reason"])
    return 0 if res["ok"] else 3


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

    st = sub.add_parser("status", help="print a glass-box status snapshot "
                        "(reads the live daemon when one is running)")
    add_radio_flags(st)
    st.add_argument("--port", type=int, default=8080,
                    help="live daemon dashboard port to read real status from (default 8080)")
    st.add_argument("--no-daemon", action="store_true",
                    help="force the local no-hardware snapshot instead of the live daemon")
    st.set_defaults(func=cmd_status)

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
    wls = wlc.add_parser("select", help="pick the wordlist the crack uses (--name sticks); glass-box")
    wls.add_argument("--name", default=None, help="persist this list (by name/id) as the crack's pick")
    wls.add_argument("--tier", default=None, choices=("fast", "standard", "thorough"),
                     help="preview the most thorough list in this tier (not persisted)")
    wls.add_argument("--clear", action="store_true", help="clear the persisted pick (back to fastest)")
    wlc.add_parser("verify", help="re-hash registered lists and report drift/missing")
    wlr = wlc.add_parser("remove", help="unregister a list (leaves the file on disk)")
    wlr.add_argument("key", help="wordlist id or name to remove")
    wl.set_defaults(func=cmd_wordlist)

    ck = sub.add_parser("crack", help="guided any-hash cracking (not just Wi-Fi): modes/id/plan/run; "
                                      "plus `wifi` for the aircrack-ng engine on WPA handshakes")
    cksub = ck.add_subparsers(dest="crack_cmd", required=True)
    ckm = cksub.add_parser("modes", help="browse/search the hash-type catalog")
    ckm.add_argument("query", nargs="?", default="", help="filter by slug/label/category/mode")
    ckm.add_argument("--category", default=None,
                     help="limit to a category (wifi/windows/network/linux/web/generic/archive/document/disk/crypto)")
    cki = cksub.add_parser("id", help="identify a hash sample and suggest the mode + options")
    cki.add_argument("hashfile", help="file containing one hash per line")

    def _crack_opts(p, *, running):
        p.add_argument("hashfile", help="absolute path to the hash file to crack")
        p.add_argument("--type", required=True, help="hash type slug (see `crack modes`) or a bare -m number")
        p.add_argument("--attack", default=None,
                       help="straight|combinator|mask|hybrid-wm|hybrid-mw (default: the type's own)")
        p.add_argument("--wordlist", default=None, help="registered wordlist name/id (else the persisted pick)")
        p.add_argument("--wordlist-path", default=None, help="use this absolute wordlist path directly")
        p.add_argument("--wordlist-dir", default="/captures/wordlists", help="wordlist store dir")
        p.add_argument("--rules", default=None, help="rule preset name or .rule path (fast hashes only)")
        p.add_argument("--mask", default=None, help="hashcat mask or a preset (for mask/hybrid attacks)")
        p.add_argument("--binary", default="hashcat", help="hashcat binary (default: hashcat)")
        if running:
            p.add_argument("--authorized", action="store_true",
                           help="confirm this hash file is yours to crack (not needed in LAB mode)")
            p.add_argument("--scope-file", default=None,
                           help="read LAB/FIELD mode from this scope file (default: the device scope)")
            p.add_argument("--results-dir", default="/captures/crack", help="where recovered results are written (0600)")
            p.add_argument("--runtime", type=int, default=1800, help="max hashcat runtime seconds (default 1800)")
        else:
            p.add_argument("--outfile", default=None, help="show an --outfile in the planned command")
    _crack_opts(cksub.add_parser("plan", help="show the exact hashcat command that would run (glass-box)"), running=False)
    _crack_opts(cksub.add_parser("run", help="run the crack (gated by default; unlocked in LAB mode)"), running=True)
    cwf = cksub.add_parser("wifi", help="crack a WPA/WPA2 handshake with aircrack-ng "
                                        "(alternative engine to the hashcat -m 22000 path)")
    cwf.add_argument("capture", help="absolute path to the handshake .cap/.pcap")
    cwf.add_argument("--bssid", required=True, help="target AP BSSID (scope-gated on --run)")
    cwf.add_argument("--wordlist", default=None, help="registered wordlist name/id (else the persisted pick)")
    cwf.add_argument("--wordlist-path", default=None, help="use this absolute wordlist path directly")
    cwf.add_argument("--wordlist-dir", default="/captures/wordlists", help="wordlist store dir")
    cwf.add_argument("--run", action="store_true",
                     help="actually run it (gated: LAB mode or --authorized, plus the BSSID in scope)")
    cwf.add_argument("--authorized", action="store_true",
                     help="confirm this capture is yours to crack (not needed in LAB mode)")
    cwf.add_argument("--scope-file", default=None,
                     help="read LAB/FIELD mode + scope from this file (default: the device scope)")
    ck.set_defaults(func=cmd_crack)

    cp = sub.add_parser("captured", help="the already-pwned / ignore list (APs offense skips)")
    cp.add_argument("--db", default=None, help="pipeline DB (default /captures/jobs.db)")
    cp.add_argument("--file", default=None,
                    help="manual ignore list (default /captures/config/captured.json)")
    cpsub = cp.add_subparsers(dest="captured_cmd", required=True)
    cpsub.add_parser("list", help="show retired APs (verified captures + manual ignores)")
    cpa = cpsub.add_parser("add", help="manually ignore an AP so offense skips it")
    cpa.add_argument("bssid", help="AP BSSID to ignore (aa:bb:cc:dd:ee:ff)")
    cpa.add_argument("--ssid", default="", help="optional label")
    cpr = cpsub.add_parser("remove", help="re-target an AP (drop it from the manual ignore list)")
    cpr.add_argument("bssid", help="AP BSSID to stop ignoring")
    cp.set_defaults(func=cmd_captured)

    md = sub.add_parser("mode", help="operating mode: LAB (automatic) <-> FIELD (passive, gated)")
    mdsub = md.add_subparsers(dest="mode_cmd")
    mdsub.add_parser("status", help="show the current mode")
    mdsub.add_parser("lab", help="LAB: unlocked, automatic chain (deauth-capture + crack) on your own gear")
    mdsub.add_parser("field", help="FIELD: passive, authorize-required, no auto-crack (the lock-down)")
    mdsub.add_parser("toggle", help="flip between LAB and FIELD")
    md.set_defaults(func=cmd_mode)

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

    dr = sub.add_parser("doctor", help="headless glass-box self-diagnosis "
                        "(reads the live daemon when one is running)")
    add_radio_flags(dr)
    dr.add_argument("--port", type=int, default=8080,
                    help="live daemon dashboard port to read real Doctor from (default 8080)")
    dr.add_argument("--no-daemon", action="store_true",
                    help="force the local no-hardware self-diagnosis instead of the live daemon")
    dr.set_defaults(func=cmd_doctor)

    ca = sub.add_parser("campaign", help="autonomous glass-box kill-chain operator")
    ca.add_argument("--persona", help="posture (red/purple = offense; blue/recon = detection-only)")
    ca.add_argument("--scope-file", default=_DEFAULT_SCOPE_PATH)
    casub = ca.add_subparsers(dest="campaign_cmd", required=True)
    cap_ = casub.add_parser("plan", help="dry-run the gated chain (nothing executes)")
    cap_.add_argument("--targets", nargs="*", help="targets to run the chain against")
    cap_.add_argument("--no-engine", action="store_true", help="plan as if no capture engine present")
    cad_ = casub.add_parser("demo", help="run the chain with fake executors + render the report")
    cad_.add_argument("--targets", nargs="*", help="targets to run the chain against")
    cau_ = casub.add_parser("unleashed", help="LAB-only: turn the full chain loose on every in-scope target (POWER)")
    cau_.add_argument("--cycles", type=int, default=1, help="how many sweep cycles to run")
    cau_.add_argument("--targets", nargs="*", help="override the target set (default: armed scope BSSIDs)")
    cau_.add_argument("--adaptive", action="store_true", help="order targets by learned capture success (experience)")
    cau_.add_argument("--experience", default=None, help="experience store (with --adaptive)")
    cau_.add_argument("--confirm", action="store_true", help="required in LAB to actually run")
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

    sd = sub.add_parser("sdr", help="ingest SDR output (rtl_433/ADS-B/POCSAG/ACARS) or analyze a spectrum sweep")
    sdsub = sd.add_subparsers(dest="sdr_cmd", required=True)
    sdi = sdsub.add_parser("ingest", help="ingest decoder output into the sighting store")
    sdi.add_argument("file", help="rtl_433 -F json / dump1090 json / multimon-ng POCSAG text / acarsdec json")
    g = sdi.add_mutually_exclusive_group()
    g.add_argument("--rtl433", action="store_true", help="records are rtl_433 ISM devices (default)")
    g.add_argument("--adsb", action="store_true", help="records are dump1090 ADS-B aircraft")
    g.add_argument("--pocsag", action="store_true", help="lines are multimon-ng POCSAG (capcode stored, message NOT)")
    g.add_argument("--acars", action="store_true", help="records are acarsdec JSON (tail stored, text NOT)")
    sdi.add_argument("--lat", type=float, help="receiver latitude (tags position-less sightings)")
    sdi.add_argument("--lon", type=float, help="receiver longitude (tags position-less sightings)")
    sdi.add_argument("--db", help="sighting store path")
    sds = sdsub.add_parser("spectrum", help="analyze an rtl_power CSV sweep: noise floor + active peaks")
    sds.add_argument("file", help="rtl_power CSV output")
    sds.add_argument("--threshold", type=float, default=8.0, help="dB above floor to count as a peak (default 8)")
    sds.add_argument("--max-peaks", type=int, default=20, help="max peaks to report (default 20)")
    sdsub.add_parser("presets", help="list guided SDR presets (pick what to listen to)")
    sdt = sdsub.add_parser("tune", help="print the exact tuned command for a preset (Augur sets the frequency)")
    sdt.add_argument("preset", help="preset name (see `redux sdr presets`)")
    sdt.add_argument("--device", type=int, default=0, help="RTL-SDR device index (default 0)")
    sdt.add_argument("--freq", default=None, help="override center frequency (e.g. 433.92M)")
    sdt.add_argument("--scan-range", default=None, help="for the scan preset: START:STOP:STEP (e.g. 430M:440M:25k)")
    sdt.add_argument("--duration", type=int, default=None, help="for scan: sweep for N seconds instead of once")
    sdw = sdsub.add_parser("waterfall", help="render an rtl_power sweep as a waterfall (ASCII + optional PGM image)")
    sdw.add_argument("file", help="rtl_power CSV output (repeated sweeps over time)")
    sdw.add_argument("--width", type=int, default=64, help="columns in the ASCII waterfall (default 64)")
    sdw.add_argument("--out", default=None, help="also write a grayscale PGM image to this path")
    sdw.add_argument("--svg", default=None, help="also write an SVG heatmap (web/phone view) to this path")
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
    rp.add_argument("--html", help="also write a dark-themed HTML report to this path")
    rpsub = rp.add_subparsers(dest="report_cmd", required=True)
    rpsub.add_parser("demo", help="build a sample report (includes an out-of-scope action)")
    rpb = rpsub.add_parser("build", help="build from a scope file + actions JSON or the signed auth log")
    rpb.add_argument("--engagement", default="engagement")
    rpb.add_argument("--scope-file", default=_DEFAULT_SCOPE_PATH)
    rpb.add_argument("--actions", default=None, help="actions JSON: [{ts,action,target,reason,result}]")
    rpb.add_argument("--from-authlog", action="store_true",
                     help="build the timeline from the signed chain-of-authorization log (+ integrity proof)")
    rpb.add_argument("--authlog", default=None, help="auth-log path (with --from-authlog)")
    rpb.add_argument("--key", default=None, help="signing-key path (to verify signatures)")
    rp.set_defaults(func=cmd_report)

    rg = sub.add_parser("range", help="purple Range mode — grade your detectors vs ATT&CK (and D3FEND)")
    rgsub = rg.add_subparsers(dest="range_cmd", required=True)
    rgsub.add_parser("techniques", help="list the ATT&CK→D3FEND framework map")
    rgsub.add_parser("demo", help="run a simulated attack set and score detector coverage")
    rglf = rgsub.add_parser("livefire", help="LAB-only: fire REAL attacks at your own gear and score blue (POWER)")
    rglf.add_argument("--scope-file", default=None, help="scope store (default: the device scope)")
    rglf.add_argument("--targets", nargs="*", help="targets (default: armed scope BSSIDs)")
    rglf.add_argument("--fired", action="append", help="detector that blue reports firing (repeatable)")
    rglf.add_argument("--confirm", action="store_true", help="required in LAB to actually run")
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
    dx.add_argument("--dna", action="store_true",
                    help="behavioral re-id: link MACs by probing behavior (timing/rates/IE), not just PNL/IE equality")
    dx.add_argument("--demo", action="store_true", help="with --dna: synthetic traces (no radio)")
    dx.set_defaults(func=cmd_dex)

    tb = sub.add_parser("toolbox", help="curated catalog of useful tools + the exact command to add each")
    tbsub = tb.add_subparsers(dest="toolbox_cmd", required=True)
    tbl = tbsub.add_parser("list", help="list tools (optionally filter)")
    tbl.add_argument("query", nargs="?", default="", help="filter by name/summary/feature")
    tbl.add_argument("--category", default=None,
                     help="sdr/wifi/crack/recon/forensics/bt/gps/ir/tx/companion")
    tbs = tbsub.add_parser("show", help="full detail + install command for one tool")
    tbs.add_argument("name")
    tbi = tbsub.add_parser("install", help="print (or --run) the install command for a tool")
    tbi.add_argument("name")
    tbi.add_argument("--run", action="store_true", help="execute the install on this device (needs root)")
    tb.set_defaults(func=cmd_toolbox)

    cap = sub.add_parser("capabilities", aliases=["caps"],
                         help="the honest catalog of everything Augur can do + how ready each is")
    capsub = cap.add_subparsers(dest="capabilities_cmd", required=True)
    capl = capsub.add_parser("list", help="every capability with its readiness state")
    capl.add_argument("--category", default=None,
                      help="wifi/net/sdr/rf/bt/gps/power/sense/mesh/blue/storage")
    capl.add_argument("--json", action="store_true", help="machine-readable records")
    caps_ = capsub.add_parser("show", help="full detail + what it needs + how to use one capability")
    caps_.add_argument("id")
    caps_.add_argument("--json", action="store_true")
    capc = capsub.add_parser("completion", help="evidence-based completion picture (no subjective %)")
    capc.add_argument("--json", action="store_true")
    cap.set_defaults(func=cmd_capabilities)
    sim = sub.add_parser("simulate",
                         help="boot the whole live stack with no Pi (fake engine replaying a scenario)")
    sim.add_argument("--port", type=int, default=8080, help="status API / dashboard port (default 8080)")
    sim.add_argument("--seconds", type=float, default=None,
                     help="auto-stop after N seconds (default: run until Ctrl-C)")
    sim.add_argument("--scenario", default=None,
                     help="a JSON list of {tag,time,data} events to replay (default: a built-in session)")
    sim.add_argument("--once", action="store_true",
                     help="tick a few times, print the status snapshot JSON, and exit")
    sim.add_argument("--ticks", type=int, default=14, help="with --once: how many tick steps to run")
    sim.set_defaults(func=cmd_simulate)

    hw = sub.add_parser("hardware", aliases=["hw"],
                        help="what's plugged in and what capability each device unlocks")
    hw.add_argument("--json", action="store_true", help="machine-readable output")
    hw.set_defaults(func=cmd_hardware)

    sh = sub.add_parser("shadow", help="blue-shadow — what a defender sees from each action (+ quieter paths)")
    sh.add_argument("action", nargs="?", default=None, help="one action to detail (else list all)")
    sh.add_argument("--json", action="store_true", help="machine-readable output")
    sh.set_defaults(func=cmd_shadow)

    st = sub.add_parser("stealth", help="OPSEC for Augur's own device (MAC randomization, …)")
    stsub = st.add_subparsers(dest="stealth_cmd", required=True)
    stm = stsub.add_parser("mac", help="randomize a radio's MAC so the box doesn't present a stable fingerprint")
    stm.add_argument("iface", help="interface to randomize (e.g. wlan0)")
    stm.add_argument("--mode", choices=["vendor", "laa"], default="vendor",
                     help="vendor = plausible real OUI (blend in, default); laa = locally-administered random")
    stm.add_argument("--apply", action="store_true", help="actually change it on this device (needs root)")
    stq = stsub.add_parser("quiet", help="Quiet/Ghost mode — go dark (LAB): passive-first + frame budget + MAC rotate")
    stq.add_argument("quiet_cmd", choices=["on", "off", "status"], nargs="?", default="status")
    stq.add_argument("--budget", type=int, default=None, help="max frames to emit while quiet (0 = fully passive)")
    stq.add_argument("--file", default=None, help="stealth config path (default: the device stealth state)")
    stp = stsub.add_parser("pace", help="low-and-slow planner — spread N actions under a footprint cap (jittered)")
    stp.add_argument("--count", type=int, default=5, help="how many actions to schedule")
    stp.add_argument("--kind", default="deauth", help="action kind (deauth/assoc/probe/eapol/pmkid…)")
    stp.add_argument("--window", type=float, default=300.0, help="sliding window seconds (default 300)")
    stp.add_argument("--cap", type=int, default=14, help="footprint-weight ceiling per window (default 14 = quiet)")
    stp.add_argument("--min-gap", type=float, default=8.0, help="floor spacing between actions (seconds)")
    stp.add_argument("--jitter", type=float, default=6.0, help="random extra spacing so cadence isn't a pattern")
    stp.add_argument("--seed", type=int, default=0, help="jitter seed (reproducible schedules)")
    stp.add_argument("--quiet", action="store_true", help="also honour the saved Quiet-mode gate")
    stp.add_argument("--confirm", action="store_true", help="treat over-budget transmits as confirmed")
    stp.add_argument("--file", default=None, help="stealth config path (with --quiet)")
    st.set_defaults(func=cmd_stealth)

    nb = sub.add_parser("notebook", help="lab session journal — log activity + render a report")
    nb.add_argument("--path", default="/captures/live/notebook.jsonl", help="notebook path")
    nbsub = nb.add_subparsers(dest="notebook_cmd", required=True)
    nbl = nbsub.add_parser("log", help="record an entry")
    nbl.add_argument("kind", help="recon/capture/crack/signal/mode/offense/scan/note")
    nbl.add_argument("summary", help="what happened")
    nbsub.add_parser("show", help="print the session timeline")
    nbr = nbsub.add_parser("report", help="render a Markdown session report")
    nbr.add_argument("--title", default="Lab session")
    nbr.add_argument("--out", default=None, help="write the report to this file")
    nbsub.add_parser("clear", help="clear the notebook")
    nb.set_defaults(func=cmd_notebook)

    tp = sub.add_parser("tpms", help="re-identify returning vehicles by tire-sensor IDs (from rtl_433 JSON)")
    tp.add_argument("file", help="rtl_433 -F json capture")
    tp.add_argument("--min-gap", type=float, default=300.0, help="seconds between sightings to count as a 'return' (default 300)")
    tp.set_defaults(func=cmd_tpms)

    sx = sub.add_parser("signals", help="the Signal Dex: one catalog across WiFi/BLE/ISM/ADS-B/sub-GHz/IR")
    sx.add_argument("--db", default=None, help="sighting store path")
    sx.add_argument("--subghz-lib", default="/captures/config/subghz.json", help="sub-GHz library")
    sx.add_argument("--ir-lib", default="/captures/config/ir.json", help="IR library")
    sx.add_argument("--domain", default=None, help="show only one medium (wifi/ble/ism/adsb/pocsag/acars/subghz/ir)")
    sx.add_argument("--limit", type=int, default=50, help="max entries to print (default 50)")
    sx.set_defaults(func=cmd_signals)

    gp = sub.add_parser("gps", help="read the current position from gpsd (USB/serial/BT GPS)")
    gp.add_argument("--host", default="127.0.0.1", help="gpsd host (default 127.0.0.1)")
    gp.add_argument("--port", type=int, default=2947, help="gpsd port (default 2947)")
    gp.add_argument("--timeout", type=float, default=5.0, help="read timeout seconds")
    gp.set_defaults(func=cmd_gps)

    hr = sub.add_parser("here", help="temporal RF diff: what changed at this location since your last visit")
    hr.add_argument("--label", default=None, help="name this place (works indoors / without GPS)")
    hr.add_argument("--lat", type=float, default=None, help="latitude (else a gpsd fix is tried)")
    hr.add_argument("--lon", type=float, default=None, help="longitude (else a gpsd fix is tried)")
    hr.add_argument("--precision", type=int, default=7, help="geohash cell precision (7 ~ 150 m)")
    hr.add_argument("--rssi-delta", type=int, default=18, help="dBm shift that flags a device CHANGED")
    hr.add_argument("--window-hours", type=float, default=None, help="only count devices seen in the last N hours")
    hr.add_argument("--db", default=None, help="sightings DB (default: the device store)")
    hr.add_argument("--memory", default=None, help="location-memory file (default: /captures/live/location_memory.json)")
    hr.add_argument("--pnl", default=None, help="probe PNL store to de-randomize returning devices (reid); e.g. /captures/live/pnl.json")
    hr.add_argument("--no-save", action="store_true", help="peek only — don't update the baseline for this place")
    hr.add_argument("--host", default="127.0.0.1", help="gpsd host for an auto position fix")
    hr.add_argument("--port", type=int, default=2947, help="gpsd port")
    hr.add_argument("--timeout", type=float, default=3.0, help="gpsd read timeout seconds")
    hr.set_defaults(func=cmd_here)

    ir = sub.add_parser("ir", help="infrared: TV-B-Gone power blast + loadable IR code library (POWER-tier)")
    ir.add_argument("--lib", default="/captures/config/ir.json", help="IR code library path")
    irsub = ir.add_subparsers(dest="ir_cmd", required=True)
    irsub.add_parser("codes", help="show the built-in TV power codes")
    irsub.add_parser("list", help="list saved IR codes")
    iri = irsub.add_parser("import", help="import a Flipper .ir file into the library")
    iri.add_argument("file", help="path to a .ir file")
    irb = irsub.add_parser("blast", help="TV-B-Gone: blast all power codes (LAB + --confirm)")
    irb.add_argument("--confirm", action="store_true", help="per-action opt-in")
    irb.add_argument("--scope-file", default=None)
    irr = irsub.add_parser("replay", help="replay one saved IR code (LAB + --confirm)")
    irr.add_argument("name", help="code name (see `redux ir list`)")
    irr.add_argument("--confirm", action="store_true", help="per-action opt-in")
    irr.add_argument("--scope-file", default=None)
    ir.set_defaults(func=cmd_ir)

    sg = sub.add_parser("subghz", help="sub-GHz signal library: capture/catalog (RX) + gated replay (keyfobs, garage, ISM)")
    sg.add_argument("--lib", default="/captures/config/subghz.json", help="signal library path")
    sgsub = sg.add_subparsers(dest="subghz_cmd", required=True)
    sgc = sgsub.add_parser("capture", help="print the RX capture command for a frequency (works with RTL-SDR)")
    sgc.add_argument("freq", help="frequency, e.g. 433.92M or 315M")
    sgc.add_argument("--seconds", type=int, default=10, help="capture duration (default 10)")
    sgc.add_argument("--raw", action="store_true", help="raw IQ grab (rtl_sdr) for later replay, not decode")
    sgc.add_argument("--out", default=None, help="raw capture output path")
    sgc.add_argument("--device", type=int, default=0, help="RTL-SDR device index")
    sgsub.add_parser("list", help="list catalogued signals")
    sga = sgsub.add_parser("add", help="add a signal to the library")
    sga.add_argument("name"); sga.add_argument("freq", help="e.g. 390M")
    sga.add_argument("--protocol", default=None, help="rtl_433 protocol/model if known")
    sga.add_argument("--capture", default=None, help="path to a raw IQ/.ook capture file (needed to replay)")
    sga.add_argument("--note", default=None)
    sgr = sgsub.add_parser("remove", help="remove a signal"); sgr.add_argument("id")
    sgrp = sgsub.add_parser("replay", help="replay a signal (POWER-tier: LAB + --confirm + a TX backend)")
    sgrp.add_argument("id", help="signal id or name")
    sgrp.add_argument("--confirm", action="store_true", help="the per-action opt-in (required in LAB)")
    sgrp.add_argument("--backend", default=None, choices=("hackrf", "rpitx", "cc1101"))
    sgrp.add_argument("--scope-file", default=None, help="read mode from this scope file")
    sg.set_defaults(func=cmd_subghz)

    pw = sub.add_parser("power", help="power-tier gate: the strongest tools (LAB-only + confirm; some forbidden)")
    pw.add_argument("--mode", default=None, choices=("lab", "field"), help="check a specific mode (default: device mode)")
    pw.add_argument("--scope-file", default=None, help="read mode from this scope file")
    pwsub = pw.add_subparsers(dest="power_cmd", required=True)
    pwsub.add_parser("list", help="show what's available in a mode (and what's hidden/forbidden)")
    pwc = pwsub.add_parser("check", help="show the gate decision for a capability")
    pwc.add_argument("capability", help="capability id (e.g. sdr.replay, ir.blast, badusb.hid)")
    pwc.add_argument("--confirm", action="store_true", help="the per-action opt-in (press OK)")
    pw.set_defaults(func=cmd_power)
    ou = sub.add_parser("oui", help="MAC vendor lookup / device classification / full-DB import")
    ousub = ou.add_subparsers(dest="oui_cmd", required=True)
    oul = ousub.add_parser("lookup", help="vendor for a MAC (built-in + imported DB)")
    oul.add_argument("mac", help="a MAC address (aa:bb:cc:dd:ee:ff)")
    ouc = ousub.add_parser("classify", help="vendor + device-class guess + randomized-MAC flag")
    ouc.add_argument("mac", help="a MAC address")
    oui_ = ousub.add_parser("import", help="merge a full IEEE oui.txt / Wireshark manuf DB on top of the built-in table")
    oui_.add_argument("source", help="path to the vendor DB (IEEE oui.txt, manuf, or normalized TSV)")
    oui_.add_argument("--dest", default="/captures/config/oui.tsv",
                      help="where the normalized DB is written (default /captures/config/oui.tsv)")
    ousub.add_parser("stats", help="counts for the active vendor table")
    ou.set_defaults(func=cmd_oui)
    ln = sub.add_parser("learn", help="experience layer: what actually worked (honest, glass-box)")
    ln.add_argument("--path", default="/captures/live/experience.jsonl",
                    help="experience log path (default /captures/live/experience.jsonl)")
    lnsub = ln.add_subparsers(dest="learn_cmd", required=True)
    lnsub.add_parser("summary", help="overall totals, success rate, outcomes")
    lns = lnsub.add_parser("stats", help="ranked stats for a dimension (exploration-aware)")
    lns.add_argument("--dim", default="channel", help="channel|band|hour|vendor|kind")
    lnr = lnsub.add_parser("rank", help="rank specific candidate values of a dimension")
    lnr.add_argument("--dim", default="channel", help="channel|band|hour|vendor|kind")
    lnr.add_argument("--candidates", default=None, help="comma-separated values to rank (e.g. 1,6,11)")
    lnsub.add_parser("reset", help="clear the experience log")
    ln.set_defaults(func=cmd_learn)
    km = sub.add_parser("karma", help="probe harvesting (passive) + aimed karma plan (scope-gated)")
    km.add_argument("--store", default="/captures/live/pnl.json",
                    help="harvested PNL store (default /captures/live/pnl.json)")
    kmsub = km.add_subparsers(dest="karma_cmd", required=True)
    kmsub.add_parser("clients", help="per-client preferred-network list (who's looking for what)")
    kmsub.add_parser("ssids", help="probed SSIDs ranked by how many clients want them")
    kmp = kmsub.add_parser("plan", help="aimed advertise plan (only in-scope clients; glass-box)")
    kmp.add_argument("--scope-file", default=None, help="scope file (default: the device scope)")
    km.set_defaults(func=cmd_karma)
    rd = sub.add_parser("reid", help="re-identify devices across randomized MACs from harvested probe PNLs")
    rd.add_argument("--store", default="/captures/live/pnl.json",
                    help="harvested PNL store (default /captures/live/pnl.json)")
    rd.add_argument("--min-weight", type=float, default=0.5,
                    help="link-strength threshold (sum of 1/rarity over shared SSIDs; default 0.5)")
    rd.add_argument("--max-common-fraction", type=float, default=0.5,
                    help="SSIDs probed by more than this fraction of MACs are noise, not evidence")
    rd.add_argument("--db", default="/captures/live/sightings.db",
                    help="sightings DB to build a dossier (where/when/signal + vendor) per device")
    rd.add_argument("--precision", type=int, default=7, help="geohash cell precision for places (7 ~ 150 m)")
    rd.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    rd.set_defaults(func=cmd_reid)
    wp = sub.add_parser("wps", help="WPS / Pixie-Dust: passive wash discovery + scope-gated attack")
    wpsub = wp.add_subparsers(dest="wps_cmd", required=True)
    wpscan = wpsub.add_parser("scan", help="list WPS-enabled APs from a `wash` capture (passive)")
    wpscan.add_argument("file", help="wash output text")
    for _name, _help in (("plan", "show the exact reaver/bully command (glass-box)"),
                         ("run", "run the WPS attack (gated; unlocked in LAB)")):
        w = wpsub.add_parser(_name, help=_help)
        w.add_argument("--bssid", required=True, help="target AP BSSID")
        w.add_argument("--channel", type=int, default=None, help="AP channel")
        w.add_argument("--ssid", default="", help="AP SSID (label only)")
        w.add_argument("--tool", default="reaver", choices=("reaver", "bully"))
        w.add_argument("--no-pixie", action="store_true", help="online PIN brute instead of pixie-dust")
        w.add_argument("--iface", default="wlan1mon", help="monitor interface (default wlan1mon)")
        w.add_argument("--scope-file", default=None, help="scope file (default: the device scope)")
        if _name == "run":
            w.add_argument("--authorized", action="store_true",
                           help="confirm this AP is yours to test (not needed in LAB mode)")
            w.add_argument("--timeout", type=int, default=180, help="max attack seconds (default 180)")
    wp.set_defaults(func=cmd_wps)
    tk = sub.add_parser("tracker", help="unwanted-tracker check: devices moving WITH you (AirTag-style)")
    tk.add_argument("--log", default="/captures/live/observations.jsonl",
                    help="observation history log (default /captures/live/observations.jsonl)")
    tk.add_argument("--min-minutes", type=float, default=10.0, help="min duration to flag (default 10)")
    tk.add_argument("--min-span-m", type=float, default=100.0, help="min travel span in meters (default 100)")
    tk.add_argument("--all", action="store_true", help="show every analyzed device, not just followers")
    tk.set_defaults(func=cmd_tracker)
    cd = sub.add_parser("crack-distribute",
                        help="split one hashcat job across the mesh (disjoint per-node slices)")
    cd.add_argument("hashfile", help="hash file (same on every node)")
    cd.add_argument("--mode", type=int, required=True, help="hashcat -m hash mode")
    cd.add_argument("--attack", type=int, default=0, choices=(0, 3), help="0=wordlist, 3=mask")
    cd.add_argument("--wordlist", default=None, help="wordlist path (attack 0; lines counted to partition)")
    cd.add_argument("--mask", default=None, help="hashcat mask (attack 3)")
    cd.add_argument("--keyspace", type=int, default=None, help="mask keyspace total (attack 3)")
    cd.add_argument("--nodes", type=int, required=True, help="number of swarm nodes to split across")
    cd.add_argument("--binary", default="hashcat", help="hashcat binary name")
    cd.set_defaults(func=cmd_crack_distribute)
    pu = sub.add_parser("push", help="push-to-phone alerts (ntfy / pushover / webhook)")
    pu.add_argument("--service", default="ntfy", choices=("ntfy", "pushover", "webhook"))
    pu.add_argument("--target", required=True, help="ntfy topic URL / pushover user key / webhook URL")
    pu.add_argument("--token", default="", help="pushover app token (pushover only)")
    pusub = pu.add_subparsers(dest="push_cmd", required=True)
    pusub.add_parser("test", help="send a test notification")
    pus = pusub.add_parser("send", help="send a title + message")
    pus.add_argument("title", help="notification title")
    pus.add_argument("message", help="notification body")
    pus.add_argument("--priority", type=int, default=3, help="0..5 (default 3)")
    pus.add_argument("--tags", default="", help="optional tags/emoji (ntfy)")
    pu.set_defaults(func=cmd_push)
    hp = sub.add_parser("help", help="in-product guidance: info-bubbles, when-to-use, how-to for each feature")
    hp.add_argument("topic", nargs="?", default=None, help="a topic id (run `redux help` for the list)")
    hp.add_argument("--search", default=None, help="find topics by keyword")
    hp.set_defaults(func=cmd_help)

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
    ag = sub.add_parser("authlog", help="tamper-evident chain-of-authorization log (scope changes + fire actions)")
    ag.add_argument("--log", default=None, help="auth-log path (default: the device auth log)")
    ag.add_argument("--key", default=None, help="signing-key path (default: the device key)")
    agsub = ag.add_subparsers(dest="authlog_cmd", required=True)
    agsub.add_parser("verify", help="prove the chain is intact (and signatures, if a key exists)")
    ags = agsub.add_parser("show", help="print the log")
    ags.add_argument("--json", action="store_true", help="machine-readable output")
    agr = agsub.add_parser("record", help="append a manual note to the log")
    agr.add_argument("event", help="event name, e.g. engagement.start")
    agr.add_argument("--detail", action="append", help="key=value (repeatable)")
    agk = agsub.add_parser("init-key", help="create the device signing key (entries become signed)")
    agk.add_argument("--force", action="store_true", help="replace an existing key (invalidates past signatures)")
    ag.set_defaults(func=cmd_authlog)
    tr = sub.add_parser("triage", help="crack ROI triage + passive-capture forecast (learning aid; heuristic)")
    tr.add_argument("--dir", default="/captures/ready", help="dir of captured .hc22000 artifacts to rank")
    tr.add_argument("--file", default=None, help="a single .hc22000 file instead of --dir")
    tr.add_argument("--pnl", default="/captures/live/pnl.json", help="harvested PNL store for the capture forecast")
    tr.add_argument("--reassoc-per-hour", type=float, default=0.5,
                    help="assumed per-client reassociation rate for the forecast (default 0.5/h)")
    tr.add_argument("--json", action="store_true", help="machine-readable output")
    tr.set_defaults(func=cmd_triage)
    pf = sub.add_parser("preflight", help="is this capture crackable? decode PMKID/EAPOL completeness before spending GPU")
    pf.add_argument("file", help="a .hc22000 or raw .pcap/.pcapng/.cap")
    pf.add_argument("--json", action="store_true", help="machine-readable output")
    pf.set_defaults(func=cmd_preflight)
    cpp = sub.add_parser("crack-prep", help="chain preflight+triage+forge into one ranked crack work order")
    cpp.add_argument("--dir", default="/captures/ready", help="dir of captured .hc22000 artifacts")
    cpp.add_argument("--file", default=None, help="a single .hc22000 file instead of --dir")
    cpp.add_argument("--top", type=int, default=0, help="only prep the top N targets (0 = all)")
    cpp.add_argument("--token", action="append", help="OSINT seed passed to forge (repeatable)")
    cpp.add_argument("--out-dir", default=None, help="write each target's forged wordlist to this dir")
    cpp.add_argument("--json", action="store_true", help="machine-readable output")
    cpp.set_defaults(func=cmd_crackprep)
    fg = sub.add_parser("forge", help="target-aware wordlist/mask forge (recon -> targeted candidates)")
    fg.add_argument("ssid", nargs="?", default="", help="the target SSID (optional)")
    fg.add_argument("--token", action="append", help="OSINT seed (owner name, pet, street, zip…); repeatable")
    fg.add_argument("--pnl", default=None, help="harvested PNL store to mine nearby SSIDs as seeds")
    fg.add_argument("--bssid", default=None, help="target BSSID (adds the AP vendor as a seed via OUI)")
    fg.add_argument("--out", default=None, help="write the candidate wordlist to this file")
    fg.add_argument("--min-len", type=int, default=8, help="minimum length (WPA2-PSK is 8+; default 8)")
    fg.add_argument("--max", type=int, default=2000, help="cap the candidate count (default 2000)")
    fg.add_argument("--environment", action="store_true",
                    help="grow seeds from everything the box observed (PNLs + passive-PMKID SSIDs + looted-file strings)")
    fg.add_argument("--loot-dir", default=None,
                    help="with --environment: triage looted files here and mine their strings/metadata")
    fg.add_argument("--json", action="store_true", help="machine-readable output")
    fg.set_defaults(func=cmd_forge)
    ex = sub.add_parser("explain", help="purple-team: how a technique is detected and defended")
    ex.add_argument("technique", nargs="?", default="", help="technique key (omit to list all)")
    ex.add_argument("--json", action="store_true", help="machine-readable output")
    ex.set_defaults(func=cmd_explain)
    ra = sub.add_parser("radar", help="threat radar: counter-surveillance (evil twins, KARMA lures) from sightings")
    ra.add_argument("--db", default=None, help="sightings DB (default /captures/live/sightings.db)")
    ra.add_argument("--pnl", default=None, help="harvested PNL store (flags lures for networks you seek)")
    ra.add_argument("--trusted", action="append", help="a BSSID you own/recognise (repeatable); armed scope BSSIDs are trusted too")
    ra.add_argument("--scope-file", default=None, help="treat this scope's armed BSSIDs as trusted (default: the device scope)")
    ra.add_argument("--new-window-hours", type=float, default=1.0, help="how recent a 'new' AP is (default 1h)")
    ra.add_argument("--strong-rssi", type=int, default=-50, help="dBm at/above which a new AP is 'close' (default -50)")
    ra.add_argument("--json", action="store_true", help="machine-readable output")
    ra.set_defaults(func=cmd_radar)
    fu = sub.add_parser("fuse", help="co-occurrence fusion: cluster devices that travel together (time+place)")
    fu.add_argument("--log", default="/captures/live/observations.jsonl", help="observation time-series log")
    fu.add_argument("--window-seconds", type=float, default=120.0, help="time-bucket width (default 120s)")
    fu.add_argument("--precision", type=int, default=7, help="geohash cell precision (7 ~ 150 m)")
    fu.add_argument("--min-co", type=int, default=2, help="min shared windows to link a pair (default 2)")
    fu.add_argument("--min-score", type=float, default=0.3, help="min Jaccard overlap to link (default 0.3)")
    fu.add_argument("--json", action="store_true", help="machine-readable output")
    fu.set_defaults(func=cmd_fuse)
    loc = sub.add_parser("locate", help="direction-find a signal to its origin from positioned RSSI (fox-hunt)")
    loc.add_argument("target", nargs="?", default="", help="MAC to hunt (omit = all observations pooled)")
    loc.add_argument("--obs", default="/captures/live/observations.jsonl", help="observation time-series log")
    loc.add_argument("--window", type=int, default=3, help="recent-readings window for warmer/colder (default 3)")
    loc.add_argument("--json", action="store_true", help="machine-readable output")
    loc.set_defaults(func=cmd_locate)
    fh = sub.add_parser("firehose", help="LAB-only glass-box stream: every decision + every device seen, by time")
    fh.add_argument("--scope-file", default=None, help="scope store (decides LAB vs FIELD)")
    fh.add_argument("--authlog", default=None, help="auth-log path (default: the device auth log)")
    fh.add_argument("--db", default=None, help="sightings DB (default: the device store)")
    fh.add_argument("--tail", type=int, default=200, help="show the most recent N events (default 200)")
    fh.add_argument("--json", action="store_true", help="machine-readable output")
    fh.set_defaults(func=cmd_firehose)
    gr = sub.add_parser("graph", help="entity graph: devices (reid) + travels-with (fusion) + sightings/vendor/loot")
    gr.add_argument("--pnl", default="/captures/live/pnl.json", help="harvested PNL store (reid identity)")
    gr.add_argument("--obs", default="/captures/live/observations.jsonl", help="observation log (co-travel edges)")
    gr.add_argument("--db", default="/captures/live/sightings.db", help="sightings DB (annotations)")
    gr.add_argument("--loot", default="/captures/loot/vault.json", help="loot vault (key-recovered flag)")
    gr.add_argument("--json", action="store_true", help="machine-readable output")
    gr.set_defaults(func=cmd_graph)
    nr = sub.add_parser("narrate", help="explain-as-it-goes: the auth log with each action's detection + mitigation")
    nr.add_argument("--authlog", default=None, help="auth-log path (default: the device auth log)")
    nr.add_argument("--json", action="store_true", help="machine-readable output")
    nr.set_defaults(func=cmd_narrate)
    sc = sub.add_parser("scrub", help="secure-wipe the device's OWN data (scrub keeps the brain; panic burns it all)")
    sc.add_argument("--root", default=None, help="captures root (default: /captures)")
    sc.add_argument("--panic", action="store_true", help="full burn: wipe brain + geo + target list too (keeps the box bootable)")
    scsub = sc.add_subparsers(dest="scrub_cmd", required=True)
    scsub.add_parser("status", help="show on-disk sensitive-data exposure")
    scsub.add_parser("plan", help="dry-run: what this mode would wipe vs keep")
    scr = scsub.add_parser("run", help="execute the scrub (DRY-RUN unless --apply)")
    scr.add_argument("--apply", action="store_true", help="actually wipe (otherwise dry-run)")
    scr.add_argument("--no-overwrite", action="store_true", help="unlink without the best-effort overwrite pass")
    sc.set_defaults(func=cmd_scrub)
    aw = sub.add_parser("airwave", help="Airwave DVR — scrub back through RF history (timeline/scrub/track)")
    aw.add_argument("--dvr", default=None, help="DVR JSONL file (default: the device airwave log)")
    aw.add_argument("--demo", action="store_true", help="synthetic RF timeline (no radio)")
    awsub = aw.add_subparsers(dest="airwave_cmd", required=True)
    awsub.add_parser("summary", help="overview of recorded airtime")
    awt = awsub.add_parser("timeline", help="activity histogram over time (the scrub bar)")
    awt.add_argument("--bucket", type=float, default=60.0, help="bucket size in seconds")
    aws = awsub.add_parser("scrub", help="snapshot the air at an instant")
    aws.add_argument("--at", type=float, required=True, help="timestamp to scrub to")
    aws.add_argument("--radius", type=float, default=5.0, help="+/- seconds around --at")
    awk = awsub.add_parser("track", help="every time one BSSID appeared")
    awk.add_argument("bssid", help="BSSID to follow")
    aw.set_defaults(func=cmd_airwave)
    rp = sub.add_parser("responder", help="harvest NetNTLM hashes on the joined LAN (LLMNR/NBT-NS/mDNS poisoning) → crack queue")
    rp.add_argument("iface", help="LAN interface Augur joined (e.g. wlan0)")
    rpsub = rp.add_subparsers(dest="responder_cmd", required=True)
    rpp = rpsub.add_parser("plan", help="show the exact command + posture (no run)")
    rpp.add_argument("--analyze", action="store_true", help="passive: watch, don't answer")
    rph = rpsub.add_parser("harvest", help="run bounded + parse captured hashes into the crack queue")
    rph.add_argument("--seconds", type=int, default=60, help="how long to run (time-boxed; default 60)")
    rph.add_argument("--analyze", action="store_true", help="passive: watch, don't answer (always allowed)")
    rph.add_argument("--authorized", action="store_true", help="FIELD: actually poison (LAN must be armed in scope)")
    rph.add_argument("--lab", action="store_true", help="LAB: run unleashed on your own network")
    rph.add_argument("--scope-file", default=None, help="scope store (FIELD gate)")
    rph.add_argument("--out-dir", default="/captures/loot/netntlm", help="where to drop harvested hash files")
    rp.set_defaults(func=cmd_responder)
    pk = sub.add_parser("pmkid", help="passive PMKID sweep — zero-transmit loot (harvest PMKIDs APs leak, correlate to scope/loot/forge)")
    pksub = pk.add_subparsers(dest="pmkid_cmd", required=True)
    pksub.add_parser("plan", help="explain the zero-transmit posture + what it correlates")
    pks = pksub.add_parser("sweep", help="harvest + rank PMKID loot from real .22000 output or synthetic frames")
    pks.add_argument("--from-22000", default=None, help="hashcat-22000 file the capture engine wrote")
    pks.add_argument("--demo", action="store_true", help="synthetic PMKID sightings (no radio)")
    pks.add_argument("--scoped", action="store_true", help="gate by the device scope (default: LAB unscoped)")
    pks.add_argument("--scope-file", default=None, help="scope store (with --scoped)")
    pks.add_argument("--json", action="store_true", help="machine-readable output")
    pk.set_defaults(func=cmd_pmkid)
    lo = sub.add_parser("loot", help="recovered-credential vault + scope-gated network pivot")
    lo.add_argument("--vault", default=None, help="vault file (default: the device loot vault)")
    losub = lo.add_subparsers(dest="loot_cmd", required=True)
    la = losub.add_parser("add", help="store a recovered WPA PSK")
    la.add_argument("psk", help="the recovered passphrase")
    la.add_argument("--bssid", default=None, help="AP BSSID the PSK belongs to")
    la.add_argument("--ssid", default=None, help="network name")
    la.add_argument("--source", default="manual", help="where it came from (crack/manual/…)")
    ll = losub.add_parser("list", help="list vaulted credentials (secrets redacted by default)")
    ll.add_argument("--reveal", action="store_true", help="show the actual secrets")
    ll.add_argument("--json", action="store_true", help="machine-readable output")
    lp = losub.add_parser("pivot", help="recon + credential-reuse into a subnet (FIELD: plan unless --authorized; LAB: runs)")
    lp.add_argument("--subnet", default=None, help="CIDR to pivot into (else the armed CIDRs in scope)")
    lp.add_argument("--user", action="append", help="username to try (repeatable; default: admin, root)")
    lp.add_argument("--pw", action="append", help="extra password to try (vaulted PSKs are included automatically)")
    lp.add_argument("--authorized", action="store_true", help="FIELD only: actually fire (targets must be armed in scope)")
    lp.add_argument("--scope-file", default=None, help="scope store (default: the device scope)")
    lt = losub.add_parser("triage", help="forensic triage of pulled files (exiftool/binwalk/steghide/secrets)")
    lt.add_argument("files", nargs="+", help="files to triage")
    lt.add_argument("--json", action="store_true", help="machine-readable output")
    lf = losub.add_parser("files", help="scope-gated SMB file-loot: pull interesting files off a host with a recovered credential")
    lf.add_argument("host", help="target host (IP or name); must be armed in scope")
    lf.add_argument("--user", required=True, help="username for the recovered credential")
    lf.add_argument("--pw", required=True, help="password for the recovered credential")
    lf.add_argument("--dest", default="/captures/loot/files", help="where to drop pulled files")
    lf.add_argument("--max-files", type=int, default=25, help="cap on files pulled (default: 25)")
    lf.add_argument("--triage", action="store_true", help="run forensic triage on the pulled files")
    lf.add_argument("--scope-file", default=None, help="scope store (default: the device scope)")
    lo.set_defaults(func=cmd_loot)
    return p


def _ensure_tool_path() -> None:
    """iw / ip / rfkill live in /usr/sbin (and /sbin) on Debian / Raspberry Pi OS,
    which a non-root login PATH omits. redux invokes them by bare name, so without
    this the CLI reports installed radio tools as MISSING — the "iw: command not
    found" trap that makes a working radio look dead (and a healthy benchpass gate
    read as a failure). Append the system sbin dirs so they resolve, never
    shadowing anything already on PATH. The root daemon already gets them from
    systemd's PATH; this is for the interactive / non-root CLI."""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for d in ("/usr/sbin", "/sbin"):
        if d not in parts:
            parts.append(d)
    os.environ["PATH"] = os.pathsep.join(parts)


def main(argv: Optional[List[str]] = None) -> int:
    _ensure_tool_path()
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(e)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
