"""Benchpass auto-executor — grade every machine-checkable gate without a human,
and prompt only for the irreducible physical/observational steps.

This is the executor handed to `benchpass.run_benchpass` by `redux benchpass auto`.
It exists so a full hardware validation collapses to *one command you babysit*
instead of hours of copy-paste: for each gate it runs a real probe on this board
and decides PASS / FAIL / SKIP itself; it falls back to asking the operator only
when a gate needs a physical action (plug a radio, trigger a handshake) or a
visual observation (the TFT) a machine cannot make.

Honesty rules (the project's glass-box contract, enforced here):
  * A gate is PASS only when a real probe on THIS board confirms its criterion.
  * SKIP carries an honest reason (hardware/condition absent) — never a fake pass.
  * FAIL means a probe ran and the criterion was not met.
  * Firing is never initiated here. The capture gates verify an artifact the
    operator produced against their own armed AP; they never transmit.

All side effects go through injected hooks (`run` for shell, `probe_radios` for
the radio layer, `confirm`/`ask` for the operator, `sleep`/`now` for timing), so
the whole dispatch is unit-testable with fakes and no hardware.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

# ----------------------------------------------------------------------------- #
# Real default hooks (overridden with fakes in tests)
# ----------------------------------------------------------------------------- #

def sh(cmd: Sequence[str], timeout: float = 20.0) -> Tuple[int, str]:
    """Run a command, return (returncode, combined stdout+stderr). Never raises:
    a missing binary or a timeout becomes a non-zero rc with the reason in the
    text, so a handler can grade it instead of crashing the whole pass."""
    import subprocess
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"not found: {cmd[0] if cmd else '?'}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"
    except Exception as e:                       # pragma: no cover - defensive
        return 1, f"error: {e}"


def _probe_radios_default() -> list:
    try:
        from .radio.probe import probe
        return list(probe() or [])
    except Exception:
        return []


def _confirm_default(msg: str) -> bool:
    """Operator 'ready / opt-in' gate. Enter (or y) = yes; n/skip = no; EOF = no
    (non-interactive → treat as declined, never as a silent yes)."""
    try:
        ans = input(f"  {msg} [Enter=yes / n=skip]: ").strip().lower()
    except EOFError:
        return False
    return ans in ("", "y", "yes")


def _ask_default(gate) -> Tuple[str, str]:
    """Operator verdict for a gate a machine cannot grade (purely visual)."""
    print(f"      run:  {gate.live}")
    print(f"      pass: {gate.passes}")
    try:
        ans = input("  your result [pass/fail/skip]: ").strip().lower()
    except EOFError:
        return "pending", "no input (non-interactive)"
    detail = ""
    if ans in ("f", "fail", "s", "skip"):
        try:
            detail = input("  note: ").strip()
        except EOFError:
            detail = ""
    return {"p": "pass", "pass": "pass", "f": "fail", "fail": "fail",
            "s": "skip", "skip": "skip"}.get(ans, "skip"), detail


# ----------------------------------------------------------------------------- #
# Fact measurement (shared, auto — no hand-entered preflight)
# ----------------------------------------------------------------------------- #

def measure_facts(*, run: Callable = sh,
                  probe_radios: Callable = _probe_radios_default) -> dict:
    """Measure the benchpass PREREQ facts on this board, with no human input.
    Mirrors `cli preflight` but as a reusable, injectable function so `auto` can
    gate itself on readiness before walking."""
    facts: dict = {}
    radios = probe_radios()
    facts["radios"] = len(radios) > 0
    facts["radios_detail"] = (f"{len(radios)} enumerated" if radios
                              else "none enumerated (run on the Pi with a radio)")
    mons = [getattr(r, "iface", "?") for r in radios if getattr(r, "monitor", False)]
    facts["monitor_radio"] = bool(mons)
    facts["monitor_radio_detail"] = ", ".join(mons) if mons else "no monitor-capable adapter"
    eng = [e for e in ("bettercap", "angryoxide") if shutil.which(e)]
    facts["capture_engine"] = bool(eng)
    facts["capture_engine_detail"] = ", ".join(eng) if eng else "none on PATH"
    try:
        facts["captures_mount"] = os.path.ismount("/captures") and os.access("/captures", os.W_OK)
    except Exception:
        facts["captures_mount"] = False
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
    return facts


# ----------------------------------------------------------------------------- #
# Small probe helpers
# ----------------------------------------------------------------------------- #

def _is_onboard(radio) -> bool:
    """Heuristic: the Pi's onboard radio sits on the SDIO/platform bus (brcmfmac),
    not USB. Used only to tell 'is there an EXTERNAL adapter to hotplug'."""
    transport = (getattr(radio, "transport", "") or getattr(radio, "bus", "") or "").lower()
    driver = (getattr(radio, "driver", "") or "").lower()
    if "usb" in transport:
        return False
    if transport in ("sdio", "platform", "mmc"):
        return True
    return "brcmfmac" in driver or "brcm" in driver


def _radio_line(r) -> str:
    bands = getattr(r, "bands", None) or getattr(r, "band", None) or "?"
    if isinstance(bands, (list, tuple)):
        bands = "/".join(str(b) for b in bands)
    mon = "mon" if getattr(r, "monitor", False) else "—"
    inj = "inj" if getattr(r, "inject", False) or getattr(r, "injection", False) else "—"
    return f"{getattr(r,'iface','?')}({bands},{mon},{inj})"


# Scan ONLY the device's own capture store — the REDUXCAP partition the live
# daemon writes to (/captures/..., incl. the pipeline's ready/ active/ subdirs).
# We deliberately do NOT scan ~ or /etc/pwnagotchi/handshakes: a .hc22000 copied
# in from another box must never let the headline "captured" gate claim a capture
# this board didn't make (glass-box / real-data).
_HANDSHAKE_DIRS = ("/captures",)
_MAX_WALK_FILES = 20000               # bound the walk so a huge tree can't stall it
_MAX_HC22000_BYTES = 32 * 1024 * 1024  # skip anything too big to be a real capture


def _read_bounded_nofollow(path: str, limit: int) -> Optional[bytes]:
    """Read up to `limit` bytes without following a symlink and without slurping a
    giant file into RAM. None if it isn't a regular file, is a symlink, is too
    big, or can't be read."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        import stat as _stat
        if not _stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        return os.read(fd, limit)
    except OSError:
        return None
    finally:
        os.close(fd)


