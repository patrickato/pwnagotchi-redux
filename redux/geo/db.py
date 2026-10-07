"""BeastSpatialDB — unified sighting store (stdlib sqlite3).

One schema for WiFi / BLE / SDR sightings with glass-box provenance on
every row. Dedup key is (kind, mac): keeps the strongest RSSI sample and
the earliest first_seen timestamp.

No redux.engine import — the lead wires live GPS/events at integration.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Union

PathLike = Union[str, Path]

# Default on-device path (bus convention); tests use :memory: or a temp file.
DEFAULT_DB_PATH = "/var/lib/redux/geo/sightings.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL,   -- wifi | ble | sdr | other
    mac           TEXT    NOT NULL,   -- BSSID / BD_ADDR / normalized id
    ssid          TEXT    NOT NULL DEFAULT '',
    lat           REAL,
    lon           REAL,
    rssi          INTEGER,
    channel       INTEGER,
    source_radio  TEXT    NOT NULL DEFAULT '',
    ts            REAL    NOT NULL,   -- last observation time
    first_seen    REAL    NOT NULL,   -- earliest observation time
    provenance    TEXT    NOT NULL,   -- glass-box: why/how this row exists
    UNIQUE (kind, mac)
);
CREATE INDEX IF NOT EXISTS idx_sightings_ts ON sightings(ts);
CREATE INDEX IF NOT EXISTS idx_sightings_kind ON sightings(kind);
"""


@dataclass(frozen=True)
class Sighting:
    """One RF sighting ready for insert or returned from a query."""

    kind: str
    mac: str
    ssid: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    rssi: Optional[int] = None
    channel: Optional[int] = None
    source_radio: str = ""
    ts: float = 0.0
    provenance: str = ""
    first_seen: Optional[float] = None  # filled by store on read / insert

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", (self.kind or "other").lower().strip())
        object.__setattr__(self, "mac", (self.mac or "").lower().strip())
        object.__setattr__(self, "ssid", self.ssid or "")
        object.__setattr__(self, "source_radio", self.source_radio or "")
        object.__setattr__(self, "provenance", (self.provenance or "").strip())


