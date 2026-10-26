"""Toolbox — a curated catalog of useful tools and how to add them.

The image ships with a broad working set already baked in (see the image
toolset in `image/stage-redux/00-redux/00-run.sh`). This catalog is the map of
that set plus the easy "A and B are here, wire in C" path for anything not
baked: for each tool it says what it's for, which redux feature it lights up
(only when redux actually drives it — no aspirational wiring), and the **exact**
install command, so adding software is one copy-paste, not a research project.

`list` marks what is actually present on this device (via `shutil.which` on the
tool's real binary) with a ✓, so a new operator can see at a glance what the
image already gives them versus what is a one-command add.

Curated to the useful-and-safe side on purpose: standard SDR / Wi-Fi / crack /
recon / forensics / GPS / Bluetooth / IT tools. It deliberately carries nothing
whose only purpose is to harm — if a tool can't sit on the OK side of that line,
it simply isn't in the catalog.

`install` prints the command by default (installing system packages is the
operator's call and usually needs root); `--run` executes it on the device.
Pure/testable: the catalog and command generation are data; the only impurity is
the optional `which` probe in `is_installed`, which is injected for tests.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

CATEGORIES = ("sdr", "wifi", "crack", "recon", "forensics", "bt", "gps", "ir", "tx", "companion")


@dataclass(frozen=True)
class Tool:
    name: str
    summary: str
    category: str
    install: List[str]            # exact shell command(s) to add it
    enables: str = ""             # the redux feature/command it lights up
    needs_hw: str = ""            # hardware it needs, if any
    note: str = ""                # honest caveat / guidance
    baked: bool = False           # shipped in the stock image toolset
    probe: str = ""               # binary that proves it's installed (defaults to name)

    def to_dict(self) -> dict:
        return {"name": self.name, "summary": self.summary, "category": self.category,
                "install": list(self.install), "enables": self.enables,
                "needs_hw": self.needs_hw, "note": self.note, "baked": self.baked,
                "probe": self.probe}


_APT = "sudo apt-get install -y"


def _apt(pkg: str) -> List[str]:
    return [f"{_APT} {pkg}"]


CATALOG: tuple = (
    # --- SDR receive / decode (work with your RTL-SDR today) ---
    Tool("rtl-sdr", "RTL-SDR drivers + rtl_sdr/rtl_test/rtl_power", "sdr",
         _apt("rtl-sdr"), enables="redux sdr spectrum/waterfall, subghz capture",
         needs_hw="RTL-SDR dongle", baked=True, probe="rtl_power"),
    Tool("rtl-433", "decode ISM devices: weather, TPMS, meters, many keyfobs", "sdr",
         _apt("rtl-433"), enables="redux sdr ingest --rtl433, subghz capture",
         needs_hw="RTL-SDR dongle", baked=True, probe="rtl_433"),
    Tool("multimon-ng", "POCSAG pager decoder (pairs with rtl_fm)", "sdr",
         _apt("multimon-ng"), enables="redux sdr ingest --pocsag, sdr tune pagers",
         baked=True),
    Tool("dump1090", "ADS-B aircraft receiver", "sdr",
         _apt("dump1090-mutability"), enables="redux sdr ingest --adsb",
         needs_hw="RTL-SDR dongle", probe="dump1090-mutability"),
    Tool("acarsdec", "ACARS aircraft-messaging decoder", "sdr",
         ["git clone https://github.com/TLeconte/acarsdec", "cd acarsdec && mkdir build && cd build",
          "cmake .. -Drtl=ON && make && sudo make install"],
         enables="redux sdr ingest --acars", note="builds from source"),
    # --- GPS ---
    Tool("gpsd", "GPS daemon (owns USB/serial/BT GPS; clients read over TCP)", "gps",
         _apt("gpsd gpsd-clients"), enables="redux gps, geo-tagging",
         note="the image sets REDUX_GPSD=1 and runs gpsd; plug in the GPS and go",
         baked=True),
    # --- Wi-Fi capture / survey ---
    Tool("aircrack-ng", "classic Wi-Fi capture/crack suite", "wifi", _apt("aircrack-ng"),
         enables="redux crack (aircrack backend)", baked=True),
    Tool("hcxdumptool", "targeted PMKID / handshake capture straight to pcapng", "wifi",
         _apt("hcxdumptool"), needs_hw="monitor-mode Wi-Fi",
         note="capture here, then `redux crack` converts via hcxtools", baked=True),
    Tool("hcxtools", "convert captures to hashcat 22000 (hcxpcapngtool)", "crack",
         _apt("hcxtools"), enables="capture -> crack conversion", baked=True,
         probe="hcxpcapngtool"),
    Tool("reaver", "WPS PIN / pixie-dust attack (with pixiewps)", "wifi",
         _apt("reaver pixiewps"), enables="redux wps (reaver backend)", baked=True),
    Tool("bully", "alternative WPS attack tool", "wifi", _apt("bully"),
         enables="redux wps --tool bully", baked=True),
    Tool("kismet", "wardriving / wireless survey + logging", "recon", _apt("kismet"),
         enables="redux import (kismetdb)", baked=True),
    Tool("wavemon", "live Wi-Fi signal / link-quality monitor (ncurses)", "wifi",
         _apt("wavemon"), baked=True),
    Tool("horst", "lightweight 802.11 live analyzer", "wifi", _apt("horst"), baked=True),
    Tool("wireless-tools", "legacy iwconfig/iwlist Wi-Fi utilities", "wifi",
         _apt("wireless-tools"), baked=True, probe="iwlist"),
    # --- crack / password ---
    Tool("hashcat", "GPU/CPU hash cracker — the crack pipeline's engine", "crack",
         _apt("hashcat"), enables="redux crack, pipeline audit", baked=True),
    Tool("john", "John the Ripper — CPU hash cracker + rule engine", "crack",
         _apt("john"), baked=True),
    Tool("hydra", "parallel network login brute-forcer", "crack", _apt("hydra"),
         enables="redux netrecon (scope-gated cred-test)", baked=True),
    Tool("medusa", "parallel, modular network login brute-forcer", "crack",
         _apt("medusa"), baked=True),
    Tool("ncrack", "high-speed network auth cracking (nmap project)", "crack",
         _apt("ncrack"), baked=True),
    Tool("crunch", "wordlist generator (patterns / charsets)", "crack", _apt("crunch"),
         baked=True),
    Tool("cewl", "scrape a target site into a custom wordlist", "crack", _apt("cewl"),
         baked=True),
    Tool("hashid", "identify the type of a hash", "crack", _apt("hashid"), baked=True),
    # --- network recon / IT ---
    Tool("nmap", "network/port scanner — the recon pipeline's mapper", "recon",
         _apt("nmap"), enables="redux netrecon (scope-gated scan)", baked=True),
    Tool("masscan", "internet-scale asynchronous TCP port scanner", "recon",
         _apt("masscan"), baked=True),
    Tool("arp-scan", "discover live hosts on the local link via ARP", "recon",
         _apt("arp-scan"), baked=True),
    Tool("netdiscover", "active/passive ARP host discovery", "recon",
         _apt("netdiscover"), baked=True),
    Tool("nbtscan", "scan a range for NetBIOS names", "recon", _apt("nbtscan"),
         baked=True),
    Tool("hping3", "custom TCP/IP packet crafting & probing", "recon", _apt("hping3"),
         baked=True),
    Tool("fping", "fast multi-host ping sweep", "recon", _apt("fping"), baked=True),
    Tool("mtr", "combined traceroute + ping path analysis", "recon", _apt("mtr-tiny"),
         baked=True),
    Tool("dig", "DNS lookup tools (dig/nslookup/host)", "recon",
         _apt("bind9-dnsutils"), baked=True),
    Tool("whois", "domain / IP registration lookup", "recon", _apt("whois"), baked=True),
    Tool("nc", "netcat — the TCP/IP swiss-army knife", "recon",
         _apt("netcat-openbsd"), baked=True),
    Tool("socat", "multipurpose socket relay / forwarder", "recon", _apt("socat"),
         baked=True),
    Tool("ngrep", "grep across live network traffic", "recon", _apt("ngrep"), baked=True),
    Tool("tcpdump", "packet capture / inspection", "recon", _apt("tcpdump"),
         enables="redux capture (parked-channel fallback)", baked=True),
    Tool("tshark", "Wireshark CLI — deep packet analysis", "recon", _apt("tshark"),
         enables="redux capture analysis", baked=True),
    Tool("scapy", "craft / sniff / forge packets from Python", "recon",
         _apt("python3-scapy"), baked=True),
    Tool("impacket", "SMB / MSRPC / Kerberos attack & scripting toolkit", "recon",
         _apt("python3-impacket"), baked=True, probe="impacket-smbclient"),
    Tool("nikto", "web-server vulnerability scanner", "recon", _apt("nikto"), baked=True),
    Tool("whatweb", "fingerprint web technologies behind a site", "recon",
         _apt("whatweb"), baked=True),
    Tool("dirb", "web content / directory brute-forcer", "recon", _apt("dirb"),
         baked=True),
    Tool("wfuzz", "web application fuzzer", "recon", _apt("wfuzz"), baked=True),
    Tool("sqlmap", "automated SQL-injection discovery & exploitation", "recon",
         _apt("sqlmap"), baked=True),
    # --- forensics / carving / stego ---
    Tool("binwalk", "firmware / blob analysis + extraction", "forensics",
         _apt("binwalk"), baked=True),
    Tool("foremost", "recover files by header signature (carving)", "forensics",
         _apt("foremost"), baked=True),
    Tool("exiftool", "read / write media & document metadata", "forensics",
         _apt("libimage-exiftool-perl"), baked=True),
    Tool("steghide", "hide / extract data inside images & audio", "forensics",
         _apt("steghide"), baked=True),
    # --- Bluetooth ---
    Tool("bluez", "Linux Bluetooth stack (bluetoothctl / hcitool)", "bt",
         _apt("bluez"), enables="redux bt (PAN companion dashboard)", baked=True,
         probe="bluetoothctl"),
    Tool("bluez-tools", "extra Bluetooth CLI utilities (bt-adapter, bt-device...)", "bt",
         _apt("bluez-tools"), baked=True, probe="bt-adapter"),
    # --- IR ---
    Tool("lirc", "Linux IR — irsend/irrecord for the IR LED + receiver", "ir",
         _apt("lirc"), enables="redux ir blast/replay", needs_hw="IR LED (+ receiver) on GPIO",
         note="wire the LED to a free GPIO — the 3.5in TFT uses header pins 1-24, so use 25-40",
         probe="irsend"),
    # --- TX backends (unlock sub-GHz / RF replay in the cage) ---
    Tool("hackrf", "HackRF tools (hackrf_transfer) — TX/RX 1 MHz-6 GHz", "tx",
         _apt("hackrf"), enables="redux subghz replay (hackrf backend)",
         needs_hw="HackRF One", note="your Portapack bundle — this is the TX backend",
         baked=True, probe="hackrf_info"),
    Tool("rpitx", "transmit from a Pi GPIO pin (no SDR) ~5 kHz-1500 MHz", "tx",
         ["git clone https://github.com/F5OEO/rpitx", "cd rpitx && ./install.sh"],
         enables="redux subghz replay (rpitx backend)",
         needs_hw="a wire on a free GPIO",
         note="CAGE ONLY — a raw transmitter. TFT uses pins 1-24, so use a pin in 25-40"),
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


def probe_binary(tool: Tool) -> str:
    """The binary whose presence on PATH proves the tool is installed."""
    return tool.probe or tool.name


def is_installed(tool: Tool, *, which: Callable[[str], Optional[str]] = shutil.which) -> bool:
    """True when the tool's real binary is found on PATH. `which` is injected for tests."""
    return which(probe_binary(tool)) is not None


def install_commands(name: str) -> Optional[List[str]]:
    t = get(name)
    return list(t.install) if t else None
