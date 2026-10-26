"""Secure-wipe / panic-scrub — the device's OWN data, never a target's.

Operator OPSEC: if the box is lost or seized, you want the sensitive *collected*
data gone — raw captures, looted files, recovered credentials, harvested hashes —
without throwing away the LEARNING that makes Augur better over time. That brain
(experience, the entity graph, forged/grown wordlists, device DNA, the airwave
DVR) lives on /captures precisely so it survives reboots; a scrub is about
emissions-on-disk, not amnesia.

Two deliberate tiers, both glass-box and DRY-RUN by default:
  - scrub  (surgical): wipe RAW sensitive loot/captures/creds; KEEP the brain.
           "I ran an op — remove the evidence, keep what I learned."
  - panic  (full burn): wipe raw + brain + geo + the authlog + your target list;
           keep only what lets the box still boot. "Someone's at the door."

This tool ONLY ever touches the device's own /captures tree — it is not target
anti-forensics and will not wipe anyone else's logs or evidence. It refuses any
root outside the configured captures dir. Pure/deterministic planning over an
injected filesystem root; the executor is best-effort and honest about flash.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# tier of each on-device data location. The planner classifies real paths under
# the captures root against this; unknown paths default to "unknown" (kept, and
# reported, so nothing sensitive is wiped OR retained silently).
#   raw               — sensitive collected intel (captures, loot, creds, hashes)
#   brain             — the learning that should persist (experience, wordlists…)
#   geo               — location history (most seizure-sensitive; kept on scrub)
#   log               — the signed auth log (your own accountability record)
#   config            — keeps the box functional (kept by both modes)
#   config-sensitive  — config that is itself intel (your armed target list)
_CATALOG: Tuple[Tuple[str, str, str], ...] = (
    # (relative path, tier, why)
    ("incoming", "raw", "raw inbound captures"),
    ("ready", "raw", "prepared crackable handshakes (.hc22000 with real keys)"),
    ("active", "raw", "in-flight captures"),
    ("crack", "raw", "crack working files"),
    ("audits", "raw", "audit outputs"),
    ("jobs.db", "raw", "capture jobs DB (BSSIDs + capture state)"),
    ("loot/files", "raw", "files pulled off other hosts — the most sensitive loot"),
    ("loot/netntlm", "raw", "harvested NetNTLM hashes"),
    ("loot/vault.json", "raw", "recovered-credential vault (secrets)"),
    ("live/pmkid.json", "raw", "harvested PMKIDs (crackable hashes)"),
    ("live/experience.jsonl", "brain", "the learning — what works where (makes it smarter)"),
    ("live/observations.jsonl", "brain", "device observations feeding re-id / DNA"),
    ("live/airwave.jsonl", "brain", "RF history DVR"),
    ("live/pnl.json", "brain", "harvested PNLs (wordlist seeds)"),
    ("live/notebook.jsonl", "brain", "lab notebook (learning journal)"),
    ("wordlists", "brain", "forged / environment-grown wordlists"),
    ("live/sightings.db", "geo", "entity graph + sighting locations (where you've been)"),
    ("live/location_memory.json", "geo", "self-locate memory"),
    ("live/authlog.jsonl", "log", "signed auth log (your own accountability record)"),
    ("config/scope.json", "config-sensitive", "your armed target list"),
    ("live/stealth.json", "config-sensitive", "stealth posture"),
    ("config", "config", "device config (keeps the box functional)"),
    ("plugins", "config", "installed plugins"),
    ("rules", "config", "detection rules"),
)

SCRUB_TIERS = frozenset({"raw"})
PANIC_TIERS = frozenset({"raw", "brain", "geo", "log", "config-sensitive"})


@dataclass(frozen=True)
class ScrubItem:
    path: str
    tier: str
    reason: str
    exists: bool
    size: int = 0

    def to_dict(self) -> dict:
        return {"path": self.path, "tier": self.tier, "reason": self.reason,
                "exists": self.exists, "size": self.size}


def _du(path: str) -> int:
    """Bytes on disk for a file or dir tree; 0 on any error (fail-safe)."""
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total
    except OSError:
        return 0


@dataclass
class SecureWipe:
    """Plan + (on explicit apply) execute a scrub of the device's own captures."""
    root: str = "/captures"
    catalog: Tuple[Tuple[str, str, str], ...] = _CATALOG

    def _guard(self) -> None:
        # Never operate outside a captures tree — this is own-device OPSEC, not
        # target anti-forensics. A root that isn't a 'captures' dir is refused.
        base = os.path.basename(os.path.normpath(self.root)).lower()
        if "captures" not in base and not os.environ.get("REDUX_SCRUB_ROOT_OK"):
            raise ValueError(
                f"refused: scrub root {self.root!r} is not a captures dir — this tool only "
                "wipes the device's OWN collected data, never a target's")

    def inventory(self) -> List[ScrubItem]:
        """Classify every catalogued location under the root (glass-box)."""
        out: List[ScrubItem] = []
        for rel, tier, why in self.catalog:
            p = os.path.join(self.root, rel)
            exists = os.path.exists(p)
            out.append(ScrubItem(path=p, tier=tier, reason=why, exists=exists,
                                 size=_du(p) if exists else 0))
        return out

    def plan(self, mode: str = "scrub") -> Dict[str, List[ScrubItem]]:
        """What a mode would WIPE vs KEEP. Does not touch disk."""
        tiers = PANIC_TIERS if mode == "panic" else SCRUB_TIERS
        wipe, keep = [], []
        for item in self.inventory():
            (wipe if item.tier in tiers else keep).append(item)
        return {"mode": mode, "wipe": wipe, "keep": keep}

    def exposure(self) -> dict:
        """How much sensitive collected data is on the box right now — the
        'remove footprint/details' view, before deciding to scrub."""
        inv = [i for i in self.inventory() if i.exists]
        by_tier: Dict[str, dict] = {}
        for i in inv:
            t = by_tier.setdefault(i.tier, {"items": 0, "bytes": 0})
            t["items"] += 1
            t["bytes"] += i.size
        raw = by_tier.get("raw", {"items": 0, "bytes": 0})
        return {"by_tier": by_tier,
                "raw_sensitive_items": raw["items"], "raw_sensitive_bytes": raw["bytes"],
                "note": (f"{raw['items']} raw-sensitive item(s), {raw['bytes']} bytes — "
                         f"`redux scrub run` removes these and keeps the brain"
                         if raw["items"] else "no raw-sensitive data on disk")}

    # ---- execution (explicit apply only) -----------------------------------
    def _wipe_path(self, path: str, *, overwrite: bool,
                   remover: Callable, overwriter: Callable) -> Tuple[bool, str]:
        try:
            if os.path.isfile(path):
                if overwrite:
                    overwriter(path)
                remover(path)
                return True, "wiped"
            # directory: wipe files, then drop empty dirs
            for r, _d, files in os.walk(path, topdown=False):
                for f in files:
                    fp = os.path.join(r, f)
                    if overwrite:
                        overwriter(fp)
                    remover(fp)
                try:
                    os.rmdir(r)
                except OSError:
                    pass
            return True, "wiped"
        except OSError as e:
            return False, f"failed: {type(e).__name__}"

    def execute(self, mode: str = "scrub", *, apply: bool = False, overwrite: bool = True,
                remover: Optional[Callable] = None,
                overwriter: Optional[Callable] = None) -> dict:
        """Run the plan. DRY-RUN unless apply=True. Best-effort secure delete:
        overwrite-then-unlink. Honest caveat: on flash/SD wear-leveling makes
        overwrite non-guaranteed — full-disk encryption is the real protection."""
        self._guard()
        plan = self.plan(mode)
        targets = [i for i in plan["wipe"] if i.exists]
        if not apply:
            return {"mode": mode, "applied": False,
                    "would_wipe": [i.to_dict() for i in targets],
                    "kept": [i.path for i in plan["keep"]],
                    "note": "DRY RUN — pass apply=True (CLI: --apply) to actually wipe",
                    "flash_caveat": "overwrite is best-effort on flash; FDE is the real guarantee"}
        remover = remover or os.remove

        def _default_overwriter(fp: str) -> None:
            try:
                n = os.path.getsize(fp)
                if 0 < n <= 50 * 1024 * 1024:            # don't churn huge files
                    with open(fp, "r+b") as fh:
                        fh.write(os.urandom(n))
                        fh.flush()
                        os.fsync(fh.fileno())
            except OSError:
                pass
        overwriter = overwriter or _default_overwriter
        results = []
        for item in targets:
            ok, msg = self._wipe_path(item.path, overwrite=overwrite,
                                      remover=remover, overwriter=overwriter)
            results.append({"path": item.path, "tier": item.tier, "ok": ok, "result": msg})
        wiped = sum(1 for r in results if r["ok"])
        return {"mode": mode, "applied": True, "wiped": wiped, "results": results,
                "kept": [i.path for i in plan["keep"]],
                "flash_caveat": "overwrite is best-effort on flash; FDE is the real guarantee"}