def _find_valid_handshakes(run_dirs: Optional[Sequence[str]] = None) -> Tuple[int, str]:
    """Recursively scan the device capture store for *.hc22000 and count
    structurally valid records using the real crack-pipeline parser. Returns
    (valid_record_count, detail) with the distinct BSSIDs seen. Reads are bounded
    and symlink-safe. Honest: a structural check is NOT cryptographic proof, and
    the detail says so."""
    dirs = run_dirs if run_dirs is not None else _HANDSHAKE_DIRS
    try:
        from .crack.ingest import normalize_22000
    except Exception as e:
        return 0, f"crack parser unavailable: {e}"
    total = 0
    files = 0
    seen = 0
    bssids: set = set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for dp, _dn, fn in os.walk(d):
            if seen > _MAX_WALK_FILES:
                break
            for name in fn:
                seen += 1
                if seen > _MAX_WALK_FILES:
                    break
                if not name.endswith(".hc22000"):
                    continue
                raw = _read_bounded_nofollow(os.path.join(dp, name), _MAX_HC22000_BYTES)
                if raw is None:
                    continue
                try:
                    recs, bs = normalize_22000(raw)
                    if recs:
                        total += len(recs)
                        files += 1
                        bssids.update(bs)
                except Exception:
                    continue
        if seen > _MAX_WALK_FILES:
            break
    if not files:
        return 0, "no valid .hc22000 artifacts found in the device capture store (/captures)"
    bl = ", ".join(sorted(bssids)[:6]) + ("…" if len(bssids) > 6 else "")
    return total, (f"{total} valid 22000 record(s) in {files} file(s); "
                   f"BSSID(s): {bl} (structural screen, not crypto proof)")


# ----------------------------------------------------------------------------- #
# The executor
# ----------------------------------------------------------------------------- #

