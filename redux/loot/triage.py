"""Forensic triage — run the baked carving/metadata/stego tools over pulled files.

The tail of the kill-chain: once files are pulled off a looted host (or you hand it
any files), this runs them through the forensics tools the image now bakes —
exiftool (metadata), binwalk (embedded data / firmware structure), steghide (is
something hidden?) — plus an in-process strings+secret scan (so it works even with
no binutils). It reports what each tool found, honestly noting any tool that isn't
installed rather than inventing a result.

Pure/testable: the subprocess runner and the `which` probe are injected, and the
secret scan reads bytes directly, so the whole thing is verified with no tools.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

# secret-looking strings worth flagging in pulled loot
_SECRET_RE = re.compile(
    rb"(?i)(password\s*[=:]|passwd\s*[=:]|secret\s*[=:]|api[_-]?key|aws_secret|"
    rb"BEGIN [A-Z ]*PRIVATE KEY|xox[baprs]-|ghp_[A-Za-z0-9]{20,})")
_PRINTABLE = re.compile(rb"[\x20-\x7e]{6,}")
_MAX_READ = 8 * 1024 * 1024        # cap per-file byte scan at 8 MiB


def _run(runner, argv, timeout=30):
    try:
        return runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return subprocess.CompletedProcess(argv, 1, "", f"{type(error).__name__}")


def _strings_secrets(path: str) -> dict:
    """Extract printable strings and flag secret-looking ones — pure Python."""
    try:
        data = Path(path).read_bytes()[:_MAX_READ]
    except OSError as error:
        return {"available": False, "reason": type(error).__name__}
    hits: List[str] = []
    for m in _PRINTABLE.finditer(data):
        if _SECRET_RE.search(m.group(0)):
            s = m.group(0).decode("ascii", "replace")
            if s not in hits:
                hits.append(s)
    return {"available": True, "secret_hits": hits[:50], "secret_count": len(hits)}


def triage_file(path: str, *, runner: Callable = subprocess.run,
                which: Callable[[str], Optional[str]] = shutil.which) -> dict:
    """Triage one file. Each tool entry is present with its finding, or marked
    not-installed — never a fabricated result."""
    tools: Dict[str, dict] = {}

    if which("exiftool"):
        r = _run(runner, ["exiftool", "-s", "-G", path])
        tools["exiftool"] = {"available": True, "metadata": (r.stdout or "").strip()[:4000]}
    else:
        tools["exiftool"] = {"available": False, "reason": "exiftool not installed"}

    if which("binwalk"):
        r = _run(runner, ["binwalk", path])
        tools["binwalk"] = {"available": True, "signatures": (r.stdout or "").strip()[:4000]}
    else:
        tools["binwalk"] = {"available": False, "reason": "binwalk not installed"}

    if which("steghide"):
        r = _run(runner, ["steghide", "info", "-p", "", path])
        out = ((r.stdout or "") + (r.stderr or "")).strip()[:2000]
        tools["steghide"] = {"available": True, "info": out,
                             "maybe_embedded": "embedded" in out.lower()}
    else:
        tools["steghide"] = {"available": False, "reason": "steghide not installed"}

    tools["strings"] = _strings_secrets(path)
    flagged = tools["strings"].get("secret_count", 0) or tools["steghide"].get("maybe_embedded", False)
    return {"path": path, "tools": tools, "flagged": bool(flagged)}


def triage(files: Sequence[str], *, runner: Callable = subprocess.run,
           which: Callable[[str], Optional[str]] = shutil.which) -> Dict[str, dict]:
    """Triage several files → {path: result}. The flagged ones (secrets or hidden
    data) are what to look at first."""
    return {f: triage_file(f, runner=runner, which=which) for f in files}
