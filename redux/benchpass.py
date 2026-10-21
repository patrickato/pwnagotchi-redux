"""Benchpass — the one lump-sum hardware validation session.

Every real-hardware gate (Phase-1 + platform + the subsystem battery) in one
ordered pass you run in a single sitting. `prep` runs anywhere with no hardware:
it reports which gates are software-ready, the gear you need to stage, and the
exact command sequence. `run` walks the gates on the Pi, recording PASS/FAIL/SKIP
to a resumable ledger so a long session can pause and continue, then emits one
consolidated report. The pass judgment is always the operator's — hardware gates
need a human to attest — so this module never fabricates a result or transmits;
it sequences, remembers, and reports.

The registry below is the authoritative machine-readable form of
`docs/HARDWARE_VALIDATION.md`. The orchestration is pure and takes an injected
executor, so ordering, resume, and reporting are testable with no hardware.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Optional

LEDGER_FORMAT = "redux-benchpass-ledger-v1"
TERMINAL = ("pass", "fail", "skip")
REQUIRED_BOARDS = ("pi4", "pi5")


@dataclass(frozen=True)
class Gate:
    id: str
    phase: str                 # phase1 | platform | subsystem
    title: str
    needs: tuple               # hardware / prerequisites to stage
    live: str                  # the command(s) to run on the Pi
    passes: str                # one-line pass criterion
    manual: bool = False       # requires a physical action (plug / walk / trigger)

    def as_dict(self) -> dict:
        return {"id": self.id, "phase": self.phase, "title": self.title,
                "needs": list(self.needs), "live": self.live,
                "passes": self.passes, "manual": self.manual}


# Ordered to match a real bench session: prove the image + radios first, then the
# headline hands-off pass, then platform surfaces, then the subsystem battery.
GATES: List[Gate] = [
    Gate("1.1", "phase1", "Image boots on the board",
         ("flashed SD card", "Pi 4", "Pi 5"),
         "flash the image; boot; confirm the redux service comes up",
         "boots to a running redux service on Pi 4 AND Pi 5", manual=True),
    Gate("1.2", "phase1", "Radio capability probe → Radio records",
         ("onboard radio", "one USB adapter"),
         "redux status --adapter   # iw phy/dev parsed into Radio records",
         "onboard + plugged adapter enumerated with correct bands/monitor/inject"),
    Gate("1.3", "phase1", "udev hotplug → roles re-apply, no manual steps",
         ("USB adapter to plug/unplug",),
         "plug the adapter, wait, unplug it; watch roles re-apply",
         "roles re-apply on plug/unplug with no manual airmon-ng/iw", manual=True),
    Gate("1.4", "phase1", "bettercap captures a real handshake",
         ("monitor adapter", "your own AP", "your own client"),
         "redux demo --live --intent hunt  # PLAN ONLY; real capture requires separately reviewed execution",
         "a real handshake is captured on your own AP, zero pwnagotchi in path", manual=True),
    Gate("1.5", "phase1", "intent switch re-arranges radios + repoints bettercap (TFT)",
         ("TFT panel", "2 radios"),
         "switch intent; watch roles re-arrange + capture repoint on the TFT",
         "intent change re-arranges radios and repoints capture, shown on the TFT", manual=True),
    Gate("1.6", "phase1", "THE hands-off demo (headline)",
         ("monitor adapter", "your own AP"),
         "redux demo --live --intent hunt  # PLAN ONLY; no real hands-off executor yet",
         "separately approved live executor must flash→boot→plug→arrange→capture; video plus real frames; plan-only command is insufficient", manual=True),

    Gate("2.0", "platform", "Dashboard serves real status",
         ("LAN/device browser",),
         "redux web   # open the printed URL; confirm REAL status, no fake telemetry",
         "dashboard shows real intent/capture_iface/sightings/reason", manual=True),
    Gate("2.1", "platform", "Packs enable/disable on-device",
         (),
         "redux packs   # list/enable/disable a pack",
         "a pack installs and removes cleanly on-device"),
    Gate("2.2", "platform", "Spatial sightings persist (if GPS attached)",
         ("GPS (optional)",),
         "confirm sightings persist to SQLite with real lat/lon (None when no fix)",
         "sightings persist with real coords; None, never fabricated, with no fix"),

    Gate("2b.1", "subsystem", "CSI motion/occupancy sensing",
         ("nexmon_csi firmware", "a real CSI capture"),
         "redux sense replay <real_csi.json> --calibrate 200",
         "parser validated on real nexmon_csi; calibrated baseline; motion/occupied read", manual=True),
    Gate("2b.2", "subsystem", "Capture engines (AngryOxide + bettercap)",
         ("your own AP",),
         "which angryoxide; redux capture --iface wlan1 --persona red plan; redux doctor",
         "AngryOxide selected when present; bettercap honest fallback; Doctor truthful"),
    Gate("2b.3", "subsystem", "WPA-Enterprise EAP capture",
         ("your own enterprise test SSID", "your own client"),
         "redux eap --persona red plan --ssid <OWN_ENTERPRISE> --authorized; redux eap parse <log>",
         "own client MSCHAPv2 captured; -m 5500 line cracks a known test password", manual=True),
    Gate("2b.4", "subsystem", "Mesh scope-sync over LoRa",
         ("2+ nodes with LoRa", "same swarm key"),
         "arm on node A; observe convergence on node B; a wrong-key delta is rejected",
         "arm converges over real LoRa; forged delta rejected; duty-cycle legal", manual=True),
    Gate("2b.5", "subsystem", "RSSI fox-hunt",
         ("your own AP", "GPS (optional)"),
         "redux hunt demo; then feed live RSSI for your own AP while walking",
         "toward=warmer, away=colder; centroid lands near the AP within error radius", manual=True),
    Gate("2b.6", "subsystem", "Fingerprint re-identification (live tap)",
         ("monitor adapter", "your own phone"),
         "redux captap live --iface wlan1mon --seconds 30",
         "your phone re-identified as ONE device across MAC randomization; no false merges", manual=True),
    Gate("2b.7", "subsystem", "Sentinel end-to-end (armed)",
         ("your own gear", "notifier (LoRa/log)"),
         "redux persona apply blue; redux sentinel …   # arm, walk away",
         "detector + CSI motion while armed both alert; disarmed motion suppressed; de-dup works", manual=True),
    Gate("2b.8", "subsystem", "Autonomous operator against own AP",
         ("your own armed AP",),
         "wire real executors; run a campaign against your own armed AP",
         "chain advances on success; unarmed target blocked at capture; report CLEAN", manual=True),
    Gate("2b.9", "subsystem", "Governor under real load",
         (),
         "vcgencmd measure_temp; vcgencmd get_throttled   # feed via observe_resources",
         "real temp/throttle shift FULL→…→SURVIVAL; cadence stretches; recovery held ~20s"),
    Gate("2b.10", "subsystem", "boot-POST / Doctor can't fake green",
         ("TFT panel (optional)",),
         "redux post --tft",
         "READY only when critical radio passes AND engine present; else honest DEGRADED/UNKNOWN"),
    Gate("2b.11", "subsystem", "Scope persistence across reboot",
         (),
         "redux scope add/arm-lab; reboot; redux scope list",
         "armed targets survive reboot; expired entry lapsed; firing gate refuses when empty", manual=True),
    Gate("2b.12", "subsystem", "Raw-frame detectors via live tap",
         ("monitor adapter", "your own AP+client"),
         "redux captap live --iface wlan1mon --seconds 30   # trigger a short own-gear deauth burst",
         "real deauth burst raises deauth_flood; quiet airspace stays silent", manual=True),
    Gate("2b.13", "subsystem", "First-run init, at-rest encryption, dashboard auth",
         (),
         "redux init --dir /etc/redux; redux config check; redux web --bind-scope lan",
         "sealed keystore (AUGURv1); config check clean; LAN dashboard 401 without token, serves with it"),
]


# Prerequisites to check on the Pi before starting the session, each mapped to the
# gates it unblocks. `key` is the fact looked up in the measured facts dict.
PREREQS = [
    ("radios", "≥1 Wi-Fi radio enumerated", ("1.2", "1.3", "1.5", "1.6")),
    ("monitor_radio", "≥1 monitor/injection adapter", ("1.4", "1.6", "2b.6", "2b.12")),
    ("capture_engine", "bettercap or AngryOxide installed", ("1.4", "1.6", "2b.2", "2b.8")),
    ("captures_mount", "/captures mounted read-write", ("1.4", "2b.2")),
    ("software_battery", "selftest software battery green", ("all",)),
    ("crypto", "at-rest encryption available (optional)", ("2b.13",)),
]


def preflight(facts: dict) -> dict:
    """Pure readiness board. `facts` maps each PREREQ key to a truthy/falsey
    measurement (and optional '<key>_detail'); returns {key: {ok, label, detail,
    unblocks}}. Hardware-absent facts read MISSING honestly — never faked."""
    board = {}
    for key, label, unblocks in PREREQS:
        ok = bool(facts.get(key))
        board[key] = {"ok": ok, "label": label,
                      "detail": facts.get(key + "_detail", "present" if ok else "missing"),
                      "unblocks": list(unblocks)}
    return board


def preflight_ready(board: dict) -> bool:
    """Ready enough to start when every non-optional prerequisite is OK. The
    crypto prereq is optional (its gate is honestly skipped without it)."""
    optional = {"crypto"}
    return all(v["ok"] for k, v in board.items() if k not in optional)


def gates_by_phase(gates: List[Gate] = None) -> dict:
    gates = GATES if gates is None else gates
    out: dict = {}
    for g in gates:
        out.setdefault(g.phase, []).append(g)
    return out


def gear_checklist(gates: List[Gate] = None) -> List[str]:
    """The union of everything to stage before the session, de-duplicated,
    preserving first-seen order."""
    gates = GATES if gates is None else gates
    seen: List[str] = []
    for g in gates:
        for need in g.needs:
            if need not in seen:
                seen.append(need)
    return seen


@dataclass
class Ledger:
    """Resumable per-gate progress, persisted as JSON so a long session can pause
    and continue. Only PASS/FAIL/SKIP are terminal; anything else re-runs. `board`
    names the board this ledger is for (e.g. pi4 / pi5), since a gate is only truly
    done when it passes on both."""
    path: Optional[str] = None
    started: float = 0.0
    gates: dict = field(default_factory=dict)   # id -> {status, detail, at}
    board: str = ""

    @classmethod
    def load(cls, path: str) -> "Ledger":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return cls(path=path, started=time.time())
        except (OSError, ValueError) as exc:
            raise ValueError("existing_benchpass_ledger_unreadable") from exc
        if (type(data) is not dict or data.get("format") != LEDGER_FORMAT
                or type(data.get("gates")) is not dict
                or type(data.get("board", "")) is not str):
            raise ValueError("existing_benchpass_ledger_invalid")
        records = data["gates"]
        if any(type(key) is not str or type(value) is not dict
               or value.get("status") not in ("pass", "fail", "skip", "pending")
               or type(value.get("detail", "")) is not str
               for key, value in records.items()):
            raise ValueError("existing_benchpass_ledger_invalid")
        return cls(path=path, started=data.get("started", time.time()),
                   gates=dict(records), board=data.get("board", ""))

    def status_of(self, gid: str) -> str:
        return self.gates.get(gid, {}).get("status", "pending")

    def done(self, gid: str) -> bool:
        return self.status_of(gid) in TERMINAL

    def record(self, gid: str, status: str, detail: str = "", *, now=None) -> None:
        if status not in (*TERMINAL, "pending"):
            raise ValueError("unrecognized_benchpass_status")
        self.gates[gid] = {"status": status, "detail": detail,
                           "at": (time.time() if now is None else now)}

    def save(self) -> None:
        if not self.path:
            return
        payload = {"format": LEDGER_FORMAT, "started": self.started,
                   "gates": self.gates, "board": self.board}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        import os
        os.replace(tmp, self.path)


@dataclass
class Report:
    rows: list = field(default_factory=list)   # {id, title, phase, status, detail, needs}
    counts: dict = field(default_factory=dict)
    complete: bool = False                     # every gate terminal
    clean: bool = False                        # all selected gates passed
    independently_qualified: bool = False      # requires separately verified hardware evidence

    def as_dict(self) -> dict:
        return {"rows": self.rows, "counts": self.counts,
                "complete": self.complete, "clean": self.clean,
                "independently_qualified": self.independently_qualified}


def run_benchpass(gates: List[Gate], executor: Callable[[Gate], tuple], ledger: Ledger,
                  *, resume: bool = False, only: Optional[list] = None) -> Report:
    """Walk gates in order. `executor(gate) -> (status, detail)` does the actual
    work (prompt the operator, run a command); it is injected so this is testable.
    With `resume`, gates already terminal in the ledger are not re-run. `only`
    limits the walk to those ids. The ledger is saved after each gate so a crash
    or Ctrl-C loses nothing."""
    chosen = [g for g in gates if (only is None or g.id in only)]
    for g in chosen:
        if resume and ledger.done(g.id):
            continue
        status, detail = executor(g)
        if status not in TERMINAL and status != "pending":
            status = "pending"
        ledger.record(g.id, status, detail)
        ledger.save()
    return build_report(gates, ledger, only=only)


def build_report(gates: List[Gate], ledger: Ledger, *, only: Optional[list] = None) -> Report:
    rep = Report()
    counts = {"pass": 0, "fail": 0, "skip": 0, "pending": 0}
    for g in gates:
        if only is not None and g.id not in only:
            continue
        st = ledger.status_of(g.id)
        counts[st] = counts.get(st, 0) + 1
        rep.rows.append({"id": g.id, "title": g.title, "phase": g.phase,
                         "status": st, "detail": ledger.gates.get(g.id, {}).get("detail", ""),
                         "needs": list(g.needs)})
    rep.counts = counts
    rep.complete = bool(rep.rows) and counts.get("pending", 0) == 0
    rep.clean = rep.complete and counts.get("pass", 0) == len(rep.rows)
    # A typed PASS is an operator observation, NOT independent hardware
    # evidence. Do not upgrade a human-editable ledger into release PASS.
    rep.independently_qualified = False
    return rep


def board_matrix(gates: List[Gate], ledgers: dict) -> list:
    """Cross-board view. `ledgers` maps board name -> Ledger. A gate is `done`
    only when EVERY provided board recorded `pass` for it (the project rule:
    done = both Pi 4 and Pi 5). Returns rows for rendering."""
    boards = list(ledgers.keys())
    required_present = set(REQUIRED_BOARDS).issubset(ledgers)
    rows = []
    for g in gates:
        per = {b: ledgers[b].status_of(g.id) for b in boards}
        done = required_present and all(per[b] == "pass" for b in REQUIRED_BOARDS)
        rows.append({"id": g.id, "title": g.title, "phase": g.phase,
                     "per_board": per, "done": done,
                     "independently_qualified": False})
    return rows


def all_boards_done(matrix: list) -> bool:
    """True when every gate is done on every board."""
    return bool(matrix) and all(r["done"] for r in matrix)


def report_markdown(report: "Report", *, board: str = "", image_sha: str = "",
                    model: str = "", now=None) -> str:
    """A dated results block ready to paste into docs/IMAGE_BUILD.md's
    hardware-gate record. Reflects only what the ledger actually holds."""
    import datetime
    ts = datetime.datetime.fromtimestamp(
        time.time() if now is None else now,
        datetime.timezone.utc).strftime("%Y-%m-%d")
    head = f"### {model or 'Pi'} — {ts} · benchpass"
    lines = [head, "",
             "**Operator-recorded results: not independently qualified; independent hardware acceptance NOT established.**",
             ""]
    if board:
        lines.append(f"- board: `{board}`")
    if image_sha:
        lines.append(f"- image SHA256: `{image_sha}`")
    c = report.counts
    lines.append(f"- result: {c.get('pass',0)} pass · {c.get('fail',0)} fail · "
                 f"{c.get('skip',0)} skip · {c.get('pending',0)} pending"
                 + ("  — ALL OPERATOR-RECORDED PASS (not release-qualified)"
                    if report.clean else ""))
    lines.append("")
    lines.append("| gate | status | note |")
    lines.append("|---|---|---|")
    for r in report.rows:
        note = (r.get("detail") or "").replace("|", "\\|")
        lines.append(f"| {r['id']} {r['title']} | {r['status']} | {note} |")
    return "\n".join(lines) + "\n"
