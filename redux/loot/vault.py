"""Loot vault — the durable, on-device store of recovered credentials.

netrecon's LootBook is per-run and ephemeral. This persists what you actually
recovered — cracked WPA PSKs and service credentials — across runs, links each
to the signed chain-of-authorization log, and serves them to the engagement
report. A cracked key is only loot once you can find it again.

Credentials are sensitive: the vault file is written owner-only (0600) on the
device's own storage and is **never** sent to the shared memory system. The
auth-log entry records only that a credential was added (kind, target, who),
**never the secret** — the log is a shareable integrity proof, not a password
dump.

Wi-Fi PSK entries serialize with `bssid`/`ssid`/`psk` keys so the vault file is
directly consumable by `redux report --loot`.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import List, Optional


def default_loot_path() -> str:
    env = os.environ.get("REDUX_LOOT")
    if env:
        return env
    try:
        if os.path.ismount("/captures"):
            return "/captures/loot/vault.json"
    except OSError:
        pass
    return "/etc/pwnagotchi/loot.json"


@dataclass(frozen=True)
class LootEntry:
    kind: str                 # "wifi-psk" | "service-cred"
    target: str               # bssid (wifi) or ip (service)
    secret: str               # the PSK / password
    ssid: str = ""
    service: str = ""
    username: str = ""
    source: str = ""          # "crack" | "pivot" | free text
    ts: float = 0.0

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "target": self.target, "ssid": self.ssid,
             "service": self.service, "username": self.username,
             "secret": self.secret, "source": self.source, "ts": self.ts}
        if self.kind == "wifi-psk":
            d["bssid"] = self.target          # report-friendly aliases
            d["psk"] = self.secret
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LootEntry":
        return cls(
            kind=str(d.get("kind") or ("wifi-psk" if d.get("psk") else "service-cred")),
            target=str(d.get("target") or d.get("bssid") or d.get("ip") or ""),
            secret=str(d.get("secret") or d.get("psk") or ""),
            ssid=str(d.get("ssid") or ""), service=str(d.get("service") or ""),
            username=str(d.get("username") or ""), source=str(d.get("source") or ""),
            ts=float(d.get("ts") or 0.0))

    def _dedupe_key(self):
        return (self.kind, self.target.lower(), self.service, self.username)

    def log_detail(self) -> dict:
        """What goes to the auth log — NEVER the secret."""
        d = {"kind": self.kind, "target": self.target, "source": self.source}
        if self.ssid:
            d["ssid"] = self.ssid
        if self.service:
            d["service"] = self.service
        if self.username:
            d["username"] = self.username
        return d


def _default_logger(event, detail):
    try:
        from ..core.authlog import record_event
        record_event(event, detail)
    except Exception:
        pass


class LootVault:
    """Persistent credential store (one JSON file, owner-only)."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or default_loot_path()
        self._entries: List[LootEntry] = []
        try:
            with open(self.path, encoding="utf-8") as f:
                blob = json.load(f)
            rows = blob.get("entries", []) if isinstance(blob, dict) else blob
            if isinstance(rows, list):
                self._entries = [LootEntry.from_dict(r) for r in rows if isinstance(r, dict)]
        except (OSError, ValueError, TypeError):
            self._entries = []          # fail-safe: empty vault

    def entries(self) -> List[LootEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, entry: LootEntry, *, logger=_default_logger) -> bool:
        """Add (dedup on kind/target/service/username — newer secret wins). Logs
        a NON-secret 'loot.add' event. Returns True if newly added (not a dup update)."""
        if not entry.ts:
            entry = LootEntry(**{**entry.__dict__, "ts": time.time()})
        key = entry._dedupe_key()
        existed = any(e._dedupe_key() == key for e in self._entries)
        self._entries = [e for e in self._entries if e._dedupe_key() != key]
        self._entries.append(entry)
        self._flush()
        if logger:
            logger("loot.add", entry.log_detail())
        return not existed

    def find_psk(self, *, bssid: str = "", ssid: str = "") -> Optional[str]:
        for e in self._entries:
            if e.kind != "wifi-psk":
                continue
            if bssid and e.target.lower() == bssid.lower():
                return e.secret
            if ssid and e.ssid == ssid:
                return e.secret
        return None

    def psk_list(self) -> List[str]:
        """Recovered PSKs, for credential-reuse spraying."""
        out, seen = [], set()
        for e in self._entries:
            if e.secret and e.secret not in seen:
                seen.add(e.secret); out.append(e.secret)
        return out

    def _flush(self) -> None:
        parent = os.path.dirname(self.path)
        try:
            if parent:
                os.makedirs(parent, mode=0o700, exist_ok=True)
            tmp = f"{self.path}.tmp"
            try:
                os.unlink(tmp)
            except OSError:
                pass
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(tmp, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"entries": [e.to_dict() for e in self._entries]}, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            pass                      # best-effort; a failed write is not fatal to the run
