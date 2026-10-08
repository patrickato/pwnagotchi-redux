"""redux selftest — the on-device software battery for the hardware pass.

Runs everything that needs no physical trigger (the captap pipeline, crypto/vault,
config, store, detectors) and probes the environment (python, board, radios,
capture engine, CSI), then returns a PASS / FAIL / SKIP report you can paste back.
The genuinely physical tests (handshake, live capture, fox-hunt) are listed as the
manual steps they are — see docs/HARDWARE_VALIDATION.md. Honest: a probe that can't
measure reads SKIP with the reason, never a fake PASS.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Check:
    name: str
    status: str   # PASS | FAIL | SKIP
    detail: str = ""


def run_selftest() -> List[Check]:
    checks: List[Check] = []

    def add(name: str, status: str, detail: str = "") -> None:
        checks.append(Check(name, status, detail))

    v = sys.version_info
    add("python", "PASS" if v >= (3, 11) else "FAIL", f"{v.major}.{v.minor}.{v.micro} (need >= 3.11)")

    try:
        import redux  # noqa: F401
        add("import redux", "PASS", "package imports")
    except Exception as e:  # nothing else will work
        add("import redux", "FAIL", repr(e))
        return checks

    try:
        from redux.detect.engine import DetectEngine
        add("detect engine", "PASS", f"{DetectEngine().detector_count} detectors registered")
    except Exception as e:
        add("detect engine", "FAIL", repr(e))

    try:
        import tomllib
        from redux.config import AugurConfig, template
        probs = AugurConfig.from_dict(tomllib.loads(template())).validate()
        add("config template+validate", "PASS" if not probs else "FAIL",
            "template parses + validates" if not probs else "; ".join(probs))
    except Exception as e:
        add("config template+validate", "FAIL", repr(e))

    try:
        from redux.vault import crypto_available, Vault
        if not crypto_available():
            add("vault (at-rest)", "SKIP", "crypto extra absent — pip install '.[crypto]' to enable")
        else:
            vlt = Vault("selftest-pass")
            ok = vlt.unseal(vlt.seal(b"secret-loot")) == b"secret-loot"
            add("vault (at-rest)", "PASS" if ok else "FAIL", "seal/open roundtrip")
    except Exception as e:
        add("vault (at-rest)", "FAIL", repr(e))

    # the P0 chain, end-to-end on synthetic bytes: parse -> bridge -> detectors
    try:
        from redux.captap import build_probe_req, build_deauth, capture_run
        from redux.detect.engine import DetectEngine
        ies = [(1, b"\x82\x84\x0b\x16"), (45, b"\x2d\x40\x00"), (127, b"\x00" * 6 + b"\x40")]
        src = []
        for mac in ("a2:11:11:11:11:11", "de:22:22:22:22:22"):
            for ssid in ("HomeLab-5G", "CoffeeShop"):
                src.append((build_probe_req(mac, ssid, ies=ies), 10.0))
        for i in range(22):
            src.append((build_deauth("de:ad:00:00:00:01", "ff:ff:ff:ff:ff:ff", "11:22:33:44:55:66"), 20.0 + i * 0.1))
        tap, alerts = capture_run(iter(src), engine=DetectEngine())
        reid = tap.link().summary()["reidentified"]
        fired = "deauth_flood" in {a.kind.value for a in alerts}
        add("captap pipeline", "PASS" if (reid >= 1 and fired) else "FAIL",
            f"cross-MAC re-id={reid}, deauth_flood={'fired' if fired else 'MISSING'}")
    except Exception as e:
        add("captap pipeline", "FAIL", repr(e))

    try:
        from redux.geo.db import SightingStore, Sighting
        st = SightingStore(":memory:")
        st.insert(Sighting(kind="wifi", mac="aa:bb:cc:00:00:01", channel=6, rssi=-55, ts=1.0, provenance="selftest"))
        st.prune(max_rows=10)
        add("sighting store", "PASS" if st.stats()["count"] == 1 else "FAIL", "insert / prune / stats")
    except Exception as e:
        add("sighting store", "FAIL", repr(e))

    # --- environment probes (real on-device signal; SKIP when unmeasurable) ---
    add("board", "SKIP", _board())
    add("radios", *_radios())
    add("capture engine", *_engine())
    add("csi (nexmon_csi)", "SKIP", "needs the CSI-enabled nexmon build; verify the UDP CSI stream on-device")

    return checks


def _board() -> str:
    try:
        with open("/proc/device-tree/model") as f:
            return f.read().strip("\x00").strip()
    except Exception:
        return f"{platform.system()} {platform.machine()}"


def _radios() -> Tuple[str, str]:
    if not shutil.which("iw"):
        return ("SKIP", "`iw` not found; run `iw dev` to list interfaces")
    try:
        out = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=10).stdout
        ifaces = [ln.split()[1] for ln in out.splitlines() if ln.strip().startswith("Interface")]
        return ("PASS" if ifaces else "SKIP", f"interfaces: {', '.join(ifaces) if ifaces else 'none'}")
    except Exception as e:
        return ("SKIP", f"iw dev failed: {e}")


def _engine() -> Tuple[str, str]:
    found = [t for t in ("bettercap", "angryoxide") if shutil.which(t)]
    return ("PASS" if found else "SKIP", f"present: {', '.join(found) if found else 'none (install bettercap)'}")
