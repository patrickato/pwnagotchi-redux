"""Scope-bounded location trail — a persistent fox-hunt history, armed-only.

The sighting store (`db.py`) keeps exactly one latest/best row per (kind, mac):
a snapshot, not a history. Fox-hunting a specific transmitter over time — "where
has this BSSID been, and where is it now" — needs a *trail* of fixes. This store
provides that, with one hard rule that keeps it from becoming mass surveillance:

    **Every fix is Scope-gated.** `record_fix()` asks the central Scope whether
    the BSSID is authorized and logs it ONLY if it is. A target you have not
    armed is refused and nothing is written — so the trail accumulates location
    history for the handful of transmitters you are hunting (your own gear, a
    rogue AP you are chasing, a contracted target), never for every bystander
    device in range. That is the difference between a legitimate direction-
    finding log and a who-was-where database of the whole airspace.

Everything else mirrors the sighting store's field conventions so it behaves the
same on the device: WAL + synchronous=NORMAL + busy_timeout, a required
`provenance` on every row (glass-box), batch-commit via `insert_many`, and a
`prune(older_than=, max_rows=)` with the identical shape — so old fixes expire
and the trail can't grow unbounded on a long hunt (pruning old location history
is privacy-positive too).

Pure/testable: no radio, no GPS, no engine import. The caller passes a `Scope`
and the fix coordinates; live GPS (`geo/gps.py`) and events are wired by the lead.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from .estimate import estimate_from_coords

PathLike = Union[str, Path]

DEFAULT_TRACKS_PATH = "/var/lib/redux/geo/tracks.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fixes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bssid       TEXT    NOT NULL,   -- the armed target this fix belongs to
    lat         REAL    NOT NULL,
    lon         REAL    NOT NULL,
    rssi        INTEGER,            -- observer RSSI at this fix (for estimates)
    ts          REAL    NOT NULL,   -- when the fix was taken
    node_id     TEXT    NOT NULL DEFAULT '',  -- which swarm node logged it
    provenance  TEXT    NOT NULL,   -- glass-box: why/how this fix exists
    job         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fixes_bssid_ts ON fixes(bssid, ts);
CREATE INDEX IF NOT EXISTS idx_fixes_ts ON fixes(ts);
"""


def _norm(b: str) -> str:
    return (b or "").strip().lower()


@dataclass(frozen=True)
class Fix:
    """One location fix on an armed target."""
    bssid: str
    lat: float
    lon: float
    ts: float
    rssi: Optional[int] = None
    node_id: str = ""
    provenance: str = ""
    job: str = ""