class SightingStore:
    """SQLite-backed sighting store with best-RSSI / first-seen dedup."""

    def __init__(self, path: PathLike = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        # SD-wear: WAL coalesces writes and survives power loss to the last
        # checkpoint; synchronous=NORMAL cuts fsyncs vs FULL without risking the
        # DB. (No-ops harmlessly on an in-memory store.)
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

    def __enter__(self) -> "SightingStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def insert(self, sighting: Sighting) -> Sighting:
        """Insert or merge one sighting, committing immediately. Returns the
        stored row state. For a batch, prefer insert_many (one transaction)."""
        self._apply(sighting)
        self._conn.commit()
        return self.get(sighting.kind, sighting.mac)  # type: ignore[return-value]

    def _apply(self, sighting: Sighting) -> None:
        """Insert or merge one sighting WITHOUT committing (so a batch can share
        one transaction — the SD-friendly path).

        Dedup (kind, mac):
        - first_seen = min(existing, new)
        - rssi = max by strength (None loses to a real value; weaker skipped)
        - ts / lat / lon / channel / source_radio / ssid / provenance update
          when the new sample is kept as the best-RSSI (or first insert).
        """
        if not sighting.mac:
            raise ValueError("Sighting.mac is required")
        if not sighting.provenance:
            raise ValueError(
                "Sighting.provenance is required (glass-box: why this row exists)"
            )

        existing = self.get(sighting.kind, sighting.mac)
        if existing is None:
            first = sighting.ts if sighting.first_seen is None else sighting.first_seen
            self._conn.execute(
                """
                INSERT INTO sightings (
                    kind, mac, ssid, lat, lon, rssi, channel,
                    source_radio, ts, first_seen, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sighting.kind,
                    sighting.mac,
                    sighting.ssid,
                    sighting.lat,
                    sighting.lon,
                    sighting.rssi,
                    sighting.channel,
                    sighting.source_radio,
                    sighting.ts,
                    first,
                    sighting.provenance,
                ),
            )
            return

        first_seen = min(existing.first_seen or existing.ts, sighting.ts)
        keep_new = _better_rssi(sighting.rssi, existing.rssi)

        if keep_new:
            self._conn.execute(
                """
                UPDATE sightings SET
                    ssid = ?, lat = ?, lon = ?, rssi = ?, channel = ?,
                    source_radio = ?, ts = ?, first_seen = ?, provenance = ?
                WHERE kind = ? AND mac = ?
                """,
                (
                    sighting.ssid or existing.ssid,
                    sighting.lat if sighting.lat is not None else existing.lat,
                    sighting.lon if sighting.lon is not None else existing.lon,
                    sighting.rssi,
                    sighting.channel if sighting.channel is not None else existing.channel,
                    sighting.source_radio or existing.source_radio,
                    max(existing.ts, sighting.ts),
                    first_seen,
                    sighting.provenance,
                    sighting.kind,
                    sighting.mac,
                ),
            )
        else:
            # Weaker/equal RSSI: keep the stronger sample's values, but still
            # BACKFILL fields the stored row is missing (NULL coords/channel,
            # empty ssid) from this sample — never discard a real GPS fix just
            # because this sample's RSSI was weaker. Never overwrites a value
            # the stronger sample already set.
            self._conn.execute(
                """
                UPDATE sightings SET
                    ssid = ?, lat = ?, lon = ?, channel = ?, ts = ?, first_seen = ?
                WHERE kind = ? AND mac = ?
                """,
                (
                    existing.ssid or sighting.ssid,
                    existing.lat if existing.lat is not None else sighting.lat,
                    existing.lon if existing.lon is not None else sighting.lon,
                    existing.channel if existing.channel is not None else sighting.channel,
                    max(existing.ts, sighting.ts),
                    first_seen,
                    sighting.kind,
                    sighting.mac,
                ),
            )

    def insert_many(self, sightings: Iterable[Sighting]) -> List[Sighting]:
        """Insert/merge a batch in ONE transaction — one commit, one fsync, not
        N. This is the SD-friendly write path for coalesced sighting flushes."""
        items = list(sightings)
        for s in items:
            self._apply(s)
        self._conn.commit()
        return [self.get(s.kind, s.mac) for s in items]  # type: ignore[list-item]

    def get(self, kind: str, mac: str) -> Optional[Sighting]:
        row = self._conn.execute(
            "SELECT * FROM sightings WHERE kind = ? AND mac = ?",
            ((kind or "").lower(), (mac or "").lower()),
        ).fetchone()
        return _row_to_sighting(row) if row else None

    def query(
        self,
        *,
        kind: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[Sighting]:
        clauses: List[str] = []
        params: List[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.lower())
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until)
        sql = "SELECT * FROM sightings"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_sighting(r) for r in rows]

    def count(self, kind: Optional[str] = None) -> int:
        if kind is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM sightings").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM sightings WHERE kind = ?",
                (kind.lower(),),
            ).fetchone()
        return int(row["n"]) if row else 0


def _better_rssi(new: Optional[int], old: Optional[int]) -> bool:
    """True if new should replace old (higher RSSI wins; any value beats None)."""
    if new is None:
        return False
    if old is None:
        return True
    return new > old


def _row_to_sighting(row: sqlite3.Row) -> Sighting:
    return Sighting(
        kind=row["kind"],
        mac=row["mac"],
        ssid=row["ssid"] or "",
        lat=row["lat"],
        lon=row["lon"],
        rssi=row["rssi"],
        channel=row["channel"],
        source_radio=row["source_radio"] or "",
        ts=float(row["ts"]),
        provenance=row["provenance"] or "",
        first_seen=float(row["first_seen"]),
    )
