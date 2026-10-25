"""Target-aware wordlist/mask forge — turn recon into the attack.

A generic rockyou run ignores everything you learned during recon. This builds a
small, *targeted* candidate set from what you actually observed — the SSID, the
AP vendor (OUI), nearby probed network names (PNL), and any OSINT you supply
(owner name, pet, street, zip, phone) — plus the hashcat masks worth trying
first. It's the supplement you run *before* the big generic list, and it feeds
straight into what `redux triage` already recommends per target.

Honest about what it is: these are candidate-generation heuristics (case/leet
variants, common year/suffix patterns, keyboard walks, digit/date/phone masks),
not a claim that any of them is the key. WPA2-PSK is 8–63 chars, so anything
shorter is dropped (hashcat would skip it anyway). Pure/deterministic — same
inputs, same list — so runs are reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..geo.oui import lookup_oui

_LEET = str.maketrans({"a": "4", "e": "3", "o": "0", "i": "1", "s": "5", "t": "7"})
_SUFFIXES = ("", "1", "12", "123", "1234", "12345", "!", "1!", "01", "007", "00",
             "99", "69", "420", "2021", "2022", "2023", "2024", "2025", "2026",
             "wifi", "Wifi", "#1")
# common PSKs / keyboard walks that are >= 8 chars
_KEYBOARD = ("password", "password1", "password123", "qwertyui", "qwerty123",
             "qwertyuiop", "1qaz2wsx", "1q2w3e4r", "zaq12wsx", "qazwsxedc",
             "asdfghjkl", "1234567890", "12345678", "87654321", "admin123",
             "changeme", "letmein1", "iloveyou")
_MASKS = (
    ("?d?d?d?d?d?d?d?d", "8 digits — PINs and dates (MMDDYYYY), a very common WPA key"),
    ("?d?d?d?d?d?d?d?d?d?d", "10 digits — phone numbers"),
    ("?d?d?d?d?d?d?d?d?d?d?d", "11 digits — phone with country/area code"),
    ("?u?l?l?l?l?l?d?d", "Capitalised word + 2 digits (e.g. Summer24)"),
    ("?u?l?l?l?l?l?l?l", "Capitalised 8-letter word"),
    ("?l?l?l?l?l?l?l?l", "8 lowercase letters"),
)


@dataclass(frozen=True)
class Mask:
    mask: str
    note: str


@dataclass(frozen=True)
class Forge:
    ssid: str
    seeds: Tuple[str, ...]
    vendor: Optional[str]
    words: Tuple[str, ...]
    masks: Tuple[Mask, ...]
    notes: Tuple[str, ...]


def mutate_word(word: str) -> List[str]:
    """Case + leet + common-suffix variants of one seed (all lengths; the caller
    length-filters). Deterministic order."""
    base = (word or "").strip()
    if not base:
        return []
    low = base.lower()
    cores = []
    for c in (base, low, base.upper(), base.capitalize(), base.title(), low.translate(_LEET)):
        c = c.replace(" ", "")
        if c and c not in cores:
            cores.append(c)
    out: List[str] = []
    seen = set()
    for core in cores:
        for suf in _SUFFIXES:
            w = core + suf
            if w not in seen:
                seen.add(w)
                out.append(w)
    return out


def forge(ssid: str = "", *, tokens: Sequence[str] = (), pnl: Sequence[str] = (),
          bssid: Optional[str] = None, min_len: int = 8, max_len: int = 63,
          max_words: int = 2000) -> Forge:
    """Build a targeted candidate wordlist + mask set from recon inputs."""
    seeds: List[str] = []
    def add(s):
        s = (s or "").strip()
        if s and s not in seeds:
            seeds.append(s)
    add(ssid)
    vendor = lookup_oui(bssid) if bssid else None
    if vendor:
        add(vendor)
    for t in tokens:
        add(t)
    for s in pnl:
        add(s)

    candidates: List[str] = []
    for seed in seeds:
        candidates.extend(mutate_word(seed))
    candidates.extend(_KEYBOARD)            # generic walks after the targeted ones

    words: List[str] = []
    seen = set()
    for w in candidates:
        if min_len <= len(w) <= max_len and w not in seen:
            seen.add(w)
            words.append(w)
            if len(words) >= max_words:
                break

    notes = [
        f"{len(words)} targeted candidate(s) from {len(seeds)} seed(s) — run these "
        "BEFORE a generic list (rockyou), not instead of it",
        "masks are starting points for a mask attack (hashcat -a 3); try digits/dates first",
    ]
    if vendor:
        notes.append(f"AP vendor {vendor!r} (from OUI) added as a seed — not a default-key "
                     "generator, just a word worth trying")
    return Forge(ssid=ssid, seeds=tuple(seeds), vendor=vendor,
                 words=tuple(words), masks=tuple(Mask(m, n) for m, n in _MASKS),
                 notes=tuple(notes))
