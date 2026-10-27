"""Environment-grown targeted wordlists — mine everything the box has seen.

The forge (#3) turns one SSID into a targeted candidate list. This grows the
*seeds* for it from the whole environment the device is sitting in — so the
wordlist is tuned to the people and gear actually here, not a generic rockyou.
It is the clearest "we have A" + "we have B" ⇒ "so we get C" in the kit:

  - A: SSIDs on the air + the probe-request PNLs of nearby phones (captap)
  - B: strings and metadata pulled out of looted files (triage #2 / file-loot #89)
  - C: BLE device names, AP vendor (OUI), scanned hostnames (net.recon)
      → one ranked seed set → the forge → a wordlist grown from this place.

Every token remembers which sources it came from (glass-box provenance), and a
token seen from several independent sources ranks higher — a name that is both
an SSID and a string in a looted doc is a strong guess. Pure/deterministic and
fail-safe: bad input is skipped, never fabricated, and nothing here transmits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

# split on anything that isn't a letter/digit, and on letter↔digit and camelCase
# boundaries, so "NetgearGuest5G" → netgear, guest, 5g and "SmithFamily2019" →
# smith, family, 2019.
_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
# noise tokens that are everywhere and tell us nothing about THIS environment
_STOP = {"wifi", "network", "wlan", "ssid", "the", "and", "for", "guest", "free",
         "public", "private", "home", "default", "router", "setup", "hidden",
         "ghz", "mhz", "2g", "5g", "6g", "24g", "net", "www", "http", "https"}
_MIN = 3        # tokens shorter than this are dropped as noise


def _tokenize(text: str) -> List[str]:
    """Break a name/string into lowercase word tokens worth seeding the forge."""
    if not text:
        return []
    out: List[str] = []
    for part in _SPLIT.split(str(text)):
        t = part.strip().lower()
        if len(t) >= _MIN and t not in _STOP and not t.isspace():
            out.append(t)
    return out


@dataclass
class EnvironmentHarvester:
    """Accumulate candidate seeds from the device's observations, with provenance.

    Sources are tracked per token so the output is explainable and rank-ordered;
    `grow()` hands the ranked seeds to the forge to produce the actual wordlist."""
    _tokens: Dict[str, Set[str]] = field(default_factory=dict)
    _ssids: Set[str] = field(default_factory=set)       # whole SSIDs, kept as strong seeds
    _vendors: Set[str] = field(default_factory=set)

    def _add(self, token: str, source: str) -> None:
        t = (token or "").strip().lower()
        if len(t) >= _MIN and t not in _STOP:
            self._tokens.setdefault(t, set()).add(source)

    def _add_text(self, text: str, source: str) -> None:
        for tok in _tokenize(text):
            self._add(tok, source)

    # ---- sources (each one glass-box-tagged) --------------------------------
    def add_ssid(self, ssid: str) -> None:
        """An SSID on the air — keep the whole name as a seed AND its word tokens."""
        s = (ssid or "").strip()
        if s:
            self._ssids.add(s)
            self._add_text(s, "ssid")

    def add_pnl(self, ssids: Sequence[str]) -> None:
        """A phone's preferred-network list (probe requests) — networks it has
        joined before, often named after the owner/home."""
        for s in ssids or ():
            if (s or "").strip():
                self._ssids.add(s.strip())
                self._add_text(s, "pnl")

    def add_ble_name(self, name: str) -> None:
        self._add_text(name, "ble")

    def add_hostname(self, host: str) -> None:
        self._add_text(host, "hostname")

    def add_filename(self, name: str) -> None:
        self._add_text(name, "filename")

    def add_file_strings(self, strings: Sequence[str]) -> None:
        """Secret-looking strings a looted file gave up (triage #2)."""
        for s in strings or ():
            self._add_text(s, "loot")

    def add_vendor(self, bssid: str) -> None:
        """AP vendor from the OUI — a word worth trying, not a default-key claim."""
        try:
            from ..geo.oui import lookup_oui
            v = lookup_oui(bssid) if bssid else None
        except Exception:
            v = None
        if v:
            self._vendors.add(v)
            self._add_text(v, "vendor")

    # ---- bulk convenience over existing data structures ---------------------
    def learn_access_points(self, access_points: Sequence) -> None:
        """Fold in the beacon inventory (CaptureTap.access_points())."""
        for ap in access_points or ():
            ssid = getattr(ap, "ssid", None) if not isinstance(ap, dict) else ap.get("ssid")
            bssid = getattr(ap, "bssid", None) if not isinstance(ap, dict) else ap.get("bssid")
            if ssid:
                self.add_ssid(ssid)
            if bssid:
                self.add_vendor(bssid)

    def learn_observations(self, observations: Sequence) -> None:
        """Fold in station PNLs (CaptureTap.observations() / DeviceObservation)."""
        for obs in observations or ():
            ssids = getattr(obs, "ssids", None) if not isinstance(obs, dict) else obs.get("ssids")
            if ssids:
                self.add_pnl(list(ssids))

    def learn_triage(self, triage_result: dict) -> None:
        """Fold in looted-file intel: secret-looking strings, metadata, filenames
        (the output of redux.loot.triage over pulled files)."""
        for path, res in (triage_result or {}).items():
            self.add_filename((path or "").rsplit("/", 1)[-1])
            tools = (res or {}).get("tools", {}) if isinstance(res, dict) else {}
            self.add_file_strings(tools.get("strings", {}).get("secret_hits", []))
            meta = tools.get("exiftool", {}).get("metadata", "")
            if meta:
                self._add_text(meta, "loot")

    def learn_hosts(self, hosts: Sequence) -> None:
        """Fold in scanned hostnames (net.recon Host records)."""
        for h in hosts or ():
            name = getattr(h, "hostname", None) if not isinstance(h, dict) else h.get("hostname")
            if name:
                self.add_hostname(name)

    # ---- output -------------------------------------------------------------
    def ranked_tokens(self) -> List[Tuple[str, Tuple[str, ...]]]:
        """Tokens best-first: more distinct sources = stronger seed; then alpha."""
        rows = [(t, tuple(sorted(src))) for t, src in self._tokens.items()]
        rows.sort(key=lambda r: (-len(r[1]), r[0]))
        return rows

    def seeds(self, limit: int = 400) -> List[str]:
        """The ranked token seeds (whole SSIDs first — they mutate best)."""
        out: List[str] = []
        seen: Set[str] = set()
        for s in sorted(self._ssids):
            if s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
        for tok, _src in self.ranked_tokens():
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
            if len(out) >= limit:
                break
        return out

    def grow(self, ssid: str = "", *, bssid: Optional[str] = None, **forge_kw):
        """Feed the environment-grown seeds into the forge → the actual wordlist.
        A specific target SSID/BSSID (if given) leads; the environment seeds the rest."""
        from .forge import forge
        tokens = [t for t in self.seeds() if t.lower() != (ssid or "").lower()]
        return forge(ssid or "", tokens=tokens, bssid=bssid, **forge_kw)

    def summary(self) -> dict:
        by_source: Dict[str, int] = {}
        for src in self._tokens.values():
            for s in src:
                by_source[s] = by_source.get(s, 0) + 1
        return {
            "unique_tokens": len(self._tokens),
            "whole_ssids": len(self._ssids),
            "vendors": len(self._vendors),
            "by_source": by_source,
            "note": (f"{len(self._tokens)} seed(s) grown from this environment across "
                     f"{len(by_source)} source type(s)" if self._tokens
                     else "nothing harvested yet — the box hasn't observed enough"),
        }
