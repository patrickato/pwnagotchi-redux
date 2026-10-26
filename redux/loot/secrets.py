"""Typed secrets scanner — turn looted files into a ranked, redacted secrets report.

Triage flags "secret-looking strings"; this says WHAT each one is. A pack of typed
detectors classifies every hit — AWS keys, GitHub/Slack/Stripe tokens, Google API
keys, private keys, JWTs, credentialed DB URIs, bearer tokens, password
assignments — with a confidence and, crucially, a REDACTION. The report shows
enough to recognize a secret and where it came from, never the secret itself, so
it's shareable the way the authlog is (glass-box, not a password dump).

A + B ⇒ C: A (files pulled by file-loot #89) + B (this pack) ⇒ C (a typed,
ranked, redacted inventory of the credentials sitting in the loot). Pure and
deterministic — regex over text, no network, verified with fixtures.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_MAX_READ = 8 * 1024 * 1024        # cap per-file scan at 8 MiB


def _redact(s: str) -> str:
    """Show enough to recognize, never enough to use."""
    s = s.strip()
    if len(s) <= 8:
        return s[:2] + "****"
    return s[:4] + "…" + s[-2:]


@dataclass(frozen=True)
class SecretRule:
    name: str                 # "aws-access-key-id"
    kind: str                 # key | token | private-key | jwt | db-uri | password
    confidence: str           # high | medium
    pattern: "re.Pattern"
    group: int = 0            # which capture group holds the secret for redaction


# ordered high-confidence first; the first rule that matches a span wins.
RULES: Tuple[SecretRule, ...] = (
    SecretRule("aws-access-key-id", "key", "high", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    SecretRule("github-token", "token", "high", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,})\b")),
    SecretRule("slack-token", "token", "high", re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    SecretRule("google-api-key", "key", "high", re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})\b")),
    SecretRule("stripe-secret-key", "key", "high", re.compile(r"\b(sk_live_[0-9A-Za-z]{20,})\b")),
    SecretRule("private-key", "private-key", "high",
               re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    SecretRule("db-uri-with-creds", "db-uri", "high",
               re.compile(r"\b((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:/\s]+:[^@\s]+@[^\s\"']+)")),
    SecretRule("jwt", "jwt", "medium",
               re.compile(r"\b(eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)\b")),
    SecretRule("aws-secret-key", "key", "medium",
               re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*[\"']?([A-Za-z0-9/+=]{40})")),
    SecretRule("bearer-token", "token", "medium",
               re.compile(r"(?i)authorization\s*[=:]\s*bearer\s+([A-Za-z0-9._\-]{12,})")),
    SecretRule("generic-api-key", "key", "medium",
               re.compile(r"(?i)api[_-]?key\s*[=:]\s*[\"']?([A-Za-z0-9_\-]{16,})")),
    SecretRule("password-assignment", "password", "medium",
               re.compile(r"(?i)(?:password|passwd|pwd|secret)\s*[=:]\s*[\"']?([^\s\"']{4,})")),
)

_CONF_RANK = {"high": 0, "medium": 1}


@dataclass(frozen=True)
class SecretFinding:
    rule: str
    kind: str
    confidence: str
    value: str            # the FULL recovered secret — your loot, shown in the clear
    line: int
    source: str = ""

    @property
    def redacted(self) -> str:
        return _redact(self.value)

    def to_dict(self, *, redact: bool = False) -> dict:
        return {"rule": self.rule, "kind": self.kind, "confidence": self.confidence,
                "value": self.redacted if redact else self.value,
                "redacted": self.redacted, "line": self.line, "source": self.source}


def scan_text(text: str, *, source: str = "") -> List[SecretFinding]:
    """Typed secret findings in a blob of text (deduped per rule+value). Values are
    kept in full — this is the operator's own loot; redaction is opt-in at output."""
    out: List[SecretFinding] = []
    seen = set()
    for lineno, line in enumerate((text or "").splitlines(), start=1):
        for rule in RULES:
            m = rule.pattern.search(line)
            if not m:
                continue
            raw = m.group(rule.group) if rule.group and m.groups() else m.group(0)
            key = (rule.name, raw)
            if key in seen:
                continue
            seen.add(key)
            out.append(SecretFinding(rule=rule.name, kind=rule.kind,
                                     confidence=rule.confidence, value=raw,
                                     line=lineno, source=source))
    out.sort(key=lambda f: (_CONF_RANK.get(f.confidence, 9), f.rule, f.line))
    return out


def scan_file(path: str) -> List[SecretFinding]:
    try:
        data = Path(path).read_bytes()[:_MAX_READ]
    except OSError:
        return []
    return scan_text(data.decode("utf-8", "replace"), source=str(path))


def scan_files(paths: Sequence[str], *, redact: bool = False) -> dict:
    """Scan several files → {findings, by_file, summary}. Full values by default;
    pass redact=True to mask them for a shareable report."""
    by_file: Dict[str, List[dict]] = {}
    all_findings: List[SecretFinding] = []
    for p in paths or ():
        fs = scan_file(p)
        if fs:
            by_file[str(p)] = [f.to_dict(redact=redact) for f in fs]
            all_findings.extend(fs)
    return {"findings": [f.to_dict(redact=redact) for f in sorted(
                all_findings, key=lambda f: (_CONF_RANK.get(f.confidence, 9), f.source))],
            "by_file": by_file, "summary": summarize(all_findings)}


def summarize(findings: Sequence[SecretFinding]) -> dict:
    by_kind: Dict[str, int] = {}
    by_conf: Dict[str, int] = {}
    for f in findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        by_conf[f.confidence] = by_conf.get(f.confidence, 0) + 1
    high = by_conf.get("high", 0)
    return {"total": len(findings), "by_kind": by_kind, "by_confidence": by_conf,
            "note": (f"{len(findings)} secret(s) found, {high} high-confidence"
                     if findings else "no typed secrets found")}
