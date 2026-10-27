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

# generic factory defaults seen across most gear, tried for anything. A real run
# works through the whole list, most-likely first.
_COMMON: Tuple[Pair, ...] = (
    ("admin", "admin"), ("admin", "password"), ("admin", ""), ("admin", "1234"),
    ("admin", "12345"), ("admin", "123456"), ("admin", "admin123"),
    ("admin", "pass"), ("admin", "default"), ("administrator", "administrator"),
    ("administrator", "password"), ("administrator", ""), ("root", "root"),
    ("root", ""), ("root", "admin"), ("root", "password"), ("root", "toor"),
    ("root", "1234"), ("user", "user"), ("user", "password"), ("guest", "guest"),
    ("guest", ""), ("support", "support"), ("admin", "changeme"), ("admin", "letmein"),
)

# category → published defaults for that kind of device (most-likely first). These
# are the standard lists a tool like this works through against a scope target.
_BY_CATEGORY: dict = {
    "router": (
        ("admin", "admin"), ("admin", "password"), ("admin", ""), ("admin", "1234"),
        ("admin", "motorola"), ("admin", "5up"), ("user", "user"), ("root", "admin"),
        ("admin", "epicrouter"), ("admin", "sky"), ("admin", "virginmedia"),
        ("admin", "changeme"), ("", "admin"), ("cusadmin", "highspeed"),
    ),
    "camera": (
        ("admin", "admin"), ("admin", ""), ("admin", "12345"), ("admin", "123456"),
        ("admin", "888888"), ("admin", "9999"), ("admin", "admin12345"),
        ("admin", "4321"), ("admin", "camera"), ("admin", "ipcam"), ("root", "root"),
        ("root", "pass"), ("root", "admin"), ("root", "12345"), ("root", "vizxv"),
        ("root", "888888"), ("root", "hi3518"), ("root", "xc3511"), ("root", "7ujMko0admin"),
        ("supervisor", "supervisor"), ("Admin", "1234"), ("service", "service"),
    ),
    "doorbell": (
        ("admin", "admin"), ("admin", ""), ("admin", "12345"), ("admin", "123456"),
        ("admin", "888888"), ("root", "root"), ("admin", "1234"), ("admin", "admin123"),
    ),
    "printer": (
        ("admin", ""), ("admin", "admin"), ("admin", "0000"), ("admin", "access"),
        ("admin", "1234"), ("admin", "password"), ("admin", "myadmin"),
        ("admin", "123456"), ("", "admin"),
    ),
    "nas": (
        ("admin", "admin"), ("admin", ""), ("admin", "password"), ("admin", "1234"),
        ("root", "root"), ("admin", "synology"), ("admin", "nasadmin"),
    ),
    "thermostat": (("admin", "admin"), ("admin", ""), ("admin", "1234")),
    "iot": (
        ("admin", "admin"), ("admin", ""), ("admin", "12345"), ("root", "root"),
        ("admin", "1234"), ("admin", "password"), ("admin", "0000"), ("user", "user"),
    ),
    "plug": (("admin", "admin"), ("admin", ""), ("admin", "1234")),
    "light": (("admin", "admin"), ("admin", "")),
    "voice-assistant": (),      # cloud-tied, no local login
    "streamer": (("admin", "admin"),),   # some (Roku web), most none
    "speaker": (),
    "computer": (("administrator", ""), ("admin", "admin"), ("user", "user")),
}

# vendor substring → defaults specific to that maker (refines the category set,
# tried BEFORE the category list). Published factory defaults.
_BY_VENDOR: Tuple[Tuple[str, Tuple[Pair, ...]], ...] = (
    ("tp-link", (("admin", "admin"),)),
    ("tplink", (("admin", "admin"),)),
    ("netgear", (("admin", "password"), ("admin", "1234"))),
    ("d-link", (("admin", ""), ("admin", "admin"), ("user", ""))),
    ("dlink", (("admin", ""), ("admin", "admin"))),
    ("linksys", (("admin", "admin"), ("", "admin"))),
    ("cisco", (("cisco", "cisco"), ("admin", "admin"))),
    ("ubiquiti", (("ubnt", "ubnt"),)),
    ("mikrotik", (("admin", ""),)),
    ("asus", (("admin", "admin"),)),
    ("zyxel", (("admin", "1234"), ("admin", "admin"))),
    ("huawei", (("admin", "admin"), ("telecomadmin", "admintelecom"))),
    ("arris", (("admin", "password"),)),
    ("technicolor", (("admin", "admin"),)),
    ("hikvision", (("admin", "12345"),)),
    ("dahua", (("admin", "admin"), ("888888", "888888"), ("666666", "666666"))),
    ("amcrest", (("admin", "admin"),)),
    ("reolink", (("admin", ""),)),
    ("axis", (("root", "pass"),)),
    ("foscam", (("admin", ""),)),
    # wyze / ring / arlo are cloud+app tied — no useful local web login; their real
    # surface is the Wi-Fi/BLE setup flow (see the device playbook), not a password.
    ("vivotek", (("root", ""),)),
    ("ubnt", (("ubnt", "ubnt"),)),
    ("synology", (("admin", ""),)),
    ("qnap", (("admin", "admin"),)),
    ("western digital", (("admin", "admin"),)),
    ("brother", (("admin", "access"),)),
    ("hp", (("admin", ""),)),
    ("epson", (("admin", "admin"),)),
    ("lexmark", (("admin", "admin"),)),
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
