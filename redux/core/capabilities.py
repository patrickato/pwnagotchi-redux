"""Capability graph — the layer that lets every part of redux declare what it
*provides* and *requires* against one shared vocabulary, so the system can answer,
in plain language:

    - "why is <feature> unavailable?"          -> resolve()/explain()
    - "what is actually providing <capability>?" -> active_provider()
    - "what breaks if I disable this?"          -> blast_radius()

This is the missing primitive beastagotchi reduced everything to (Signals / Events /
Actions redux already had; Capabilities + Transaction/Recovery it did not). Packs,
detectors, the radio orchestrator, GPS, a plugged SDR, and the firing gate all become
ordinary nodes here: a provider declares `provides`/`requires`, `present` says whether
its hardware/service is actually there *right now*, and `reason` says why — glass-box,
never a bare boolean. Pure logic: no I/O, no hardware, fully unit-testable.

Nothing is "available" because a module was imported — only because a present provider
satisfies its requirements. That is the honest, real-data-only contract applied to
capabilities themselves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple


class Cap(str, Enum):
    """The shared capability vocabulary. Abstract IDs, not module names — a
    consumer requires `LOCATION_POSITION`, not "gps-tagger", so any present
    provider (real GPS, dead-reckoning, self-locate) can satisfy it."""
    RADIO_WIFI_MONITOR = "radio.wifi.monitor"
    RADIO_WIFI_INJECT = "radio.wifi.inject"
    RADIO_WIFI_UPLINK = "radio.wifi.uplink"
    BLE_SCAN = "ble.scan"
    SDR_RX = "sdr.rx"                     # a plugged RTL-SDR etc. — receive only
    LOCATION_POSITION = "location.position"
    NETWORK_INTERNET = "network.internet"
    CAPTURE_HANDSHAKE = "capture.handshake"
    STORAGE_PERSIST = "storage.persist"


class CapState(str, Enum):
    SATISFIED = "satisfied"                       # a present provider covers it
    PROVIDER_PRESENT_NOT_SELECTED = "present_not_selected"  # covered, but by a non-preferred provider
    MISSING = "missing"                           # providers exist, none present
    HARDWARE_ABSENT = "hardware_absent"           # the only providers need absent hardware
    CONFLICT = "conflict"                         # a conflicting provider is also present
    UNKNOWN = "unknown"                           # no provider registered for this capability


@dataclass(frozen=True)
class Provider:
    """One node: a pack, detector, radio role, GPS source, SDR feed, the firing
    gate, etc. `present` is whether it is actually usable right now (hardware/
    service there); `reason` explains that truthfully."""
    name: str
    provides: FrozenSet[str] = frozenset()
    requires: FrozenSet[str] = frozenset()
    requires_any: Tuple[FrozenSet[str], ...] = ()   # satisfied if ANY inner set is fully met
    optional: FrozenSet[str] = frozenset()          # used if present, never blocking
    conflicts: FrozenSet[str] = frozenset()         # capability IDs it cannot coexist with
    group: str = ""                                 # providers in one group are alternates
    present: bool = True
    reason: str = ""

    @staticmethod
    def of(name, provides=(), requires=(), requires_any=(), optional=(),
           conflicts=(), group="", present=True, reason="") -> "Provider":
        def _cap(x) -> str:
            return x.value if isinstance(x, Cap) else str(x)
        return Provider(
            name=name,
            provides=frozenset(_cap(c) for c in provides),
            requires=frozenset(_cap(c) for c in requires),
            requires_any=tuple(frozenset(_cap(c) for c in s) for s in requires_any),
            optional=frozenset(_cap(c) for c in optional),
            conflicts=frozenset(_cap(c) for c in conflicts),
            group=group, present=present, reason=reason,
        )


@dataclass
class CapabilityGraph:
    """Registry + resolver over providers. Deterministic; registration order is
    the preference order when several present providers offer the same capability."""
    _providers: List[Provider] = field(default_factory=list)
    _by_name: Dict[str, Provider] = field(default_factory=dict)

    def register(self, provider: Provider) -> "CapabilityGraph":
        if provider.name in self._by_name:
            raise ValueError(f"duplicate provider name: {provider.name}")
        self._providers.append(provider)
        self._by_name[provider.name] = provider
        return self

    # --- lookups ----------------------------------------------------------- #

    def providers_of(self, cap) -> List[Provider]:
        c = cap.value if isinstance(cap, Cap) else str(cap)
        return [p for p in self._providers if c in p.provides]

    def active_provider(self, cap) -> Optional[Provider]:
        """The present provider that currently satisfies `cap` (first registered
        present one wins), or None if nothing present provides it."""
        for p in self.providers_of(cap):
            if p.present:
                return p
        return None

    def consumers_of(self, cap, *, include_optional: bool = False) -> List[str]:
        """Names of providers that require `cap` (hard), optionally including
        those that merely use it optionally."""
        c = cap.value if isinstance(cap, Cap) else str(cap)
        out: List[str] = []
        for p in self._providers:
            hard = c in p.requires or any(c in s for s in p.requires_any)
            if hard or (include_optional and c in p.optional):
                out.append(p.name)
        return out

    # --- resolution -------------------------------------------------------- #

    def _state_of_cap(self, cap: str) -> Tuple[CapState, str]:
        provs = self.providers_of(cap)
        if not provs:
            return CapState.UNKNOWN, f"no registered provider for '{cap}'"
        present = [p for p in provs if p.present]
        if present:
            return CapState.SATISFIED, f"provided by {present[0].name}"
        # providers exist but none present — distinguish hardware-absent
        reasons = "; ".join(f"{p.name}: {p.reason or 'absent'}" for p in provs)
        hw = all(("hardware" in (p.reason or "").lower()) or
                 ("absent" in (p.reason or "").lower()) for p in provs)
        state = CapState.HARDWARE_ABSENT if hw else CapState.MISSING
        return state, f"no present provider for '{cap}' ({reasons})"

    def resolve(self, name: str) -> Dict[str, Tuple[CapState, str]]:
        """Per-requirement state for one provider, each with a glass-box reason.
        Keys are capability IDs; `requires_any` groups are keyed `any(a|b|c)`."""
        p = self._by_name.get(name)
        if p is None:
            raise KeyError(name)
        out: Dict[str, Tuple[CapState, str]] = {}
        for cap in sorted(p.requires):
            out[cap] = self._state_of_cap(cap)
        for group in p.requires_any:
            key = "any(" + "|".join(sorted(group)) + ")"
            states = [self._state_of_cap(c) for c in sorted(group)]
            if any(s is CapState.SATISFIED for s, _ in states):
                sat = next(c for c, (s, _) in zip(sorted(group), states) if s is CapState.SATISFIED)
                out[key] = (CapState.SATISFIED, f"satisfied by '{sat}'")
            else:
                out[key] = (CapState.MISSING, "none of the alternatives has a present provider")
        for cap in sorted(p.conflicts):
            if self.active_provider(cap) is not None and p.present:
                out[cap] = (CapState.CONFLICT, f"conflicts with present provider of '{cap}'")
        return out

    def is_satisfied(self, name: str) -> bool:
        """True if every hard requirement (and conflict) of `name` is clear."""
        for state, _ in self.resolve(name).values():
            if state not in (CapState.SATISFIED,):
                return False
        return True

    # --- the headline questions ------------------------------------------- #

    def blast_radius(self, cap) -> dict:
        """If the active provider of `cap` were lost, who breaks — and who has a
        present alternate. This is the "what happens if I disable this?" answer."""
        c = cap.value if isinstance(cap, Cap) else str(cap)
        active = self.active_provider(c)
        alternates = [p.name for p in self.providers_of(c)
                      if p.present and (active is None or p.name != active.name)]
        affected = []
        for consumer in self.consumers_of(c):
            affected.append({"consumer": consumer, "has_alternate": bool(alternates)})
        covered = sum(1 for a in affected if a["has_alternate"])
        return {
            "capability": c,
            "active_provider": active.name if active else None,
            "present_alternates": alternates,
            "affected": affected,
            "reason": (
                f"losing '{c}' affects {len(affected)} consumer(s); "
                f"{covered} have a present alternate, {len(affected) - covered} have none"
            ),
        }

    def explain(self, cap) -> dict:
        """Glass-box summary of one capability: who provides it (and why), who
        else could, and who depends on it."""
        c = cap.value if isinstance(cap, Cap) else str(cap)
        active = self.active_provider(c)
        provs = self.providers_of(c)
        return {
            "capability": c,
            "available": active is not None,
            "active_provider": active.name if active else None,
            "reason": (active.reason or f"provided by {active.name}") if active
                      else self._state_of_cap(c)[1],
            "candidates": [
                {"name": p.name, "present": p.present, "reason": p.reason}
                for p in provs
            ],
            "consumers": self.consumers_of(c, include_optional=True),
        }
