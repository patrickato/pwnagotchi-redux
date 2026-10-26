"""Loot rollup — one "what did I actually get?" view across every loot source.

The loot is scattered: recovered credentials in the vault, files pulled off hosts,
NetNTLM hashes from Responder, typed secrets inside those files. After an op you
want the single answer, not five commands. This aggregates them into one glass-box
summary (counts + the typed-secrets breakdown), redacted and shareable like the
rest of the loot surface.

A + B + ... ⇒ C: every loot source ⇒ one rollup the engagement case file and the
dashboards can show. Pure/deterministic over injected paths; fail-safe (a missing
source counts as zero, never an error).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def _count_files(directory: str) -> int:
    try:
        n = 0
        for _root, _d, files in os.walk(directory):
            n += len(files)
        return n
    except OSError:
        return 0


def _count_hash_lines(directory: str) -> int:
    try:
        total = 0
        for p in Path(directory).glob("*.txt"):
            try:
                total += sum(1 for line in p.read_text(errors="replace").splitlines() if line.strip())
            except OSError:
                continue
        return total
    except OSError:
        return 0


def _loot_files(directory: str) -> List[str]:
    out: List[str] = []
    try:
        for root, _d, files in os.walk(directory):
            out.extend(os.path.join(root, f) for f in files)
    except OSError:
        pass
    return out


def rollup(*, vault_entries: Optional[Sequence] = None,
           loot_dir: str = "/captures/loot/files",
           netntlm_dir: str = "/captures/loot/netntlm",
           scan_secrets: bool = True) -> dict:
    """Aggregate every loot source into one summary. All inputs optional/fail-safe."""
    creds = list(vault_entries or [])
    by_kind: Dict[str, int] = {}
    service_creds = 0
    for e in creds:
        kind = getattr(e, "kind", "") or (e.get("kind") if isinstance(e, dict) else "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if kind == "service-cred":
            service_creds += 1

    files = _loot_files(loot_dir)
    secrets_summary = {"total": 0, "by_kind": {}, "by_confidence": {}}
    top_secrets: List[dict] = []
    if scan_secrets and files:
        try:
            from .secrets import scan_files
            sc = scan_files(files)
            secrets_summary = sc["summary"]
            top_secrets = sc["findings"][:10]
        except Exception:
            pass

    netntlm = _count_hash_lines(netntlm_dir)
    total_items = len(creds) + len(files) + netntlm + secrets_summary.get("total", 0)
    return {
        "credentials": {"total": len(creds), "service": service_creds, "by_kind": by_kind},
        "files": {"count": len(files)},
        "netntlm_hashes": netntlm,
        "secrets": secrets_summary,
        "top_secrets": top_secrets,
        "total_items": total_items,
        "note": (f"{len(creds)} credential(s), {len(files)} file(s), {netntlm} NetNTLM hash(es), "
                 f"{secrets_summary.get('total', 0)} typed secret(s)"
                 if total_items else "no loot gathered yet"),
    }