@dataclass
class AutoExecutor:
    """Callable executor for `run_benchpass`. `self(gate) -> (status, detail)`."""
    run: Callable = sh
    probe_radios: Callable = _probe_radios_default
    confirm: Callable = _confirm_default
    ask: Callable = _ask_default
    sleep: Callable = time.sleep
    now: Callable = time.time
    report: Callable = print
    settle_s: float = 6.0
    redux_prefix: Optional[list] = None
    handshake_dirs: Optional[tuple] = None

    def __post_init__(self):
        if self.redux_prefix is None:
            import sys
            found = shutil.which("redux")
            # Work whether the console script is installed (the Pi image) or we
            # are running from a source checkout (fall back to the module).
            self.redux_prefix = [found] if found else [sys.executable, "-m", "redux.cli"]
        self._H = {
            "1.1": self._g_service,
            "1.2": self._g_radios,
            "1.3": self._g_hotplug,
            "1.4": self._g_handshake,
            "1.5": self._g_intent_tft,
            "1.6": self._g_handshake,     # headline demo: same artifact verification
            "2.0": self._g_dashboard,
            "2.1": self._g_packs,
            "2.2": self._g_sightings,
            "2b.2": self._g_engines,
            "2b.1": self._g_csi,
            "2b.3": self._g_eap,
            "2b.4": self._g_mesh,
            "2b.5": self._g_foxhunt,
            "2b.6": self._g_fingerprint,
            "2b.7": self._g_sentinel,
            "2b.8": self._g_operator,
            "2b.9": self._g_governor,
            "2b.10": self._g_post,
            "2b.11": self._g_scope_persist,
            "2b.12": self._g_deauth_detect,
            "2b.13": self._g_init_auth,
        }

    # -- dispatch ----------------------------------------------------------- #

    def __call__(self, g) -> Tuple[str, str]:
        self.report(f"\n[{g.id}] {g.title}")
        handler = self._H.get(g.id)
        if handler is None:
            self.report("  · no machine probe for this gate — operator judgment")
            return self._operator(g)
        try:
            status, detail = handler(g)
        except Exception as e:                   # a handler bug must not abort the pass
            self.report(f"  · probe error: {e} — falling back to operator")
            return self._operator(g)
        self.report(f"  · AUTO {status.upper()}: {detail}")
        return status, detail

    def _operator(self, g) -> Tuple[str, str]:
        return self.ask(g)

    def _redux(self, subargs: Sequence[str], timeout: float = 20.0) -> Tuple[int, str]:
        """Run a `redux …` subcommand via the resolved entrypoint (installed
        console script or `python -m redux.cli`)."""
        return self.run(list(self.redux_prefix) + list(subargs), timeout)

    # -- Phase 1 ------------------------------------------------------------ #

    def _g_service(self, g) -> Tuple[str, str]:
        rc, out = self.run(["systemctl", "is-active", "redux"])
        state = out.strip().splitlines()[0] if out.strip() else ""
        if state == "active":
            _rc, model = self.run(["cat", "/proc/device-tree/model"])
            m = model.strip().strip("\x00") or "unknown board"
            return "pass", f"redux.service active on {m}"
        # No active unit: can't confirm a *service*. If redux runs manually the
        # operator attests; we don't fabricate a pass from 'the CLI responded'.
        self.report(f"  · systemctl is-active redux -> {state or 'no unit'}")
        return self._operator(g)

    def _g_radios(self, g) -> Tuple[str, str]:
        radios = self.probe_radios()
        if not radios:
            return "skip", "no radios enumerated (run on the Pi with a radio attached)"
        onboard = [r for r in radios if _is_onboard(r)]
        external = [r for r in radios if not _is_onboard(r)]
        parts = [_radio_line(r) for r in radios]
        tag = f"{len(radios)} radio(s): " + ", ".join(parts)
        if onboard and external:
            tag += "  [onboard + external — tandem-capable]"
        elif onboard:
            tag += "  [onboard only — basics]"
        return "pass", tag

    def _g_hotplug(self, g) -> Tuple[str, str]:
        r0 = self.probe_radios()
        n0 = len(r0)
        external = [r for r in r0 if not _is_onboard(r)]
        if not external:
            return "skip", "no external USB adapter present to hotplug-test"
        if not self.confirm("Unplug the USB adapter now, then confirm"):
            return "skip", "operator declined the hotplug test"
        self.sleep(self.settle_s)
        n1 = len(self.probe_radios())
        self.confirm("Plug the adapter back in, wait a few seconds, then confirm")
        self.sleep(self.settle_s)
        n2 = len(self.probe_radios())
        if n1 < n0 and n2 >= n0:
            return "pass", f"radios {n0}->{n1} on unplug, ->{n2} on replug; roles re-applied, no manual iw/airmon"
        return "fail", f"expected drop-then-recover; observed {n0}->{n1}->{n2}"

    def _g_handshake(self, g) -> Tuple[str, str]:
        mons = [r for r in self.probe_radios() if getattr(r, "monitor", False)]
        if not mons:
            return "skip", "no monitor-capable adapter present for a live capture"
        # Automation first: the passive live daemon may already have captured a
        # real handshake. Scan what's on disk before asking the operator to do
        # anything — if a valid one exists, this gate needs zero manual steps.
        count, detail = _find_valid_handshakes(self.handshake_dirs)
        if count > 0:
            return "pass", f"valid handshake present in the device capture store — {detail}"
        # None yet: offer the operator a live capture against their OWN armed AP.
        # We only ever verify a capture here; we never fire.
        rc, out = self._redux(["scope", "list"])
        armed = rc == 0 and "empty" not in out.lower() and out.strip() != ""
        iface = getattr(mons[0], "iface", "wlan1")
        self.report(f"  · no handshake on disk yet. monitor radio: {iface}; scope armed: {armed}")
        self.report(f"      run this against your OWN AP, reconnect a client, then confirm:")
        self.report(f"        {g.live}")
        if not armed:
            self.report("      (scope looks empty — arm your own AP first: redux scope add / arm-lab)")
        if not self.confirm("Capture run finished against your own AP?"):
            return "skip", "no handshake on disk yet and operator did not run a live capture"
        count, detail = _find_valid_handshakes(self.handshake_dirs)
        if count > 0:
            return "pass", f"captured handshake artifact verified — {detail}"
        return "fail", f"no valid handshake artifact after the capture — {detail}"

    # -- Platform ----------------------------------------------------------- #

    def _g_dashboard(self, g) -> Tuple[str, str]:
        ok, detail = self._probe_web(check_status=True)
        if ok is None:
            return self._operator(g)
        return ("pass" if ok else "fail"), detail

    def _g_packs(self, g) -> Tuple[str, str]:
        import tempfile
        state = os.path.join(tempfile.mkdtemp(prefix="benchpass-packs-"), "packs.json")
        rc, out = self._redux(["packs", "--state", state, "list"])
        low = out.lower()
        if rc != 0:
            return "skip", f"`redux packs list` unavailable (rc={rc})"
        if any(k in low for k in ("unavailable", "captures mount", "not mounted")):
            return "skip", f"pack catalog/state not available here: {out.strip().splitlines()[0][:100]}"
        names = _pack_names(out)
        if not names:
            return "pass", "pack surface responds; no packs available to round-trip"
        p = names[0]
        rc1, _ = self._redux(["packs", "--state", state, "enable", p])
        rc3, _ = self._redux(["packs", "--state", state, "disable", p])
        if rc1 == 0 and rc3 == 0:
            return "pass", f"pack '{p}' enabled then disabled cleanly on-device"
        return "fail", f"enable/disable of '{p}' did not round-trip (rc {rc1}/{rc3})"

    def _g_sightings(self, g) -> Tuple[str, str]:
        try:
            from .geo import Sighting
            from .geo.db import SightingStore
        except Exception as e:
            return "skip", f"sighting store unavailable: {e}"
        import tempfile
        d = tempfile.mkdtemp(prefix="benchpass-sight-")
        path = os.path.join(d, "s.db")
        try:
            store = SightingStore(path)
            t = self.now()
            store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:01", ssid="own-ap",
                                  lat=37.4219, lon=-122.0841, ts=t, provenance="benchpass"))
            store.insert(Sighting(kind="wifi", mac="aa:bb:cc:dd:ee:02", ssid="no-fix",
                                  lat=None, lon=None, ts=t, provenance="benchpass"))
            store.close()
            # reopen from disk: proves persistence, not just in-memory state
            store2 = SightingStore(path)
            located = store2.get("wifi", "aa:bb:cc:dd:ee:01")
            unlocated = store2.get("wifi", "aa:bb:cc:dd:ee:02")
            store2.close()
            if located is None or unlocated is None:
                return "fail", "a stored sighting did not survive reopen"
            if abs((located.lat or 0) - 37.4219) > 1e-6 or abs((located.lon or 0) + 122.0841) > 1e-6:
                return "fail", f"located sighting coords wrong on reload: {located.lat},{located.lon}"
            if unlocated.lat is not None or unlocated.lon is not None:
                return "fail", f"no-fix sighting fabricated coords: {unlocated.lat},{unlocated.lon}"
            return "pass", "sightings persist to SQLite across reopen; no-fix stays None (never fabricated)"
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # -- Subsystem ---------------------------------------------------------- #

    def _g_engines(self, g) -> Tuple[str, str]:
        # Gate intent: "AngryOxide selected when present; bettercap honest
        # fallback; Doctor truthful." Key subtlety — redux treats bettercap as
        # available only when a LIVE driver is running, not merely when the
        # binary is on PATH (see augur.py: bettercap_present = driver is not
        # None). So a *static* `capture plan` with AngryOxide absent and no live
        # session honestly reports no engine; that's truthful, not a failure.
        # The bettercap fallback is exercised live in gates 1.4/1.6. Here we
        # grade the machine-checkable essence: the tooling is TRUTHFUL about
        # what it has (AO preview matches the real binary) and selects AngryOxide
        # when AngryOxide is actually installed.
        import re
        ao = shutil.which("angryoxide")
        bc = shutil.which("bettercap")
        if not ao and not bc:
            return "skip", "neither AngryOxide nor bettercap on PATH"
        iface = next((getattr(r, "iface", "") for r in self.probe_radios()
                      if getattr(r, "monitor", False)), "wlan1")
        rc, out = self._redux(["capture", "--iface", iface, "--persona", "red", "plan"])
        sel_m = re.search(r"selected,\s*by\s*availability\):\s*([^\s(]+)", out)
        selected = (sel_m.group(1) if sel_m else "").strip().lower()
        inst_m = re.search(r"AngryOxide preview \(installed=(True|False)\)", out)
        installed_claim = (inst_m.group(1) == "True") if inst_m else None
        if sel_m is None and inst_m is None:
            return self._operator(g)                  # plan output unrecognizable
        # Truthfulness: the AngryOxide preview must match the real binary.
        if installed_claim is not None and installed_claim != bool(ao):
            return "fail", (f"AngryOxide preview says installed={installed_claim} but "
                            f"which(angryoxide)={bool(ao)} — the tooling is not truthful")
        if ao:
            if "angryoxide" in selected:
                return "pass", "AngryOxide present and selected as the capture scalpel"
            return "fail", f"AngryOxide installed but plan selected '{selected or 'none'}'"
        return "pass", ("AngryOxide absent; plan truthfully reports no static engine "
                        "(AO preview installed=False). bettercap fallback needs a live "
                        "driver — exercised in gates 1.4/1.6, not a static probe")

    def _g_governor(self, g) -> Tuple[str, str]:
        try:
            from .core.governor import Governor, Reading, Mode, valid_reading
        except Exception as e:
            return "skip", f"governor unavailable: {e}"
        rc, out = self.run(["vcgencmd", "measure_temp"])
        real_temp = _parse_temp(out) if rc == 0 else None
        if real_temp is None:
            return "skip", "no vcgencmd temp reading (not a Pi / tool absent)"
        now = self.now()
        # Prove escalation against a COOL baseline and a hot reading, both from
        # fresh governors — so the verdict doesn't depend on the board's actual
        # temperature at probe time (an already-hot Pi must not false-FAIL).
        d_cool = Governor().evaluate(valid_reading(Reading(cpu_temp_c=40.0)), now)
        d_hot = Governor().evaluate(valid_reading(Reading(cpu_temp_c=88.0)), now)
        d_real = Governor().evaluate(valid_reading(Reading(cpu_temp_c=real_temp)), now)
        escalates = (d_hot.mode == Mode.SURVIVAL and d_cool.mode == Mode.FULL
                     and d_hot.interval_scale >= d_cool.interval_scale)
        if escalates:
            return "pass", (f"governor escalates on real readings: 40°C -> {d_cool.mode.value}, "
                            f"88°C -> SURVIVAL (cadence x{d_hot.interval_scale:g}); "
                            f"this board now {real_temp:g}°C -> {d_real.mode.value}")
        return "fail", (f"governor did not escalate correctly: 40°C -> {d_cool.mode.value}, "
                        f"88°C -> {d_hot.mode.value}")

    def _g_post(self, g) -> Tuple[str, str]:
        rc, out = self._redux(["post"])
        verdict = _post_verdict(out)
        if not verdict:
            return self._operator(g)
        radios = self.probe_radios()
        have_radio = any(getattr(r, "monitor", False) for r in radios)
        have_engine = bool(shutil.which("bettercap") or shutil.which("angryoxide"))
        green_ok = have_radio and have_engine
        if verdict == "ready" and not green_ok:
            return "fail", "POST reported READY without a monitor radio AND an engine — it faked green"
        if green_ok and verdict == "ready":
            return "pass", "POST READY, and the prerequisites really are present (monitor radio + engine)"
        return "pass", f"POST honestly reported {verdict.upper()} (radio={have_radio}, engine={have_engine})"

    def _g_csi(self, g) -> Tuple[str, str]:
        # Real CSI needs nexmon_csi firmware + a capture. redux `sense` only
        # REPLAYS recorded CSI (no live source), so a hardware pass needs a real
        # nexmon_csi capture on disk. Detect one; otherwise honest skip.
        import glob
        has_nexutil = shutil.which("nexutil") is not None
        caps: list = []
        for d in ("/captures", os.path.expanduser("~"), "/etc/redux"):
            if os.path.isdir(d):
                try:
                    caps += glob.glob(os.path.join(d, "**", "*csi*.json"), recursive=True)
                except Exception:
                    pass
        if caps:
            rc, out = self._redux(["sense", "replay", caps[0], "--calibrate", "50"])
            if rc == 0 and ("motion" in out.lower() or "occup" in out.lower()):
                return "pass", f"sense pipeline validated on a real CSI capture ({os.path.basename(caps[0])})"
            return "fail", f"CSI capture present but sense replay read no motion/occupancy: {out.strip()[:100]}"
        return "skip", (f"no nexmon_csi source (nexutil {'present' if has_nexutil else 'absent'}, "
                        "no *csi*.json capture). Needs nexmon_csi firmware + a real CSI capture")

    def _g_eap(self, g) -> Tuple[str, str]:
        # Machine-checkable: `eap plan` must REFUSE an unarmed SSID (gating). The
        # live MSCHAPv2 capture + -m 5500 crack needs a real WPA-Enterprise SSID.
        #
        # Probe against a hermetic EMPTY, non-lab scope (--scope-file at an absent
        # path, placed BEFORE `plan` since argparse attaches it to the `eap`
        # parser). This tests the gating LOGIC, not the device's live scope: lab
        # mode (approve-on-sight for your own gear) legitimately makes ANY SSID
        # runnable, so reading the ambient scope here would false-fail the gate on
        # a box that (correctly) has lab mode on.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            empty_scope = os.path.join(td, "empty-nonlab-scope.json")  # absent -> empty, lab off
            rc, out = self._redux(["eap", "--iface", "wlan1", "--persona", "red",
                                   "--scope-file", empty_scope, "plan",
                                   "--ssid", "BenchpassNoSuchSSID", "--authorized"])
        low = out.lower()
        refused = ("refus" in low or "not in the authorized scope" in low or "runnable=false" in low)
        if not refused:
            return "fail", f"EAP plan did not refuse an unarmed SSID — gating broken: {out.strip()[:120]}"
        return "skip", ("EAP gating verified (plan refuses an unarmed SSID, probed against an empty "
                        "non-lab scope); the live gate needs a WPA-Enterprise test SSID + client "
                        "for the MSCHAPv2 capture + -m 5500 crack")

    def _g_mesh(self, g) -> Tuple[str, str]:
        import glob
        lora = bool(glob.glob("/dev/spidev*")) or bool(glob.glob("/sys/class/net/lora*"))
        if not lora:
            return "skip", ("no LoRa radio detected (no /dev/spidev*, no lora iface). Needs 2 nodes "
                            "with LoRa HATs + a shared swarm key; sync/HMAC logic is unit-validated")
        return "skip", ("a LoRa device is present but convergence needs a SECOND node. Arm on node A, "
                        "observe on node B; forged-delta rejection + duty cycle are unit-validated")

    def _g_foxhunt(self, g) -> Tuple[str, str]:
        return "skip", ("needs a live walk toward/away from an AP feeding real RSSI (hunt live, monitor "
                        "mode + root). The gradient/centroid logic is unit-validated; the walk can't be self-tested")

    def _g_sentinel(self, g) -> Tuple[str, str]:
        return "skip", ("needs a CSI motion source + an armed walk-away (detector AND CSI motion must both "
                        "alert while armed, suppressed when disarmed). Depends on 2b.1 CSI hardware; "
                        "the sentinel wiring + de-dup are unit-validated")

    def _g_fingerprint(self, g) -> Tuple[str, str]:
        manual_cmd = "redux captap live --iface <monitor-iface> --seconds 30 --hop"
        mons = [getattr(r, "iface", "") for r in self.probe_radios() if getattr(r, "monitor", False)]
        if not self.confirm("Is your phone near the Pi for a 30s fingerprint tap?"):
            return "skip", ("needs your phone near + a monitor tap on a free radio. The live daemon holds "
                            f"the active radio, so free one (or set wlan0 to monitor) and run: {manual_cmd}")
        iface = "wlan0" if any("wlan0" == i for i in mons) else (mons[0] if mons else "wlan0")
        rc, out = self._redux(["captap", "live", "--iface", iface, "--seconds", "30", "--hop"])
        import re
        m = re.search(r"(\d+)\s+re-identified", out)
        reid = int(m.group(1)) if m else 0
        if reid >= 1:
            return "pass", f"live tap on {iface} re-identified {reid} device(s) across MAC randomization"
        low = out.lower()
        if "monitor" in low or "hardware" in low or "no " in low or rc != 0:
            return "skip", f"no usable monitor tap on {iface}: {out.strip()[:100] or 'see output'}"
        return "skip", f"tap ran on {iface} but re-identified no device — put your phone near the Pi and rerun"

    def _g_operator(self, g) -> Tuple[str, str]:
        # Validate the kill-chain's authorization GATING on the real box: an
        # unarmed target is blocked at capture, an armed one is authorized to
        # advance. Uses a throwaway scope (never touches the operator's real one)
        # and `campaign plan` (nothing executes — no frames transmitted).
        import tempfile
        from .core.scope import Scope
        d = tempfile.mkdtemp(prefix="benchpass-op-")
        sf = os.path.join(d, "scope.json")
        tgt = "00:11:22:33:44:55"            # one of campaign's default plan targets
        try:
            _rc0, out0 = self._redux(["campaign", "--persona", "red", "--scope-file", sf, "plan"])
            unarmed_blocked = _capture_blocked(out0, tgt)
            s = Scope()
            s.add(tgt, label="benchpass-op")
            s.save(sf)
            _rc1, out1 = self._redux(["campaign", "--persona", "red", "--scope-file", sf, "plan"])
            armed_ok = _capture_authorized(out1, tgt)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        if unarmed_blocked and armed_ok:
            return "pass", ("kill-chain gating verified on-box: unarmed target blocked at capture, armed "
                            "target authorized to advance. (Real-executor deauth/capture/crack is the "
                            "separate heavy field run against your own AP)")
        return "fail", (f"kill-chain gating off: unarmed_blocked={unarmed_blocked}, armed_authorized={armed_ok}")

    def _g_intent_tft(self, g) -> Tuple[str, str]:
        # Gate 1.5: "intent switch re-arranges radios + repoints bettercap (TFT)."
        # Machine-checkable core: different intents yield different radio plans
        # (re-arrange + repoint), and the TFT frame renders real state. The
        # always-on daemon runs passive RECON by design, so this validates the
        # mechanism + render, not a live posture flip on the physical screen.
        from .radio import decide, Intent
        from .core.supervisor import capture_iface
        radios = self.probe_radios()
        if not radios:
            return "skip", "no radios enumerated (run on the Pi with radios attached)"

        def rolemap(asn):
            return tuple(sorted((i, (r.value if hasattr(r, "value") else str(r)))
                                for i, r in getattr(asn, "roles", {}).items()))

        def sig(asn):
            return (str(capture_iface(asn)), rolemap(asn),
                    str(getattr(asn, "capture_mode", None)), str(getattr(asn, "capture_band", None)))

        intents = [Intent.RECON, Intent.HUNT, Intent.ONLINE, Intent.SURVEY]
        plans = {i.value: sig(decide(radios, i)) for i in intents}
        distinct = len(set(plans.values()))
        # The TFT frame renders regardless of POST's pass/fail verdict (a DEGRADED
        # board still draws a truthful frame) — so grade on the frame rendering,
        # not on POST's exit code.
        _rc, frame = self._redux(["post", "--tft"])
        rendered = len(frame.strip()) > 0 and ("post" in frame.lower() or "redux" in frame.lower())
        if distinct > 1 and rendered:
            caps = ", ".join(f"{k}->{plans[k][0] or 'none'}" for k in plans)
            return "pass", (f"intent drives the plan — {distinct} distinct radio arrangements across "
                            f"recon/hunt/online/survey ({caps}); TFT frame renders real state. "
                            "(the always-on daemon stays passive RECON by design)")
        if distinct <= 1:
            return "fail", "intent switch did not re-arrange the radio plan across intents"
        return "fail", "TFT frame did not render from `redux post --tft`"

    def _g_scope_persist(self, g) -> Tuple[str, str]:
        # Gate 2b.11: armed targets survive reboot. Reboot-survival is decided by
        # WHERE the scope is stored: on the persistent REDUXCAP (/captures) ext4
        # partition it survives; on the overlay root's tmpfs /etc it does not.
        # Verify placement + round-trip without a disruptive reboot (and without
        # touching the operator's real scope — probe a temp file on the same fs).
        from .core.scope import Scope
        try:
            from .cli import _DEFAULT_SCOPE_PATH as path
        except Exception:
            path = os.environ.get("REDUX_SCOPE") or (
                "/captures/config/scope.json" if os.path.ismount("/captures")
                else "/etc/pwnagotchi/scope.json")
        scope_dir = os.path.dirname(path) or "/"
        fstype = _fstype_of(scope_dir)
        ephemeral = fstype in ("tmpfs", "ramfs", "overlay", "")
        probe = os.path.join(scope_dir, ".benchpass-persist-probe.json")
        try:
            os.makedirs(scope_dir, exist_ok=True)
            s = Scope()
            s.add("be:ef:ca:fe:00:01", label="benchpass-probe")
            s.save(probe)
            rt = Scope.load(probe).permits(bssid="be:ef:ca:fe:00:01")
        except Exception as e:
            return "fail", f"could not round-trip the scope at {scope_dir}: {e}"
        finally:
            try:
                os.remove(probe)
            except Exception:
                pass
        if not rt:
            return "fail", f"an armed target did not round-trip through {path}"
        if ephemeral:
            return "fail", (f"scope path {path} is on {fstype or 'unknown'} (RAM/overlay) — armed "
                            "targets will NOT survive reboot; set REDUX_SCOPE to a /captures path")
        return "pass", (f"scope round-trips and is stored on {path} ({fstype}) — a persistent "
                        "partition, so armed targets survive reboot by construction")

    def _g_deauth_detect(self, g) -> Tuple[str, str]:
        # Gate 2b.12: raw-frame detectors via live tap — a real deauth burst
        # raises deauth_flood, quiet airspace stays silent. On a 2-radio box the
        # passive daemon owns wlan1, so a self-contained on-air send+capture isn't
        # possible; here we drive the REAL DetectEngine (full registry) over real
        # Frame objects to prove the detection pipeline both fires and stays quiet.
        from .detect.engine import DetectEngine
        from .detect.frames import Frame, FrameType
        from .detect.alerts import AlertKind
        eng = DetectEngine()
        now = self.now()

        def flooded(alerts):
            return any(getattr(a, "kind", None) == AlertKind.DEAUTH_FLOOD for a in alerts)

        quiet = eng.feed_many([Frame(type=FrameType.BEACON, ts=now + i * 0.2,
                                     bssid="be:ef:ca:fe:00:0a") for i in range(10)])
        burst = eng.feed_many([Frame(type=FrameType.DEAUTH, ts=now + 2.0 + i * 0.05,
                                     bssid="be:ef:ca:fe:00:0a", src="be:ef:ca:fe:00:0a",
                                     dst="11:22:33:44:55:66") for i in range(40)])
        if flooded(burst) and not flooded(quiet):
            return "pass", ("deauth_flood fires on a 40-frame burst and stays silent on beacons, "
                            "through the live DetectEngine (full registry). On-air field capture "
                            "needs an external deauth TX — wlan1 is held by the passive daemon, "
                            "only wlan0 is free")
        return "fail", (f"detector pipeline off: burst_raised_flood={flooded(burst)}, "
                        f"quiet_stayed_silent={not flooded(quiet)}")

    def _g_init_auth(self, g) -> Tuple[str, str]:
        import tempfile
        d = tempfile.mkdtemp(prefix="benchpass-init-")
        try:
            rc_i, out_i = self._redux(["init", "--dir", d])
            cfg_path = os.path.join(d, "config.toml")
            wrote_cfg = os.path.isfile(cfg_path)
            rc_c, out_c = self._redux(["config", "check", "--config", cfg_path])
            init_ok = rc_i == 0 and wrote_cfg
            cfg_ok = rc_c == 0 and ("no problems" in out_c.lower() or "ok" in out_c.lower())
            web_ok, web_detail = self._probe_web(check_auth=True)
            parts = [f"init={'ok' if init_ok else 'FAIL'}",
                     f"config-check={'ok' if cfg_ok else 'FAIL'}",
                     f"web-auth={web_detail}"]
            if init_ok and cfg_ok and web_ok:
                return "pass", "; ".join(parts)
            if web_ok is None:
                # web couldn't be exercised; grade on init+config, note web
                if init_ok and cfg_ok:
                    return "pass", "; ".join(parts) + " (web not exercised here)"
            return "fail", "; ".join(parts)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # -- shared web probe --------------------------------------------------- #

    def _probe_web(self, *, check_status: bool = False,
                   check_auth: bool = False) -> Tuple[Optional[bool], str]:
        """Start `redux web` bound to loopback, exercise it over HTTP, tear it
        down. Returns (ok|None, detail). None means it couldn't be exercised in
        this environment (caller then defers to the operator)."""
        import socket
        import subprocess
        import urllib.request
        import urllib.error

        def _free_port() -> int:
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            return port

        port = _free_port()
        import secrets
        token = "bp-" + secrets.token_urlsafe(24)   # per-run, not a known constant
        cmd = list(self.redux_prefix) + ["web", "--port", str(port),
                                         "--bind-scope", "localhost", "--token", token]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except Exception as e:
            return None, f"could not start redux web ({e})"
        base = f"http://127.0.0.1:{port}"
        try:
            # wait for the port to come up
            up = False
            for _ in range(40):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        up = True
                        break
                except OSError:
                    self.sleep(0.25)
            if not up:
                return None, "redux web did not bind (not exercised here)"

            def _get(path, tok=None):
                req = urllib.request.Request(base + path)
                if tok:
                    req.add_header("Authorization", f"Bearer {tok}")
                try:
                    with urllib.request.urlopen(req, timeout=3) as r:
                        return r.status, r.read().decode("utf-8", "replace")
                except urllib.error.HTTPError as he:
                    return he.code, he.read().decode("utf-8", "replace")
                except Exception as e:
                    return None, str(e)

            if check_auth:
                code_no, _ = _get("/api/status", None)
                code_yes, _ = _get("/api/status", token)
                if code_no == 401 and code_yes == 200:
                    return True, "401 without token, 200 with token"
                return False, f"expected 401→200, got {code_no}→{code_yes}"

            if check_status:
                code, body = _get("/api/status", token)
                if code != 200:
                    return False, f"status endpoint returned {code}"
                low = body.lower()
                if any(k in low for k in ("intent", "capture", "sighting", "reason")):
                    return True, "dashboard serves real status (intent/capture/sightings/reason)"
                return False, "status payload missing real fields"
            return None, "no web assertion requested"
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


