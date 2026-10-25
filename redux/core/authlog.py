"""Chain-of-authorization log — a tamper-evident, provable engagement record.

redux already refuses to fire outside the central Scope. This is the other half
of defensible operation: a record of *when each target was authorized and what
was done*, that an operator can hand to a client (or keep for their own files)
and that cannot be quietly edited after the fact.

Every entry is appended to a JSONL file and hash-chained to the one before it:

    hash_n = sha256( seq | ts | event | detail | hash_{n-1} )

so altering, inserting, removing, or reordering *any* past entry breaks the
chain from that point on — `verify()` finds the exact break. That tamper-
evidence needs no secret. Authenticity (proof the log came from *this* device,
not a forgery someone recomputed) is optional: if a device signing key exists,
each entry also carries an HMAC-SHA256 over its hash, and `verify(key=...)`
checks it.

Glass-box and real-data-only: the log records what actually happened (scope
arming, fire-capable actions), each break is explained in plain language, and
nothing is fabricated. Fail-safe: an unreadable key just means unsigned entries,
never a crash.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

GENESIS_PREV = "0" * 64


def _captures_or(fallback: str, name: str) -> str:
    try:
        if os.path.ismount("/captures"):
            return f"/captures/config/{name}"
    except OSError:
        pass
    return fallback


def default_authlog_path() -> str:
    """Canonical auth-log path. REDUX_AUTHLOG overrides; else the persistent
    /captures partition (survives reboot) over tmpfs /etc on the field image."""
    return os.environ.get("REDUX_AUTHLOG") or _captures_or(
        "/etc/pwnagotchi/authlog.jsonl", "authlog.jsonl")


def default_key_path() -> str:
    """Canonical signing-key path. REDUX_AUTHLOG_KEY overrides."""
    return os.environ.get("REDUX_AUTHLOG_KEY") or _captures_or(
        "/etc/pwnagotchi/authlog.key", "authlog.key")


@dataclass(frozen=True)
class AuthLogEntry:
    seq: int
    ts: float
    event: str
    detail: Dict
    prev: str
    hash: str
    sig: Optional[str] = None

    def to_json(self) -> str:
        obj = {"seq": self.seq, "ts": self.ts, "event": self.event,
               "detail": self.detail, "prev": self.prev, "hash": self.hash}
        if self.sig is not None:
            obj["sig"] = self.sig
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    count: int
    reason: str
    broken_seq: Optional[int] = None
    signed: bool = False


def _entry_hash(seq: int, ts: float, event: str, detail: Dict, prev: str) -> str:
    canonical = json.dumps(
        {"seq": seq, "ts": ts, "event": event, "detail": detail, "prev": prev},
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sign(key: bytes, entry_hash: str) -> str:
    return hmac.new(key, bytes.fromhex(entry_hash), hashlib.sha256).hexdigest()


def generate_key(path: str, *, overwrite: bool = False) -> bytes:
    """Create a 32-byte device signing key (stored hex, 0600). Refuses to clobber
    an existing key unless overwrite=True (replacing it invalidates every existing
    signature, so verification of past entries would then fail)."""
    key = secrets.token_bytes(32)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= 0 if overwrite else os.O_EXCL
    if overwrite:
        flags |= os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.hex())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def load_key(path: str) -> Optional[bytes]:
    """The device signing key, or None if absent/unreadable (→ unsigned log)."""
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read().strip()
    except OSError:
        return None
    try:
        return bytes.fromhex(txt) if txt else None
    except ValueError:
        return None


class AuthLog:
    """Append-only hash-chained log at `path`, optionally HMAC-signed with `key`."""

    def __init__(self, path: str, *, key: Optional[bytes] = None):
        self.path = path
        self.key = key

    # --- read ------------------------------------------------------------- #

    def entries(self) -> List[AuthLogEntry]:
        """Parsed entries (corrupt lines skipped — use verify() to detect them)."""
        out: List[AuthLogEntry] = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    out.append(AuthLogEntry(
                        seq=int(d.get("seq", -1)), ts=float(d.get("ts", 0.0)),
                        event=str(d.get("event", "")), detail=dict(d.get("detail") or {}),
                        prev=str(d.get("prev", "")), hash=str(d.get("hash", "")),
                        sig=d.get("sig")))
        except OSError:
            return []
        return out

    def _tail(self) -> Tuple[int, str]:
        """(next_seq, prev_hash) from the last line, for appending."""
        last = None
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s:
                        last = s
        except OSError:
            return 0, GENESIS_PREV
        if last is None:
            return 0, GENESIS_PREV
        d = json.loads(last)        # a corrupt tail raises → caller surfaces it
        return int(d["seq"]) + 1, str(d["hash"])

    # --- write ------------------------------------------------------------ #

    def record(self, event: str, detail: Optional[Dict] = None, *,
               now: Optional[float] = None) -> AuthLogEntry:
        now = time.time() if now is None else now
        detail = dict(detail or {})
        next_seq, prev = self._tail()
        h = _entry_hash(next_seq, now, event, detail, prev)
        sig = _sign(self.key, h) if self.key else None
        entry = AuthLogEntry(seq=next_seq, ts=now, event=event, detail=detail,
                             prev=prev, hash=h, sig=sig)
        self._append(entry.to_json())
        return entry

    def _append(self, line: str) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # --- integrity -------------------------------------------------------- #

    def verify(self, *, key: Optional[bytes] = None) -> VerifyResult:
        """Walk the chain and report the first break (if any). With a key, also
        check each entry's signature."""
        key = key if key is not None else self.key
        try:
            with open(self.path, encoding="utf-8") as f:
                raw_lines = f.readlines()
        except OSError:
            return VerifyResult(True, 0, "no log yet — nothing to verify", None, bool(key))

        expected_seq = 0
        expected_prev = GENESIS_PREV
        count = 0
        all_signed = True
        for raw in raw_lines:
            line = raw.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                return VerifyResult(False, count, f"entry {expected_seq}: corrupt JSON "
                                    "— a line was tampered with or truncated", expected_seq, bool(key))
            seq, ts = d.get("seq"), d.get("ts")
            event, detail = d.get("event"), dict(d.get("detail") or {})
            prev, h, sig = d.get("prev"), d.get("hash"), d.get("sig")
            if seq != expected_seq:
                return VerifyResult(False, count, f"entry {seq}: sequence out of order "
                                    f"(expected {expected_seq}) — an entry was inserted or removed",
                                    expected_seq, bool(key))
            if prev != expected_prev:
                return VerifyResult(False, count, f"entry {seq}: prev-hash does not match the "
                                    "previous entry — the chain was broken (an earlier entry changed "
                                    "or was removed)", seq, bool(key))
            if h != _entry_hash(seq, ts, event, detail, prev):
                return VerifyResult(False, count, f"entry {seq}: hash mismatch — this entry's "
                                    "contents were altered after it was written", seq, bool(key))
            if key is not None:
                if not sig:
                    return VerifyResult(False, count, f"entry {seq}: unsigned, but a key is "
                                        "configured — entry not produced with this device key", seq, True)
                if not hmac.compare_digest(sig, _sign(key, h)):
                    return VerifyResult(False, count, f"entry {seq}: bad signature — not signed "
                                        "by this device key", seq, True)
            all_signed = all_signed and bool(sig)
            count += 1
            expected_seq = seq + 1
            expected_prev = h

        signed = bool(key) or (count > 0 and all_signed)
        reason = f"intact — {count} entr{'y' if count == 1 else 'ies'}, chain verified"
        if count and key is not None:
            reason += ", signatures valid"
        elif count and signed:
            reason += " (entries are signed; pass a key to check signatures)"
        return VerifyResult(True, count, reason, None, signed)


def record_event(event: str, detail: Optional[Dict] = None, *,
                 path: Optional[str] = None, key_path: Optional[str] = None) -> Optional[AuthLogEntry]:
    """Best-effort append to the device auth log: signs automatically if a device
    key exists, else hash-chains only. Swallows every error (a logging failure
    must never block a scope change or a fire decision). Returns the entry or None."""
    try:
        key = load_key(key_path or default_key_path())
        return AuthLog(path or default_authlog_path(), key=key).record(event, detail)
    except Exception:
        return None
