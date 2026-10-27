"""Default-credential pack keyed to device type — close the identify→act loop.

Once a device is identified ("TP-Link router", "Hikvision camera", "a printer"),
the single most effective first move on its web/admin service is the set of
well-known factory defaults for that kind of gear. This is that lookup: a small
knowledge base of widely-published default credentials keyed by category and
vendor, so identify()/discover() output feeds straight into the scope-gated
`loot pivot` as the credentials to try first — no manual lookup.

These are standard, published default-credential dictionaries (the same class of
data as a wordlist), for use only against targets in your Scope. The pack is just
a smarter password list; the firing, gating and authorization are the pivot's.
Pure/deterministic.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

Pair = Tuple[str, str]      # (username, password)  — "" password means blank

# generic factory defaults seen across most gear, tried for anything.
_COMMON: Tuple[Pair, ...] = (
    ("admin", "admin"), ("admin", "password"), ("admin", ""), ("admin", "1234"),
    ("admin", "12345"), ("root", "root"), ("root", ""), ("user", "user"),
    ("admin", "admin123"), ("administrator", "administrator"),
)

# category → published defaults for that kind of device (most-likely first).
_BY_CATEGORY: dict = {
    "router": (("admin", "admin"), ("admin", "password"), ("admin", ""),
               ("admin", "1234"), ("user", "user"), ("root", "admin")),
    "camera": (("admin", "admin"), ("admin", ""), ("admin", "12345"),
               ("admin", "888888"), ("root", "root"), ("admin", "admin12345"),
               ("admin", "123456")),
    "doorbell": (("admin", "admin"), ("admin", "")),
    "printer": (("admin", ""), ("admin", "admin"), ("admin", "0000"), ("admin", "access")),
    "thermostat": (("admin", "admin"), ("admin", "")),
    "iot": (("admin", "admin"), ("admin", ""), ("root", "root")),
    "plug": (("admin", "admin"), ("admin", "")),
    "light": (("admin", "admin"),),
    "computer": (("administrator", ""), ("admin", "admin")),
    "streamer": (),          # usually no local login
    "voice-assistant": (),
    "speaker": (),
}

# vendor substring → defaults specific to that maker (refines the category set).
_BY_VENDOR: Tuple[Tuple[str, Tuple[Pair, ...]], ...] = (
    ("tp-link", (("admin", "admin"),)),
    ("tplink", (("admin", "admin"),)),
    ("netgear", (("admin", "password"),)),
    ("d-link", (("admin", ""), ("admin", "admin"))),
    ("dlink", (("admin", ""), ("admin", "admin"))),
    ("linksys", (("admin", "admin"), ("", "admin"))),
    ("ubiquiti", (("ubnt", "ubnt"),)),
    ("hikvision", (("admin", "12345"),)),
    ("dahua", (("admin", "admin"),)),
    ("axis", (("root", "pass"),)),
    ("zyxel", (("admin", "1234"),)),
    ("asus", (("admin", "admin"),)),
    ("synology", (("admin", ""),)),
    ("qnap", (("admin", "admin"),)),
)


def _dedupe(pairs) -> List[Pair]:
    out: List[Pair] = []
    seen = set()
    for p in pairs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def creds_for(*, category: str = "", vendor: str = "", product: str = "",
              include_common: bool = True) -> List[Pair]:
    """The default (user, pass) pairs to try for a device, most-likely first:
    vendor-specific, then category, then the generic common set."""
    pairs: List[Pair] = []
    vlow = f"{vendor} {product}".lower()
    for sub, vp in _BY_VENDOR:
        if sub in vlow:
            pairs.extend(vp)
    pairs.extend(_BY_CATEGORY.get((category or "").lower(), ()))
    if include_common:
        pairs.extend(_COMMON)
    return _dedupe(pairs)


def users_and_passwords(pairs: List[Pair]) -> Tuple[List[str], List[str]]:
    """Split pairs into the distinct username + password lists a sprayer wants."""
    users, passwords = [], []
    for u, p in pairs:
        if u not in users:
            users.append(u)
        if p not in passwords:
            passwords.append(p)
    return users, passwords


def creds_for_identity(identity: dict, *, include_common: bool = True) -> List[Pair]:
    """Convenience: take a device_id/identify() result dict → default pairs."""
    identity = identity or {}
    return creds_for(category=identity.get("category", ""),
                     vendor=identity.get("vendor", ""),
                     product=identity.get("product", ""),
                     include_common=include_common)
