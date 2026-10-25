"""MAC OUI → vendor enrichment, randomized-MAC detection, and device classing.

Every MAC the device sees carries a 24-bit vendor prefix (OUI). Turning that
into a human vendor name — and spotting the MACs that are *randomized* (phones
rotate a locally-administered address for privacy) — is cheap, glass-box, and
makes every survey / Dex / sighting far more legible: "a Raspberry Pi and three
Espressif IoT boards" beats eight raw hex strings.

This module ships a curated built-in table of the vendors a recon device
actually runs into (mobiles, IoT, SBCs, network gear, common NIC chipsets). It
is deliberately **not** the full 35k-row IEEE registry — that stays out of the
repo to avoid bloat — but an operator can import the full DB once
(`redux oui import`) and it is merged on top of the built-in table from then on.

Honest by construction:
* a vendor we don't know returns ``None`` (never a guessed name);
* ``device_class`` is explicitly a coarse *guess from the vendor*, surfaced as
  such in the reason, not a claim about the actual device;
* ``randomized`` is a hard fact from the locally-administered bit, not a guess.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

# ---------------------------------------------------------------------------
# Built-in vendor table: lowercase 6-hex prefix -> canonical vendor name.
# Curated for what a Wi-Fi/BLE recon device sees most. The first three entries
# are long-standing test/demo fixtures — keep them.
# ---------------------------------------------------------------------------
_OUI: Dict[str, str] = {
    "001122": "ExampleCorp",     # demo/test fixture
    "aabbcc": "DemoVendor",      # demo/test fixture
    "00e04c": "Realtek",
    # --- Apple (very common; many prefixes) ---
    "f0d1a9": "Apple", "3c22fb": "Apple", "a4d1d2": "Apple", "acbc32": "Apple",
    "040cce": "Apple", "7c6d62": "Apple", "d0817a": "Apple", "f0989d": "Apple",
    "a85c2c": "Apple", "dca904": "Apple", "88665a": "Apple", "cc088d": "Apple",
    # --- Samsung ---
    "dc56e7": "Samsung", "5ccf7f": "Samsung", "001632": "Samsung", "e8508b": "Samsung",
    "fcc734": "Samsung", "bc7937": "Samsung", "8425db": "Samsung",
    # --- Google / Nest ---
    "3c5ab4": "Google", "f4f5d8": "Google", "a47733": "Google", "d8eb97": "Google",
    "1c3de4": "Google", "54604d": "Google",
    # --- Amazon (Echo/Fire/Ring) ---
    "fca667": "Amazon", "44650d": "Amazon", "68dbf9": "Amazon",
    "f0272d": "Amazon", "ac63be": "Amazon",
    # --- Espressif (ESP32/ESP8266 — the IoT workhorse) ---
    "240ac4": "Espressif", "3c6105": "Espressif", "a4cf12": "Espressif",
    "7c9ebd": "Espressif", "ecfabc": "Espressif", "84f3eb": "Espressif",
    "dc4f22": "Espressif", "b4e62d": "Espressif",
    # --- Raspberry Pi ---
    "b827eb": "RaspberryPi", "dca632": "RaspberryPi", "e45f01": "RaspberryPi",
    "28cdc1": "RaspberryPi", "d83add": "RaspberryPi",
    # --- Intel / Realtek / Broadcom / Qualcomm (NIC chipsets) ---
    "00dbdf": "Intel", "7c7a91": "Intel", "a0a8cd": "Intel", "34e12d": "Intel",
    "001018": "Broadcom", "00104b": "Broadcom",
    "0024d7": "Qualcomm", "88123d": "Qualcomm",
    # --- TP-Link ---
    "503eaa": "TP-Link", "a42bb0": "TP-Link", "003192": "TP-Link",
    "60a4b7": "TP-Link", "ac84c6": "TP-Link",
    # --- Netgear ---
    "000fb5": "Netgear", "20e52a": "Netgear", "a040a0": "Netgear", "9c3dcf": "Netgear",
    # --- Cisco / Meraki ---
    "00000c": "Cisco", "001b0c": "Cisco", "e0553d": "Cisco-Meraki", "88151c": "Cisco-Meraki",
    # --- Ubiquiti ---
    "0418d6": "Ubiquiti", "24a43c": "Ubiquiti", "788a20": "Ubiquiti", "fcecda": "Ubiquiti",
    "b4fbe4": "Ubiquiti", "68d79a": "Ubiquiti",
    # --- Aruba / HPE ---
    "000b86": "Aruba", "6cf37f": "Aruba", "943fc2": "Aruba",
    # --- Microsoft (Surface/Xbox) ---
    "000d3a": "Microsoft", "7c1e52": "Microsoft", "582f40": "Microsoft",
    # --- Xiaomi / Huawei / smart-home ---
    "286c07": "Xiaomi", "64b473": "Xiaomi", "f8a45f": "Xiaomi",
    "00e0fc": "Huawei", "48435a": "Huawei", "ac4e91": "Huawei",
    "b0f1ec": "Tuya", "d4a651": "Tuya", "68572d": "Tuya",
    "b0c554": "Shelly", "e868e7": "Shelly",
    "d073d5": "Sonos", "5caf06": "Sonos",
    "b83e59": "Roku", "cc6da0": "Roku", "dc3a5e": "Roku",
    "f0ef86": "GoogleNest", "641666": "GoogleNest",
    "9c8ecd": "Ring",
}
# strip accidental empties produced by the inline cleanups above
_OUI = {k: v for k, v in _OUI.items() if re.fullmatch(r"[0-9a-f]{6}", k)}

# Vendor -> coarse device-class GUESS (surfaced as a guess, never a claim).
_CLASS: Dict[str, str] = {
    "Apple": "apple", "Samsung": "mobile", "Google": "mobile", "Xiaomi": "mobile",
    "Huawei": "mobile", "Microsoft": "computer",
    "Amazon": "smart-home", "GoogleNest": "smart-home", "Ring": "smart-home",
    "Sonos": "smart-home", "Roku": "smart-home",
    "Espressif": "iot", "Tuya": "iot", "Shelly": "iot",
    "RaspberryPi": "sbc",
    "Cisco": "network-infra", "Cisco-Meraki": "network-infra", "Ubiquiti": "network-infra",
    "Aruba": "network-infra", "Netgear": "network-infra", "TP-Link": "network-infra",
    "Intel": "nic/chipset", "Realtek": "nic/chipset", "Broadcom": "nic/chipset",
    "Qualcomm": "nic/chipset",
}

# Where an operator-imported full IEEE DB is read from (normalized TSV), merged
# on TOP of the built-in table.
DEFAULT_DB_PATHS: Tuple[str, ...] = (
    "/captures/config/oui.tsv",
    "/usr/share/redux/oui.tsv",
)


# ---- normalization / back-compat API ------------------------------------ #

def normalize_mac(mac: str) -> str:
    return (mac or "").lower().replace(":", "").replace("-", "").replace(".", "")


def oui_prefix(mac: str) -> str:
    n = normalize_mac(mac)
    return n[:6] if len(n) >= 6 else n


def lookup_oui(mac: str, table: Optional[Dict[str, str]] = None) -> Optional[str]:
    t = table if table is not None else _OUI
    return t.get(oui_prefix(mac))


def label_mac(mac: str, table: Optional[Dict[str, str]] = None) -> str:
    vendor = lookup_oui(mac, table)
    return f"{mac} ({vendor})" if vendor else (mac or "")


# ---- randomized / locally-administered detection ------------------------ #

def _first_octet(mac: str) -> Optional[int]:
    n = normalize_mac(mac)
    if len(n) < 2:
        return None
    try:
        return int(n[:2], 16)
    except ValueError:
        return None


def is_multicast(mac: str) -> bool:
    o = _first_octet(mac)
    return o is not None and bool(o & 0x01)


def is_locally_administered(mac: str) -> bool:
    """True if the locally-administered bit (0x02 of the first octet) is set.

    A globally-unique (manufacturer-burned) MAC has it clear; a randomized /
    privacy MAC has it set. This is a hard fact, not a heuristic."""
    o = _first_octet(mac)
    return o is not None and bool(o & 0x02)


def is_randomized(mac: str, table: Optional[Dict[str, str]] = None) -> bool:
    """Likely a randomized/private MAC: locally-administered AND not matching a
    known vendor OUI (some gear legitimately uses LA addresses)."""
    return is_locally_administered(mac) and lookup_oui(mac, table) is None


# ---- classification ------------------------------------------------------ #

@dataclass(frozen=True)
class Classification:
    mac: str
    vendor: Optional[str]
    device_class: Optional[str]   # a coarse GUESS from the vendor
    randomized: bool
    reason: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def classify(mac: str, table: Optional[Dict[str, str]] = None) -> Classification:
    rnd = is_randomized(mac, table)
    vendor = lookup_oui(mac, table)
    dclass = _CLASS.get(vendor) if vendor else None
    if rnd:
        reason = "randomized/private MAC (locally-administered, no vendor) — likely a phone/privacy address"
    elif vendor:
        reason = f"{vendor}" + (f", likely {dclass} (guess from vendor)" if dclass else "")
    elif is_locally_administered(mac):
        reason = "locally-administered MAC, vendor unknown"
    else:
        reason = "vendor not in table"
    return Classification(mac=mac, vendor=vendor, device_class=dclass,
                          randomized=rnd, reason=reason)


# ---- full-DB import / merge ---------------------------------------------- #

_IEEE_HEX = re.compile(r"^\s*([0-9A-Fa-f]{2}[-:]){2}[0-9A-Fa-f]{2}\s")
_TSV_LINE = re.compile(r"^([0-9a-fA-F]{6})\t(.+)$")


def parse_ieee(text: str) -> Dict[str, str]:
    """Parse an IEEE ``oui.txt`` (``XX-XX-XX   (hex)\\tVendor``), a Wireshark
    ``manuf`` (``XX:XX:XX\\tShort\\tLong``), or our own normalized TSV
    (``xxxxxx\\tVendor``). Returns {prefix(lowercase 6-hex): vendor}."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        m = _TSV_LINE.match(line)
        if m:                                  # our normalized form: "xxxxxx\tVendor"
            out[m.group(1).lower()] = m.group(2).strip()
            continue
        if "(hex)" in line:                    # IEEE oui.txt: "00-1A-2B   (hex)\t\tAcme, Inc."
            head, _, tail = line.partition("(hex)")
            pref = re.sub(r"[^0-9A-Fa-f]", "", head)[:6].lower()
            vendor = tail.strip().strip("\t").strip()
            if len(pref) == 6 and vendor:
                out[pref] = vendor
            continue
        # Wireshark manuf: "00:1A:2B\tAcme\tAcme, Inc." — prefix first, long name last
        if "\t" in line and _IEEE_HEX.match(line):
            parts = [p for p in line.split("\t") if p.strip()]
            if len(parts) >= 2:
                pref = normalize_mac(parts[0])[:6]
                vendor = parts[-1].strip()
                if len(pref) == 6 and vendor:
                    out[pref] = vendor
    return out