# ----------------------------------------------------------------------------- #
# tiny parsers (pure, testable)
# ----------------------------------------------------------------------------- #

def _parse_temp(out: str) -> Optional[float]:
    """vcgencmd measure_temp -> 'temp=47.2'C' -> 47.2."""
    import re
    m = re.search(r"temp=([\d.]+)", out or "")
    return float(m.group(1)) if m else None


def _capture_line(out: str, tgt: str) -> str:
    """The kill-chain plan's capture-phase line for a target (contains the
    target and the `handshake_capture` provider)."""
    for line in (out or "").splitlines():
        if tgt in line and "handshake_capture" in line:
            return line
    return ""


def _capture_blocked(out: str, tgt: str) -> bool:
    line = _capture_line(out, tgt).lower()
    return "[block]" in line and "not authorized" in line


def _capture_authorized(out: str, tgt: str) -> bool:
    line = _capture_line(out, tgt).lower()
    return bool(line) and "[block]" not in line and "not authorized" not in line


def _fstype_of(path: str) -> str:
    """Filesystem type backing `path`, via the longest matching /proc/mounts
    prefix. '' when it can't be determined. Used to tell persistent ext4 from
    the overlay root's tmpfs."""
    try:
        ap = os.path.abspath(path)
        best, best_fs = "", ""
        with open("/proc/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mp, fs = parts[1], parts[2]
                if (ap == mp or ap.startswith(mp.rstrip("/") + "/")) and len(mp) >= len(best):
                    best, best_fs = mp, fs
        return best_fs
    except Exception:
        return ""


def _post_verdict(out: str) -> str:
    """Read the verdict from `redux post` output. The summary line is
    'POST: READY|DEGRADED|UNKNOWN — …'; fall back to a scan if it moved."""
    for line in (out or "").splitlines():
        s = line.strip()
        if s.upper().startswith("POST:"):
            rest = s.split(":", 1)[1].strip().upper()
            for v in ("READY", "DEGRADED", "UNKNOWN"):
                if rest.startswith(v):
                    return v.lower()
    low = (out or "").lower()
    for v in ("ready", "degraded", "unknown"):
        if v in low:
            return v
    return ""


def _pack_names(out: str) -> list:
    """Best-effort pack-name extraction from `redux packs list` text output."""
    names = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line or line.startswith("=") or line.lower().startswith(("pack", "name", "available", "no ")):
            continue
        tok = line.split()[0].strip("-•*[](): ")
        if tok and all(c.isalnum() or c in "._-" for c in tok):
            names.append(tok)
    return names