class TrackStore:
    """SQLite-backed, Scope-gated fix trail per BSSID."""

    def __init__(self, path: PathLike = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.DatabaseError:
            pass
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TrackStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- the Scope gate ---------------------------------------------------- #

    def record_fix(self, scope, bssid: str, lat: float, lon: float, *,
                   ts: Optional[float] = None, rssi: Optional[int] = None,
                   node_id: str = "", provenance: str = "fox-hunt fix",
                   job: Optional[str] = None,
                   now: Optional[float] = None) -> Tuple[bool, str]:
        """Log a fix for `bssid` — ONLY if the central Scope authorizes it.

        Returns a glass-box (recorded, reason). An un-armed target is refused and
        NOTHING is written: the trail only ever holds location history for targets
        you deliberately armed. `scope` is the live `redux.core.Scope`."""
        now = time.time() if now is None else now
        ts = now if ts is None else ts
        b = _norm(bssid)
        if scope is None:
            return False, "refused: no scope — a fix trail needs an armed target"
        ok, why = scope.authorize(bssid=b, now=now, job=job)
        if not ok:
            # Nothing is written for a target you did not arm.
            return False, f"not logged — {why}"
        if not provenance:
            provenance = "fox-hunt fix"
        try:
            lat = float(lat); lon = float(lon)
        except (TypeError, ValueError):
            return False, "refused: fix latitude/longitude must be numbers"
        if not (math.isfinite(lat) and math.isfinite(lon)
                and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return False, "refused: fix latitude/longitude out of range"
        try:
            self._conn.execute(
                "INSERT INTO fixes (bssid, lat, lon, rssi, ts, node_id, provenance, job) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (b, lat, lon, rssi if rssi is None else int(rssi), float(ts),
                 node_id, provenance, job or ""),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return True, f"logged fix for armed {b} at ({lat:.5f},{lon:.5f})"

    def record_many(self, scope, fixes: Iterable[Fix], *,
                    now: Optional[float] = None) -> Tuple[int, List[str]]:
        """Batch form (one transaction, SD-friendly). Each fix is Scope-gated
        individually; un-armed targets are skipped with a reason. Returns
        (recorded_count, skipped_reasons)."""
        now = time.time() if now is None else now
        recorded = 0
        skipped: List[str] = []
        rows = []
        for f in fixes:
            b = _norm(f.bssid)
            ok, why = scope.authorize(bssid=b, now=now, job=f.job or None) if scope else (False, "no scope")
            if not ok:
                skipped.append(f"{b}: {why}")
                continue
            try:
                lat, lon = float(f.lat), float(f.lon)
            except (TypeError, ValueError):
                skipped.append(f"{b}: non-numeric coordinates")
                continue
            if not (math.isfinite(lat) and math.isfinite(lon)
                    and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                skipped.append(f"{b}: coordinates out of range")
                continue
            rows.append((b, lat, lon, f.rssi if f.rssi is None else int(f.rssi),
                         float(f.ts or now), f.node_id,
                         f.provenance or "fox-hunt fix", f.job or ""))
            recorded += 1
        if rows:
            try:
                self._conn.executemany(
                    "INSERT INTO fixes (bssid, lat, lon, rssi, ts, node_id, provenance, job) "
                    "VALUES (?,?,?,?,?,?,?,?)", rows)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return recorded, skipped

    # --- read side --------------------------------------------------------- #

    def trail(self, bssid: str, *, since: Optional[float] = None,
              until: Optional[float] = None, limit: Optional[int] = None) -> List[dict]:
        """The ordered fix trail for one target (oldest first)."""
        b = _norm(bssid)
        sql = "SELECT bssid, lat, lon, rssi, ts, node_id, provenance, job FROM fixes WHERE bssid=?"
        params: list = [b]
        if since is not None:
            sql += " AND ts >= ?"; params.append(float(since))
        if until is not None:
            sql += " AND ts <= ?"; params.append(float(until))
        sql += " ORDER BY ts ASC, id ASC"
        if limit is not None:
            sql += " LIMIT ?"; params.append(int(limit))
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def latest(self, bssid: str) -> Optional[dict]:
        """The most recent fix for a target, or None."""
        b = _norm(bssid)
        r = self._conn.execute(
            "SELECT bssid, lat, lon, rssi, ts, node_id, provenance, job FROM fixes "
            "WHERE bssid=? ORDER BY ts DESC, id DESC LIMIT 1", (b,)).fetchone()
        return dict(r) if r else None

    def estimate(self, bssid: str, *, min_observations: int = 2):
        """A glass-box position estimate from this target's RSSI-tagged fixes, or
        None when there aren't enough located samples. Reuses the shared estimator."""
        points = [(f["lat"], f["lon"], f["rssi"]) for f in self.trail(bssid)
                  if f["rssi"] is not None]
        if len(points) < min_observations:
            return None
        return estimate_from_coords(points, min_observations=min_observations)

    def targets(self) -> List[str]:
        """Distinct BSSIDs that have a trail."""
        return [r[0] for r in self._conn.execute(
            "SELECT DISTINCT bssid FROM fixes ORDER BY bssid").fetchall()]

    def count(self, bssid: Optional[str] = None) -> int:
        if bssid is None:
            return int(self._conn.execute("SELECT COUNT(*) FROM fixes").fetchone()[0])
        return int(self._conn.execute(
            "SELECT COUNT(*) FROM fixes WHERE bssid=?", (_norm(bssid),)).fetchone()[0])

    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT bssid) FROM fixes").fetchone()
        return {"fixes": int(row[0] or 0), "oldest_ts": row[1], "newest_ts": row[2],
                "targets": int(row[3] or 0)}

    # --- retention (mirror SightingStore.prune) ---------------------------- #

    def prune(self, *, older_than: Optional[float] = None,
              max_rows: Optional[int] = None, now: Optional[float] = None) -> int:
        """Delete old / excess fixes so the trail can't grow unbounded on a long
        hunt. `older_than` is an age in seconds (fixes with ts < now-older_than
        go); `max_rows` keeps only the most-recent N by ts. Commits once; does not
        VACUUM. Pruning old location history is privacy-positive."""
        if older_than is not None:
            if not math.isfinite(float(older_than)) or float(older_than) < 0:
                raise ValueError("fix retention age must be finite and non-negative")
        if max_rows is not None and (type(max_rows) is not int or max_rows < 0):
            raise ValueError("fix max_rows must be a non-negative integer")
        now = time.time() if now is None else now
        deleted = 0
        try:
            if older_than is not None:
                cutoff = now - float(older_than)
                c = self._conn.execute("DELETE FROM fixes WHERE ts < ?", (cutoff,))
                deleted += c.rowcount or 0
            if max_rows is not None:
                c = self._conn.execute(
                    "DELETE FROM fixes WHERE id NOT IN "
                    "(SELECT id FROM fixes ORDER BY ts DESC, id DESC LIMIT ?)",
                    (max_rows,))
                deleted += c.rowcount or 0
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return deleted

    def forget(self, bssid: str) -> int:
        """Delete the whole trail for one target (e.g. when you un-arm it).
        Returns fixes removed."""
        b = _norm(bssid)
        c = self._conn.execute("DELETE FROM fixes WHERE bssid=?", (b,))
        self._conn.commit()
        return c.rowcount or 0
