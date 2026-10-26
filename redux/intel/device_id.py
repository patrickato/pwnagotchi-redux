"""Device identification — "what IS this thing?".

Recon gives scattered signals about a device — a MAC (so an OUI vendor), maybe a
hostname, a BLE local name / company id / service UUIDs, mDNS service types, a
UPnP string, a probe list. On their own each is a hint; fused, they usually name
the actual product: an iRobot Roomba, an Amazon Echo, a Chromecast, an Apple
iPhone, a Ring doorbell, a Wyze cam, a Nest thermostat, a printer, a game console.

This is that fusion: given whatever signals were collected, name the device's
CATEGORY (phone / speaker / voice-assistant / streamer / vacuum / camera /
doorbell / thermostat / printer / light / plug / wearable / gaming / router /
computer / iot …), its VENDOR, and a best-guess PRODUCT, with a confidence and a
glass-box list of which signals said so. Pure/deterministic; knowledge-base driven.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# name keywords (matched against hostname, BLE local name, UPnP/SSDP string) →
# (category, product, confidence). A device that names itself is the best signal.
_NAME_PATTERNS: Tuple[Tuple[str, str, str, str], ...] = (
    (r"roomba", "vacuum", "iRobot Roomba", "high"),
    (r"braava", "vacuum", "iRobot Braava", "high"),
    (r"\bshark(ninja)?\b|\brobovac\b|\beufy\b", "vacuum", "robot vacuum", "medium"),
    (r"echo|alexa|echodot|echo-dot", "voice-assistant", "Amazon Echo", "high"),
    (r"google-?home|nest-?hub|nest-?mini|nest-?audio|google-?nest", "voice-assistant", "Google Nest", "high"),
    (r"homepod", "speaker", "Apple HomePod", "high"),
    (r"sonos", "speaker", "Sonos", "high"),
    (r"bose", "speaker", "Bose speaker", "medium"),
    (r"chromecast", "streamer", "Google Chromecast", "high"),
    (r"apple-?tv|appletv", "streamer", "Apple TV", "high"),
    (r"fire-?tv|firetv|\baftt\b|\bafts\b", "streamer", "Amazon Fire TV", "high"),
    (r"\broku\b", "streamer", "Roku", "high"),
    (r"\bshield\b", "streamer", "NVIDIA Shield", "medium"),
    (r"iphone", "phone", "Apple iPhone", "high"),
    (r"\bipad\b", "tablet", "Apple iPad", "high"),
    (r"macbook", "laptop", "Apple MacBook", "high"),
    (r"\bimac\b|mac-?mini|mac-?studio", "computer", "Apple Mac", "high"),
    (r"pixel", "phone", "Google Pixel", "high"),
    (r"galaxy|sm-[a-z]\d", "phone", "Samsung Galaxy", "high"),
    (r"oneplus", "phone", "OnePlus", "high"),
    (r"\bring\b", "doorbell", "Ring", "high"),
    (r"wyze", "camera", "Wyze Cam", "high"),
    (r"nest-?cam|nestcam|dropcam", "camera", "Google Nest Cam", "high"),
    (r"arlo", "camera", "Arlo camera", "high"),
    (r"reolink|hikvision|dahua|amcrest", "camera", "IP camera", "high"),
    (r"ecobee", "thermostat", "ecobee", "high"),
    (r"thermostat|t-?stat", "thermostat", "thermostat", "medium"),
    (r"\bhue\b|philips-?hue", "light", "Philips Hue", "high"),
    (r"lifx", "light", "LIFX bulb", "high"),
    (r"nanoleaf", "light", "Nanoleaf", "high"),
    (r"bulb|lightstrip|light-?strip", "light", "smart light", "medium"),
    (r"kasa|\bwemo\b|smart-?plug|smartplug", "plug", "smart plug", "medium"),
    (r"printer|\bhp-?[a-z0-9]|epson|canon|brother|\blexmark\b", "printer", "printer", "high"),
    (r"\bxbox\b", "gaming", "Xbox", "high"),
    (r"playstation|\bps[45]\b", "gaming", "PlayStation", "high"),
    (r"nintendo|\bswitch-[0-9a-f]", "gaming", "Nintendo Switch", "medium"),
    (r"fitbit|versa|\bcharge\b|\binspire\b", "wearable", "Fitbit", "high"),
    (r"apple-?watch", "wearable", "Apple Watch", "high"),
    (r"garmin|fenix|forerunner", "wearable", "Garmin", "high"),
    (r"mi-?band|miband|amazfit|\bhuami\b", "wearable", "Xiaomi/Amazfit", "high"),
    (r"\btesla\b|\bmodel-?[3sxy]\b", "vehicle", "Tesla", "high"),
    (r"raspberry|raspberrypi|\brpi\b", "computer", "Raspberry Pi", "high"),
    (r"esp-?32|esp-?8266|esphome|\besp_", "iot", "ESP microcontroller", "medium"),
    (r"shelly|sonoff|tuya|smartlife", "iot", "smart-home device", "medium"),
    (r"android", "phone", "Android device", "medium"),
    (r"\bdesktop-|\blaptop-|\bpc-|win-?10|win-?11", "computer", "Windows PC", "medium"),
)

# mDNS/DNS-SD service types → (category, product, confidence)
_MDNS: Tuple[Tuple[str, str, str, str], ...] = (
    ("_googlecast._tcp", "streamer", "Google Cast device", "high"),
    ("_airplay._tcp", "streamer", "AirPlay device (Apple TV/HomePod)", "high"),
    ("_raop._tcp", "speaker", "AirPlay audio receiver", "high"),
    ("_spotify-connect._tcp", "speaker", "Spotify Connect speaker", "medium"),
    ("_sonos._tcp", "speaker", "Sonos", "high"),
    ("_ipp._tcp", "printer", "printer (IPP)", "high"),
    ("_printer._tcp", "printer", "printer", "high"),
    ("_pdl-datastream._tcp", "printer", "printer", "high"),
    ("_hap._tcp", "iot", "HomeKit accessory", "medium"),
    ("_hue._tcp", "light", "Philips Hue bridge", "high"),
    ("_nanoleafapi._tcp", "light", "Nanoleaf", "high"),
    ("_esphomelib._tcp", "iot", "ESPHome device", "high"),
    ("_amzn-wplay._tcp", "streamer", "Amazon device", "medium"),
    ("_amzn-alexa._tcp", "voice-assistant", "Amazon Echo", "high"),
    ("_googlezone._tcp", "voice-assistant", "Google Nest", "medium"),
    ("_companion-link._tcp", "computer", "Apple device", "low"),
)

# BLE company identifier (hex) → vendor
_BLE_COMPANY = {
    "004c": "Apple", "0006": "Microsoft", "00e0": "Google", "0075": "Samsung",
    "0157": "Huami (Amazfit)", "0059": "Nordic", "00d2": "Dialog", "0171": "Amazon",
    "008a": "Bose", "02e5": "Espressif", "0499": "Ruuvi", "004f": "APT (Nordic)",
    "05a7": "Sonos", "0310": "SGL (Govee)",
}

# OUI vendor substring → (category, product) default when nothing stronger fires
_VENDOR_CATEGORY: Tuple[Tuple[str, str, str], ...] = (
    ("irobot", "vacuum", "iRobot Roomba"),
    ("sonos", "speaker", "Sonos"),
    ("roku", "streamer", "Roku"),
    ("ring", "doorbell", "Ring"),
    ("ecobee", "thermostat", "ecobee"),
    ("nest", "iot", "Google Nest device"),
    ("amazon", "iot", "Amazon device"),
    ("google", "iot", "Google device"),
    ("philips", "light", "Philips (Hue?)"),
    ("signify", "light", "Philips Hue"),
    ("lifx", "light", "LIFX"),
    ("wyze", "camera", "Wyze"),
    ("arlo", "camera", "Arlo"),
    ("hikvision", "camera", "Hikvision camera"),
    ("tp-link", "router", "TP-Link"),
    ("tplink", "router", "TP-Link"),
    ("ubiquiti", "router", "Ubiquiti"),
    ("netgear", "router", "Netgear"),
    ("espressif", "iot", "ESP device"),
    ("tuya", "iot", "Tuya smart device"),
    ("raspberry", "computer", "Raspberry Pi"),
    ("apple", "phone", "Apple device"),
    ("samsung", "phone", "Samsung device"),
    ("microsoft", "computer", "Microsoft device"),
    ("intel", "computer", "PC/laptop"),
    ("realtek", "computer", "PC/laptop"),
    ("liteon", "computer", "PC/laptop"),
    ("nintendo", "gaming", "Nintendo"),
    ("sony interactive", "gaming", "PlayStation"),
    ("garmin", "wearable", "Garmin"),
    ("fitbit", "wearable", "Fitbit"),
)

_CONF = {"high": 0, "medium": 1, "low": 2}

# What you can do WITH / TO a device once you know what it is — the attack surface
# per category, pointed at the project's own tools (authorized / LAB use). These are
# surface categories + where to start, not step-by-step exploits.
# Every networked device also gets the generic on-LAN block below.
_ON_LAN: Tuple[Tuple[str, str], ...] = (
    ("map its open ports/services", "redux netrecon --subnet <cidr>"),
    ("spray recovered creds at it / reuse", "redux loot pivot ; redux loot reuse"),
    ("pull files if it shares any (SMB)", "redux loot files <ip> --user <u> --pw <p>"),
    ("grab NetNTLM if it does name resolution", "redux responder <iface> harvest"),
    ("profile it over time", "redux dossier <bssid> ; redux dex --dna"),
)
_PLAYBOOK: dict = {
    "voice-assistant": (
        ("enumerate its local API / mDNS services", "mDNS + redux netrecon"),
        ("check the companion-app BLE setup/pairing mode", "BLE scan"),
        ("default-cred check on any local admin", "redux loot pivot (known defaults)"),
        ("traffic-volume analysis (is someone talking to it?)", "airwave DVR / capture"),
    ),
    "vacuum": (
        ("probe the local HTTP/MQTT control API", "redux netrecon"),
        ("try default / weak app credentials", "redux loot pivot"),
        ("loot the stored home map + firmware", "redux loot files"),
    ),
    "camera": (
        ("find RTSP / ONVIF stream + web UI", "redux netrecon (554/80/8000)"),
        ("default-admin credential check", "redux loot pivot"),
        ("note model+firmware for a known-issue check", "redux dossier"),
    ),
    "doorbell": (
        ("find its local web/API + stream", "redux netrecon"),
        ("default-cred check", "redux loot pivot"),
    ),
    "printer": (
        ("IPP / raw-9100 / PJL enumeration", "redux netrecon (631/9100)"),
        ("pull queued / stored documents + address book", "redux loot files"),
        ("default admin on the web UI", "redux loot pivot"),
    ),
    "router": (
        ("admin panel default-cred / brute", "redux loot pivot"),
        ("WPS pixie-dust", "redux wps"),
        ("UPnP port-forward + config-backup loot", "redux netrecon"),
    ),
    "light": (("local on-LAN control API (often unauth)", "redux netrecon"),
              ("pairing mode / firmware", "BLE or local API")),
    "plug": (("local on-LAN control API (often unauth)", "redux netrecon"),),
    "thermostat": (("local/cloud API", "redux netrecon"),
                   ("schedule/occupancy data = presence intel", "redux loot files")),
    "streamer": (("DIAL / cast control (unauth casting)", "redux netrecon (8008/8009)"),
                 ("ADB if enabled (Fire/Android TV)", "redux netrecon (5555)")),
    "speaker": (("AirPlay/Spotify/Sonos local control API", "redux netrecon"),),
    "phone": (("on-LAN service scan (usually hardened)", "redux netrecon"),
              ("harvest its probe-request PNL", "redux captap / redux dex")),
    "tablet": (("on-LAN service scan", "redux netrecon"),),
    "computer": (("full service scan", "redux netrecon"),
                 ("SMB file-loot + NetNTLM", "redux loot files ; redux responder"),
                 ("credential reuse", "redux loot reuse")),
    "laptop": (("full service scan + SMB file-loot", "redux netrecon ; redux loot files"),),
    "wearable": (("BLE GATT service enumeration + pairing", "BLE scan"),),
    "gaming": (("service scan (mostly closed)", "redux netrecon"),),
    "vehicle": (("its Wi-Fi/BLE service surface", "redux netrecon ; BLE scan"),),
    "iot": (("local API / default-cred surface", "redux netrecon ; redux loot pivot"),
            ("firmware + pairing mode", "BLE or local API")),
}
# categories that live on the LAN (so the generic on-LAN block applies)
_NETWORKED = set(_PLAYBOOK) - {"wearable"}


def actions_for(category: str) -> List[dict]:
    """What you can do with/to a device of this category — category-specific options
    first, then the generic on-LAN surface for anything networked."""
    out: List[dict] = []
    for do, via in _PLAYBOOK.get(category, ()):  # category-specific
        out.append({"do": do, "via": via})
    if category in _NETWORKED or category == "unknown":
        for do, via in _ON_LAN:
            out.append({"do": do, "via": via})
    return out


@dataclass(frozen=True)
class DeviceSignals:
    mac: str = ""
    vendor: str = ""                 # OUI vendor (if already known); else identify() looks it up
    hostname: str = ""
    ble_name: str = ""
    ble_company_id: str = ""
    ble_service_uuids: tuple = ()
    mdns_services: tuple = ()
    ssdp: str = ""
    probe_ssids: tuple = ()


@dataclass(frozen=True)
class DeviceGuess:
    category: str
    vendor: str
    product: str
    confidence: str
    reasons: tuple
    actions: tuple = ()        # what you can do with/to it, given its category

    def to_dict(self) -> dict:
        return {"category": self.category, "vendor": self.vendor, "product": self.product,
                "confidence": self.confidence, "reasons": list(self.reasons),
                "actions": [dict(a) for a in self.actions]}


def _match_names(text: str) -> Optional[Tuple[str, str, str, str]]:
    low = (text or "").lower()
    for pat, cat, prod, conf in _NAME_PATTERNS:
        if re.search(pat, low):
            return (pat, cat, prod, conf)
    return None


def identify(signals: DeviceSignals) -> DeviceGuess:
    """Fuse the collected signals into a best-guess device identity (glass-box)."""
    reasons: List[str] = []
    candidates: List[Tuple[str, str, str]] = []   # (confidence, category, product)

    vendor = signals.vendor
    if not vendor and signals.mac:
        try:
            from ..geo.oui import lookup_oui
            vendor = lookup_oui(signals.mac) or ""
        except Exception:
            vendor = ""
    if not vendor and signals.ble_company_id:
        vendor = _BLE_COMPANY.get(signals.ble_company_id.lower().replace("0x", "").zfill(4), vendor)

    # 1) explicit name in hostname / BLE name / SSDP (strongest)
    for label, text in (("hostname", signals.hostname), ("BLE name", signals.ble_name),
                        ("UPnP", signals.ssdp)):
        hit = _match_names(text)
        if hit:
            _pat, cat, prod, conf = hit
            candidates.append((conf, cat, prod))
            reasons.append(f"{label} '{text}' → {prod}")

    # 2) mDNS service types
    for svc in signals.mdns_services or ():
        s = (svc or "").lower()
        for key, cat, prod, conf in _MDNS:
            if key in s:
                candidates.append((conf, cat, prod))
                reasons.append(f"mDNS {key} → {prod}")

    # 3) BLE company id → vendor (category comes from a name hit if any)
    if signals.ble_company_id and vendor:
        reasons.append(f"BLE company {signals.ble_company_id} → {vendor}")

    # 4) OUI vendor category default (weaker — a maker, not always the product)
    if vendor:
        vlow = vendor.lower()
        for sub, cat, prod in _VENDOR_CATEGORY:
            if sub in vlow:
                candidates.append(("low", cat, prod))
                reasons.append(f"OUI vendor '{vendor}' → typically {prod}")
                break

    if not candidates:
        return DeviceGuess(category="unknown", vendor=vendor or "unknown",
                           product=vendor or "unidentified device",
                           confidence="low",
                           reasons=tuple(reasons) or ("no identifying signals",),
                           actions=tuple(actions_for("unknown")))

    candidates.sort(key=lambda c: _CONF.get(c[0], 9))
    conf, category, product = candidates[0]
    # agreement across independent signals lifts confidence
    cats = {c[1] for c in candidates}
    if len(candidates) > 1 and len(cats) == 1 and conf != "high":
        conf = "high" if conf == "medium" else conf
        reasons.append("multiple signals agree")
    return DeviceGuess(category=category, vendor=vendor or "unknown", product=product,
                       confidence=conf, reasons=tuple(reasons),
                       actions=tuple(actions_for(category)))