def import_ieee(src_path: str, dest_path: str = DEFAULT_DB_PATHS[0]) -> Tuple[int, str]:
    """Parse a full vendor DB and write it as a normalized TSV at ``dest_path``,
    which :func:`full_table` then merges on top of the built-in table. Returns
    (count, reason). Fail closed (nothing written) on an unusable source."""
    try:
        text = Path(src_path).read_text(errors="replace")
    except OSError as e:
        return 0, f"refused: cannot read {src_path}: {e}"
    table = parse_ieee(text)
    if not table:
        return 0, f"refused: no vendor rows parsed from {src_path} (unrecognized format?)"
    dest = Path(dest_path)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text("".join(f"{p}\t{v}\n" for p, v in sorted(table.items())),
                       encoding="utf-8")
        os.replace(tmp, dest)
    except OSError as e:
        return 0, f"refused: cannot write {dest_path}: {e}"
    return len(table), f"imported {len(table)} vendor prefixes -> {dest_path}"


def full_table(*, extra_paths: Iterable[str] = DEFAULT_DB_PATHS,
               extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Built-in table with any on-disk imported DB merged on top (disk wins).
    Missing files are skipped silently (the built-in table still works)."""
    merged = dict(_OUI)
    for p in extra_paths:
        try:
            merged.update(parse_ieee(Path(p).read_text(errors="replace")))
        except OSError:
            continue
    if extra:
        merged.update(extra)
    return merged


def table_stats(table: Optional[Dict[str, str]] = None) -> dict:
    t = table if table is not None else _OUI
    vendors = sorted(set(t.values()))
    return {"prefixes": len(t), "vendors": len(vendors),
            "builtin_prefixes": len(_OUI)}
