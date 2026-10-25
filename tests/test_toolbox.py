"""Toolbox catalog: curated, safe, one-command installs mapped to redux features."""
from redux.toolbox import (
    CATALOG, CATEGORIES, list_tools, get, find, install_commands, Tool,
)


def test_catalog_is_coherent():
    names = [t.name for t in CATALOG]
    assert len(names) == len(set(names)), "duplicate tool name"
    for t in CATALOG:
        assert t.category in CATEGORIES
        assert t.summary and t.install        # every tool has a purpose + an install command


def test_get_and_find():
    assert get("hackrf").category == "tx"
    assert get("HACKRF").name == "hackrf"      # case-insensitive
    assert get("nope") is None
    assert any(t.name == "rtl-433" for t in find("ism"))
    assert all(t.category == "sdr" for t in find(category="sdr"))


def test_install_commands_are_exact():
    cmds = install_commands("gpsd")
    assert cmds and "apt-get install" in cmds[0] and "gpsd" in cmds[0]
    assert install_commands("nope") is None


def test_tx_backends_present_and_flagged_cage():
    rpitx = get("rpitx")
    assert rpitx.category == "tx" and "CAGE ONLY" in rpitx.note
    # the TFT pin constraint is surfaced where a GPIO tool is wired
    assert "25-40" in rpitx.note
    assert "25-40" in get("lirc").note


def test_companions_marked_as_desktop():
    for name in ("sdrpp", "urh"):
        t = get(name)
        assert t.category == "companion"
        assert "laptop" in t.note.lower() or "desktop" in t.summary.lower() or "pc" in t.note.lower()


def test_catalog_maps_tools_to_features():
    # the SDR decode tools point at the redux command they enable
    assert "pocsag" in get("multimon-ng").enables
    assert "wps" in get("reaver").enables
    assert "crack" in get("hashcat").enables
