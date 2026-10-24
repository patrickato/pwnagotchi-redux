"""The 'already-pwned' / ignore list: APs that lab-mode offense should skip.

Two sources, unioned:

  - auto  — BSSIDs with a verified, crackable capture (a 'ready' ingest
            artifact), read straight from the pipeline DB. That is the
            authoritative record of what the box has actually captured, so once
            an AP gives up a full 4-way it stops being re-pwned.
  - manual — a small JSON overlay the operator manages with `redux captured add`,
            to tell the box to just ignore an AP (even without a capture).

The daemon retires the union so a done (or disregarded) AP stops burning cycles.
Real data only; every read FAILS SAFE to 'nothing' so a missing or locked source
can never crash the capture pump — at worst an AP is targeted one cycle longer.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

DEFAULT_JOBS_DB = "/captures/jobs.db"
DEFAULT_IGNORE_PATH = "/captures/config/captured.json"

_COLON = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
_BARE = re.compile(r"^[0-9a-f]{12}$")


def normalize_bssid(value) -> str | None:
    """Return a lowercase colon MAC (aa:bb:cc:dd:ee:ff), or None if not a 48-bit
    MAC. Accepts colon form or the bare 12-hex the 22000 format stores."""
    v = (value or "").strip().lower()
    if _COLON.match(v):
        return v
    if _BARE.match(v):
        return ":".join(v[i:i + 2] for i in range(0, 12, 2))
    return None


def captured_from_ingest(db_path=DEFAULT_JOBS_DB) -> dict:
    """{bssid: first_captured_ts} for every AP with a verified capture (a 'ready'
    artifact). Read-only; fails safe to {} on a missing/locked DB or bad row."""
    out: dict = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    except Exception:
        return out
    try:
        rows = con.execute(
            "SELECT bssids, created_at FROM artifacts WHERE status='ready'").fetchall()
    except Exception:
        return out
    finally:
        con.close()
    for raw, created in rows:
        try:
            lst = json.loads(raw) if raw else []
        except Exception:
            continue
        for b in lst:
            mac = normalize_bssid(b)
            if mac:
                prev = out.get(mac)
                out[mac] = created if prev is None else min(prev, created)
    return out


class IgnoreList:
    """Operator-managed overlay of APs to ignore. A JSON object
    {bssid: {"ssid": str, "added": ts}} written atomically. Missing file = empty."""

    def __init__(self, path=DEFAULT_IGNORE_PATH, *, now=time.time):
        self.path = Path(path)
        self._now = now
        self._items: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                self._items = {k: v for k, v in data.items()
                               if normalize_bssid(k) and isinstance(v, dict)}
        except Exception:
            self._items = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._items, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def add(self, bssid, *, ssid: str = "") -> tuple[bool, str]:
        mac = normalize_bssid(bssid)
        if not mac:
            return False, f"refused: not a MAC address: {bssid!r}"
        prior = self._items.get(mac, {})
        self._items[mac] = {"ssid": ssid or prior.get("ssid", ""),
                            "added": prior.get("added") or self._now()}
        self._save()
        return True, mac

    def remove(self, bssid) -> tuple[bool, str]:
        mac = normalize_bssid(bssid)
        if mac and mac in self._items:
            del self._items[mac]
            self._save()
            return True, mac
        return False, f"not in the ignore list: {bssid!r}"

    def bssids(self) -> set:
        return set(self._items)

    def items(self) -> dict:
        return dict(self._items)


def retired_bssids(db_path=DEFAULT_JOBS_DB, ignore_path=DEFAULT_IGNORE_PATH) -> set:
    """The full set the daemon retires from offense: verified captures ∪ manual
    ignores. Fails safe — each source contributes nothing on error."""
    s = set(captured_from_ingest(db_path))
    try:
        s |= IgnoreList(ignore_path).bssids()
    except Exception:
        pass
    return s
