"""SDR presets — "pick what you want to listen to, and Augur tunes the radio."

The decoders (`decoders.py`, `ingest.py`, `spectrum.py`) read the *output* of an
SDR tool. This is the other half a new user needs: the **band plan** that knows,
for a plain-language choice, the exact tool + frequency + sample rate + arguments
to run. Pick "Pagers" and this produces the tuned `rtl_fm | multimon-ng`
pipeline; pick "Aircraft" and it produces `dump1090`; pick "Scan a band" and it
produces an `rtl_power` sweep.

So the guided flow is: choose a preset → `build_command()` gives the exact,
glass-box command → the live layer runs it → the matching decoder ingests its
output. No frequency math, no memorized flags.

Honest about the hard part of RF: **frequencies are region- and
deployment-specific** (pager and ACARS channels especially). Every preset whose
frequency depends on where you are carries a `region_note` saying so and how to
override it, rather than pretending one number works everywhere. Pure/testable:
it builds command lists, runs nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


def fmt_hz(hz: float) -> str:
    """Human frequency: 433920000 -> '433.920M'."""
    if hz >= 1e6:
        return f"{hz / 1e6:.3f}".rstrip("0").rstrip(".") + "M"
    if hz >= 1e3:
        return f"{hz / 1e3:.3f}".rstrip("0").rstrip(".") + "k"
    return str(int(hz))


@dataclass(frozen=True)
class TunedCommand:
    stages: List[List[str]]        # pipeline stages (one argv each); >1 == piped
    decoder: str                   # which redux decoder parses the output
    note: str = ""

    def shell(self) -> str:
        return " | ".join(" ".join(stage) for stage in self.stages)


@dataclass(frozen=True)
class SdrPreset:
    name: str                      # slug, e.g. "pagers"
    label: str                     # "Pagers (POCSAG)"
    what: str                      # plain-language: what you'll hear
    tool: str                      # primary tool the preset drives
    decoder: str                   # redux decoder for the output
    freq_hz: Optional[float] = None
    sample_rate: Optional[int] = None
    region_note: str = ""          # honest note when the freq is region-specific
    help: str = ""                 # how-to for a new user

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        if self.freq_hz is not None:
            d["freq"] = fmt_hz(self.freq_hz)
        return d

    # --- the tuned command ------------------------------------------------- #

    def command(self, *, device_index: int = 0, freq_hz: Optional[float] = None,
                duration_s: Optional[int] = None, outfile: str = "sweep.csv",
                scan_range: Optional[str] = None) -> TunedCommand:
        f = freq_hz if freq_hz is not None else self.freq_hz
        d = str(device_index)
        if self.tool == "rtl_433":
            argv = ["rtl_433", "-d", d, "-F", "json"]
            if f:
                argv += ["-f", fmt_hz(f)]
            return TunedCommand([argv], "rtl433", "ISM devices (weather/TPMS/meters/remotes)")
        if self.tool == "dump1090":
            return TunedCommand([["dump1090", "--device-index", d, "--net", "--write-json", "/tmp/adsb"]],
                                "adsb", "ADS-B is fixed at 1090 MHz")
        if self.tool == "multimon-ng":          # pagers: rtl_fm | multimon-ng
            sr = self.sample_rate or 22050
            front = ["rtl_fm", "-d", d, "-f", fmt_hz(f or 929.6e6), "-s", str(sr), "-g", "40", "-"]
            back = ["multimon-ng", "-t", "raw", "-a", "POCSAG512", "-a", "POCSAG1200",
                    "-a", "POCSAG2400", "-f", "alpha", "-"]
            return TunedCommand([front, back], "pocsag", self.region_note)
        if self.tool == "acarsdec":
            return TunedCommand([["acarsdec", "-o", "4", "-r", d, fmt_hz(f or 131.55e6).rstrip("M")]],
                                "acars", self.region_note)
        if self.tool == "rtl_power":
            rng = scan_range or "430M:440M:25k"
            argv = ["rtl_power", "-d", d, "-f", rng, "-i", "1"]
            if duration_s:
                argv += ["-e", f"{duration_s}s"]
            else:
                argv += ["-1"]                   # single sweep
            argv += [outfile]
            return TunedCommand([argv], "spectrum", f"sweeping {rng}")
        raise ValueError(f"preset {self.name!r} has no command builder for tool {self.tool!r}")


# ---- the catalog --------------------------------------------------------- #

PRESETS: tuple = (
    SdrPreset(
        name="ism", label="Home/ISM sensors", tool="rtl_433", decoder="rtl433",
        freq_hz=433.92e6,
        what="weather stations, thermometers, doorbells, meters, car key fobs",
        region_note="433.92 MHz in most of the world; rtl_433 also hops common ISM bands",
        help="The easiest place to start. Plug the SDR, pick this, and watch the Field "
             "Dex fill with nearby sensors. No antenna tuning needed for strong local devices."),
    SdrPreset(
        name="tpms", label="Tire sensors (TPMS)", tool="rtl_433", decoder="rtl433",
        freq_hz=315e6,
        what="the pressure sensors inside passing cars' tires",
        region_note="315 MHz in North America, 433.92 MHz in most of Europe/Asia — "
                    "override --freq 433.92M outside NA",
        help="Stand near a road. Each car broadcasts its TPMS sensor IDs; they appear as "
             "ISM sightings. Great for seeing re-visits of the same vehicle."),
    SdrPreset(
        name="aircraft", label="Aircraft (ADS-B)", tool="dump1090", decoder="adsb",
        freq_hz=1090e6,
        what="airliners and small planes broadcasting their position",
        region_note="ADS-B is fixed worldwide at 1090 MHz",
        help="Point a 1090 MHz antenna skyward (even the stock antenna works for close "
             "traffic). Aircraft report their OWN GPS, so they map themselves."),
    SdrPreset(
        name="pagers", label="Pagers (POCSAG)", tool="multimon-ng", decoder="pocsag",
        freq_hz=929.6e6, sample_rate=22050,
        what="POCSAG pager transmissions (capcodes logged; message bodies are NOT stored)",
        region_note="Pager frequencies are VERY region-specific: ~929–932 MHz (US), "
                    "~153 MHz (UK), ~148–149 MHz (parts of EU). Find yours and override --freq.",
        help="You MUST set the right frequency for your area (see region note). Only the "
             "pager capcode (device id) is recorded — message content is never stored."),
    SdrPreset(
        name="acars", label="Aircraft messaging (ACARS)", tool="acarsdec", decoder="acars",
        freq_hz=131.55e6,
        what="short data messages between aircraft and ground (aircraft tails logged)",
        region_note="131.550 MHz is the common primary; 130.025 / 131.725 are also used by region",
        help="Needs acarsdec. Only the aircraft tail/flight is recorded, not message text."),
    SdrPreset(
        name="scan", label="Scan a band (what's on the air)", tool="rtl_power", decoder="spectrum",
        freq_hz=None,
        what="a power sweep of a frequency range to find active signals",
        region_note="give a range as START:STOP:STEP, e.g. 430M:440M:25k",
        help="Don't know what to listen to? Scan a band first. `redux sdr spectrum` then "
             "shows the active peaks (strongest first) so you know where to point a decoder."),
)

_BY_NAME = {p.name: p for p in PRESETS}


def list_presets() -> List[SdrPreset]:
    return list(PRESETS)


def find_preset(name: str) -> Optional[SdrPreset]:
    return _BY_NAME.get((name or "").strip().lower())
