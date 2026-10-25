"""Toolbox — a curated catalog of useful tools and how to add them.

The image ships lean. When you later want a capability that needs an outside
program (decode a new band, add a TX backend, crack a format), this is the
"A and B are here, wire in C easily" path: a curated catalog that, for each
tool, says what it's for, which redux feature it lights up, and the **exact**
install command — so adding software is one copy-paste, not a research project.

Curated to the useful-and-safe side on purpose: these are standard SDR / Wi-Fi /
GPS / IT tools. It deliberately carries nothing whose only purpose is to harm —
if a tool can't sit on the OK side of that line, it simply isn't in the catalog.

`install` prints the command by default (installing system packages is the
operator's call and usually needs root); `--run` executes it on the device.
Pure/testable: the catalog and command generation are data; nothing is installed
here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

CATEGORIES = ("sdr", "wifi", "crack", "gps", "ir", "tx", "recon", "companion")


@dataclass(frozen=True)
class Tool:
    name: str
    summary: str
    category: str
    install: List[str]            # exact shell command(s) to add it
    enables: str = ""             # the redux feature/command it lights up
    needs_hw: str = ""            # hardware it needs, if any
    note: str = ""                # honest caveat / guidance

    def to_dict(self) -> dict:
        return {"name": self.name, "summary": self.summary, "category": self.category,
                "install": list(self.install), "enables": self.enables,
                "needs_hw": self.needs_hw, "note": self.note}


_APT = "sudo apt-get install -y"

CATALOG: tuple = (
    # --- SDR receive / decode (work with your RTL-SDR today) ---
    Tool("rtl-sdr", "RTL-SDR drivers + rtl_sdr/rtl_test/rtl_power", "sdr",
         [f"{_APT} rtl-sdr"], enables="redux sdr spectrum/waterfall, subghz capture",
         needs_hw="RTL-SDR dongle"),
    Tool("rtl-433", "decode ISM devices: weather, TPMS, meters, many keyfobs", "sdr",
         [f"{_APT} rtl-433"], enables="redux sdr ingest --rtl433, subghz capture"),
    Tool("dump1090", "ADS-B aircraft receiver", "sdr",
         [f"{_APT} dump1090-mutability"], enables="redux sdr ingest --adsb"),
    Tool("multimon-ng", "POCSAG pager decoder (pairs with rtl_fm)", "sdr",
         [f"{_APT} multimon-ng"], enables="redux sdr ingest --pocsag, sdr tune pagers"),
    Tool("acarsdec", "ACARS aircraft-messaging decoder", "sdr",
         ["git clone https://github.com/TLeconte/acarsdec", "cd acarsdec && mkdir build && cd build",
          "cmake .. -Drtl=ON && make && sudo make install"],
         enables="redux sdr ingest --acars", note="builds from source"),
    # --- GPS ---
    Tool("gpsd", "GPS daemon (owns USB/serial/BT GPS; clients read over TCP)", "gps",
         [f"{_APT} gpsd gpsd-clients"], enables="redux gps, geo-tagging",
         note="set REDUX_GPSD=1 to use it"),
    # --- Wi-Fi / crack ---
    Tool("aircrack-ng", "classic Wi-Fi capture/crack suite", "wifi", [f"{_APT} aircrack-ng"]),
    Tool("hashcat", "GPU/CPU hash cracker — the crack pipeline's engine", "crack",
         [f"{_APT} hashcat"], enables="redux crack, pipeline audit"),
    Tool("hcxtools", "convert captures to hashcat 22000 (hcxpcapngtool)", "crack",
         [f"{_APT} hcxtools"], enables="capture -> crack conversion"),
    Tool("reaver", "WPS PIN / pixie-dust attack (with pixiewps)", "wifi",
         [f"{_APT} reaver pixiewps"], enables="redux wps (reaver backend)"),
    Tool("bully", "alternative WPS attack tool", "wifi", [f"{_APT} bully"],
         enables="redux wps --tool bully"),
    Tool("kismet", "wardriving / wireless survey + logging", "recon", [f"{_APT} kismet"],
         enables="redux import (kismetdb)"),
    # --- IR ---
    Tool("lirc", "Linux IR — irsend/irrecord for the IR LED + receiver", "ir",
         [f"{_APT} lirc"], enables="redux ir blast/replay", needs_hw="IR LED (+ receiver) on GPIO",
         note="wire the LED to a free GPIO — the 3.5in TFT uses header pins 1-24, so use 25-40"),
    # --- TX backends (unlock sub-GHz / RF replay in the cage) ---
    Tool("hackrf", "HackRF tools (hackrf_transfer) — TX/RX 1 MHz-6 GHz", "tx",
         [f"{_APT} hackrf"], enables="redux subghz replay (hackrf backend)",
         needs_hw="HackRF One", note="your Portapack bundle — this is the TX backend"),
    Tool("rpitx", "transmit from a Pi GPIO pin (no SDR) ~5 kHz-1500 MHz", "tx",
         ["git clone https://github.com/F5OEO/rpitx", "cd rpitx && ./install.sh"],
         enables="redux subghz replay (rpitx backend)",
         needs_hw="a wire on a free GPIO",
         note="CAGE ONLY — a raw transmitter. TFT uses pins 1-24, so use a pin in 25-40"),
    # --- network recon / IT (genuinely useful, non-dangerous) ---
    Tool("nmap", "network/port scanner — map your own lab", "recon", [f"{_APT} nmap"]),
    Tool("tcpdump", "packet capture/inspection", "recon", [f"{_APT} tcpdump"]),
    Tool("tshark", "Wireshark CLI — deep packet analysis", "recon", [f"{_APT} tshark"]),
    # --- laptop companions (GUI; run on your PC, not the headless Pi) ---
    Tool("sdrpp", "SDR++ — interactive waterfall/panadapter (desktop GUI)", "companion",
         ["# install on your laptop from https://www.sdrpp.org"],
         note="GUI tool — run on your laptop for live exploration; Augur is the headless/logged side"),
    Tool("urh", "Universal Radio Hacker — capture/analyze/replay sub-GHz (desktop GUI)", "companion",
         ["pip install urh"],
         note="great for reverse-engineering a sub-GHz signal on your laptop, then catalog it in redux subghz"),
)

_BY_NAME: Dict[str, Tool] = {t.name: t for t in CATALOG}


def list_tools() -> List[Tool]:
    return list(CATALOG)


def get(name: str) -> Optional[Tool]:
    return _BY_NAME.get((name or "").strip().lower())


def find(query: str = "", *, category: Optional[str] = None) -> List[Tool]:
    q = (query or "").strip().lower()
    out = []
    for t in CATALOG:
        if category and t.category != category:
            continue
        if q and q not in f"{t.name} {t.summary} {t.category} {t.enables}".lower():
            continue
        out.append(t)
    return out


def install_commands(name: str) -> Optional[List[str]]:
    t = get(name)
    return list(t.install) if t else None
