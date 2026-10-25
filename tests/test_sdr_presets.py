"""SDR presets: plain-language choice -> exact tuned command, glass-box + honest."""
from redux.sdr.presets import (
    PRESETS, list_presets, find_preset, fmt_hz, SdrPreset,
)


def test_fmt_hz():
    assert fmt_hz(433_920_000) == "433.92M"
    assert fmt_hz(1_090_000_000) == "1090M"
    assert fmt_hz(25_000) == "25k"


def test_catalog_is_coherent():
    names = [p.name for p in PRESETS]
    assert len(names) == len(set(names))
    # every preset covers one of our decoders and carries help text
    decoders = {"rtl433", "adsb", "pocsag", "acars", "spectrum"}
    for p in PRESETS:
        assert p.decoder in decoders
        assert p.help and p.what
    # the ones with region-dependent freqs are honest about it
    assert find_preset("pagers").region_note
    assert find_preset("tpms").region_note


def test_find_preset():
    assert find_preset("aircraft").tool == "dump1090"
    assert find_preset("AIRCRAFT").name == "aircraft"     # case-insensitive
    assert find_preset("nope") is None


def test_ism_command():
    cmd = find_preset("ism").command(device_index=0)
    assert cmd.decoder == "rtl433"
    assert cmd.stages[0][0] == "rtl_433" and "-F" in cmd.stages[0] and "json" in cmd.stages[0]
    assert "433.92M" in cmd.stages[0]


def test_aircraft_command_is_fixed_1090():
    cmd = find_preset("aircraft").command(device_index=1)
    assert cmd.stages[0][0] == "dump1090"
    assert "--device-index" in cmd.stages[0] and "1" in cmd.stages[0]
    assert cmd.decoder == "adsb"


def test_pagers_command_is_a_tuned_pipeline():
    cmd = find_preset("pagers").command(device_index=0)
    assert len(cmd.stages) == 2                           # rtl_fm | multimon-ng
    assert cmd.stages[0][0] == "rtl_fm" and cmd.stages[1][0] == "multimon-ng"
    assert "POCSAG1200" in cmd.stages[1]
    assert "|" in cmd.shell()                             # shows as a pipe
    assert cmd.decoder == "pocsag"


def test_freq_override_applies():
    # TPMS defaults to 315M (NA); override to EU 433.92M
    cmd = find_preset("tpms").command(freq_hz=433.92e6)
    assert "433.92M" in cmd.stages[0]


def test_scan_builds_rtl_power_sweep():
    cmd = find_preset("scan").command(scan_range="430M:440M:25k")
    assert cmd.stages[0][0] == "rtl_power" and "430M:440M:25k" in cmd.stages[0]
    assert "-1" in cmd.stages[0]                          # single sweep by default
    assert cmd.decoder == "spectrum"


def test_scan_with_duration_uses_e_not_single():
    cmd = find_preset("scan").command(scan_range="88M:108M:100k", duration_s=30)
    assert "-e" in cmd.stages[0] and "30s" in cmd.stages[0]
    assert "-1" not in cmd.stages[0]


def test_acars_command():
    cmd = find_preset("acars").command(device_index=0)
    assert cmd.stages[0][0] == "acarsdec" and cmd.decoder == "acars"
